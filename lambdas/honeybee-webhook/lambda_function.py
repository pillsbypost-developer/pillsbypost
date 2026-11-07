"""
Honeybee Health Partners API webhook receiver (Nectar mode).

Events (envelope {event_id, patient_id, event_type, ...}):
  RX_RECEIVED      - a prescription arrived; attach it to the patient's open
                     nectar order and (partner_requested workflow) queue the fill
  RX_UPDATED /
  SHIPMENT_UPDATED - tracking available; record it, mark the order SHIPPED
                     (DELIVERED once actual_delivery_date is present)
  ORDER_EXCEPTION  - Honeybee needs a decision; order NECTAR_ON_HOLD + admin email

Security: X-Honeybee-Signature (HMAC over method+url+body, see
common.honeybee_client) and optionally X-Honeybee-Secret. Every event is
deduplicated on event_id (Honeybee redelivers and PATCH /webhooks/{id}
replays). Any exception after dedupe marks the event unprocessed and returns
500 so Honeybee retries.
"""
import base64
import json
import os

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common.logger import CustomLogger
from common.intakeq_client import IntakeQClient
from common.settings import Settings
from common.email_service import EmailService
from common.honeybee_client import HoneybeeClient, HoneybeeError
from common.tracking_service import process_tracking_entry
from common.utils import utc_now_iso, ttl_from_days

# Environment variables (no fallbacks - Fail Fast)
ENV = os.environ['ENV']
ORDERS_TABLE = os.environ['ORDERS_TABLE']
TRACKING_TABLE = os.environ['TRACKING_TABLE']
HONEYBEE_EVENTS_TABLE = os.environ['HONEYBEE_EVENTS_TABLE']
NECTAR_ORDER_QUEUE_URL = os.environ['NECTAR_ORDER_QUEUE_URL']
HONEYBEE_WEBHOOK_SECRET = os.environ['HONEYBEE_WEBHOOK_SECRET']

logger = CustomLogger()
dynamodb = boto3.resource('dynamodb')
orders_table = dynamodb.Table(ORDERS_TABLE)
tracking_table = dynamodb.Table(TRACKING_TABLE)
honeybee_events_table = dynamodb.Table(HONEYBEE_EVENTS_TABLE)
sqs = boto3.client('sqs')
intakeq = IntakeQClient(logger)
settings = Settings(logger)
email_service = EmailService(logger, settings=settings)
honeybee = HoneybeeClient(logger)

EVENT_TTL_DAYS = 90
OPEN_NECTAR_STATUSES = ('NEW', 'SUBMITTED_TO_NECTAR', 'NECTAR_ON_HOLD')


def _webhook_secret():
    """Partner-supplied secret for X-Honeybee-Secret, or None if unset/placeholder."""
    client = boto3.session.Session().client('secretsmanager')
    secret = json.loads(client.get_secret_value(SecretId=HONEYBEE_WEBHOOK_SECRET)['SecretString'])
    value = (secret.get('signing_secret') or '').strip()
    if not value or value.startswith('PLACEHOLDER'):
        return None
    return value


def _header(event, name):
    for key, value in (event.get('headers') or {}).items():
        if key.lower() == name.lower():
            return value
    return None


def _raw_body(event):
    body = event.get('body') or ''
    if event.get('isBase64Encoded'):
        body = base64.b64decode(body).decode('utf-8')
    return body


def _request_url(event):
    ctx = event.get('requestContext') or {}
    domain = ctx.get('domainName')
    path = ctx.get('path') or event.get('path') or ''
    if domain:
        return f"https://{domain}{path}"
    return path


def verify_request(event, raw_body):
    url = _request_url(event)
    signature = _header(event, 'X-Honeybee-Signature')
    if signature and honeybee.verify_signature(signature, 'POST', url, raw_body):
        return True
    secret_header = _header(event, 'X-Honeybee-Secret')
    if secret_header:
        webhook_secret = _webhook_secret()
        if webhook_secret and honeybee.verify_secret_header(secret_header, webhook_secret):
            return True
    return False


# ---- event store -----------------------------------------------------------

def claim_event(event_id, event_type, patient_id, raw_body):
    """Conditional put on event_id; False if already received."""
    try:
        honeybee_events_table.put_item(
            Item={
                'event_id': str(event_id),
                'event_type': event_type,
                'patient_id': str(patient_id or ''),
                'received_at': utc_now_iso(),
                'raw': raw_body,
                'processed': False,
                'ttl': ttl_from_days(EVENT_TTL_DAYS),
            },
            ConditionExpression='attribute_not_exists(event_id)',
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False
        raise


def mark_event(event_id, processed, error=None, order_id=None):
    # 'processed' and 'error' are DynamoDB reserved words
    expr = 'SET #p = :p, processed_at = :now'
    names = {'#p': 'processed'}
    values = {':p': processed, ':now': utc_now_iso()}
    if error:
        expr += ', #e = :e'
        names['#e'] = 'error'
        values[':e'] = str(error)[:1000]
    if order_id:
        expr += ', order_id = :o'
        values[':o'] = order_id
    honeybee_events_table.update_item(
        Key={'event_id': str(event_id)}, UpdateExpression=expr,
        ExpressionAttributeNames=names, ExpressionAttributeValues=values)


# ---- order lookup ----------------------------------------------------------

def orders_for_client(client_id):
    response = orders_table.query(
        IndexName='client-index',
        KeyConditionExpression=Key('client_id').eq(str(client_id)),
        ScanIndexForward=False,
    )
    return response.get('Items', [])


def find_open_nectar_order(client_id, statuses=('NEW',)):
    for order in orders_for_client(client_id):
        if order.get('fulfillment_mode') == 'nectar' and order.get('status') in statuses:
            return order
    return None


def resolve_client_id(patient_id):
    """
    patient_id == IntakeQ ClientId by convention (EHR MRN on the e-Rx). If no
    order exists under that id, fall back to the Honeybee patient's email.
    """
    if find_open_nectar_order(patient_id, OPEN_NECTAR_STATUSES):
        return str(patient_id)
    try:
        patient = honeybee.get_patient(patient_id) or {}
    except HoneybeeError as e:
        logger.log('ERROR', "Honeybee patient lookup failed", patient_id=patient_id, status=e.status_code)
        return None
    email = (patient.get('email') or '').strip()
    if not email:
        return None
    matches = intakeq.search_clients_by_email(email)
    if not matches:
        logger.log('INFO', "No IntakeQ client for Honeybee patient email", patient_id=patient_id)
        return None
    client_id = str(matches[0].get('ClientId'))
    logger.log('INFO', "Resolved Honeybee patient to IntakeQ client by email",
               patient_id=patient_id, client_id=client_id)
    return client_id


def find_order_for_shipment(client_id, order_number):
    orders = orders_for_client(client_id)
    if order_number:
        for order in orders:
            if order.get('honeybee_order_number') == order_number:
                return order
    for order in orders:
        if order.get('fulfillment_mode') == 'nectar' and order.get('status') in OPEN_NECTAR_STATUSES:
            return order
    return None


# ---- handlers --------------------------------------------------------------

def handle_rx_received(payload, patient_id):
    requests_ = payload.get('medication_requests') or []
    prescription_ids = [int(r.get('prescription_id') or r.get('id'))
                        for r in requests_ if (r.get('prescription_id') or r.get('id'))]
    logger.log('INFO', "RX_RECEIVED", patient_id=patient_id, prescription_count=len(prescription_ids))
    logger.log('DEBUG', "RX_RECEIVED medication requests", medication_requests=requests_)

    client_id = resolve_client_id(patient_id)
    order = find_open_nectar_order(client_id) if client_id else None
    if not order:
        logger.log('INFO', "No open nectar order for RX_RECEIVED - prescription parked at Honeybee",
                   patient_id=patient_id)
        return None

    existing = [int(p) for p in order.get('honeybee_prescription_ids') or []]
    merged = existing + [p for p in prescription_ids if p not in existing]
    orders_table.update_item(
        Key={'order_id': order['order_id']},
        UpdateExpression='SET honeybee_patient_id = :pid, honeybee_prescription_ids = :rx, updated_at = :now',
        ExpressionAttributeValues={':pid': str(patient_id), ':rx': merged, ':now': utc_now_iso()},
    )
    logger.log('INFO', "Prescriptions attached to order", order_id=order['order_id'],
               prescription_count=len(merged))

    if settings.honeybee_workflow == 'partner_requested':
        sqs.send_message(QueueUrl=NECTAR_ORDER_QUEUE_URL,
                         MessageBody=json.dumps({'order_id': order['order_id']}))
        logger.log('INFO', "Nectar fill request queued", order_id=order['order_id'])
    return order['order_id']


def _extract_shipment(payload):
    """(shipment, order_number) from the top level or any nested dispense."""
    def has_tracking(s):
        return isinstance(s, dict) and s.get('tracking_no')

    top = payload.get('shipment')
    if has_tracking(top):
        return top, payload.get('order_number')

    dispenses = list(payload.get('medication_dispenses') or [])
    for rx in payload.get('medication_requests') or []:
        dispenses.extend(rx.get('medication_dispenses') or [])
    for dispense in dispenses:
        shipment = dispense.get('shipment')
        if has_tracking(shipment):
            return shipment, dispense.get('order_number') or payload.get('order_number')
    return None, payload.get('order_number')


def handle_shipment(payload, patient_id, event_type):
    shipment, order_number = _extract_shipment(payload)
    if not shipment:
        logger.log('INFO', "No tracking in event", event_type=event_type, patient_id=patient_id)
        return None

    tracking_no = str(shipment['tracking_no']).strip()
    carrier = shipment.get('carrier')
    client_id = resolve_client_id(patient_id)
    order = find_order_for_shipment(client_id, order_number) if client_id else None
    if not order:
        logger.log('INFO', "No order for shipment event", patient_id=patient_id, order_number=order_number)
        return None

    if order.get('status') in ('SHIPPED', 'DELIVERED') and order.get('tracking_number') == tracking_no:
        logger.log('INFO', "Tracking already recorded", order_id=order['order_id'])
    else:
        result = process_tracking_entry(
            order['client_id'], tracking_no, tracking_table, intakeq, logger,
            carrier=carrier, ttl=ttl_from_days(settings.tracking_retention_days),
            orders_table=orders_table,
        )
        logger.log('INFO', "Tracking recorded", order_id=order['order_id'], result=result.get('status'))
        if not intakeq.update_client_tags(order['client_id'], 'SHIPPED', remove=False):
            logger.log('ERROR', "Failed to add SHIPPED tag", client_id=order['client_id'])
        client = intakeq.get_client_by_id(order['client_id'])
        if client:
            email_service.send_order_shipped_email(client, tracking_no, carrier)

    if order_number and not order.get('honeybee_order_number'):
        orders_table.update_item(
            Key={'order_id': order['order_id']},
            UpdateExpression='SET honeybee_order_number = :n, updated_at = :now',
            ExpressionAttributeValues={':n': order_number, ':now': utc_now_iso()},
        )

    if shipment.get('actual_delivery_date'):
        orders_table.update_item(
            Key={'order_id': order['order_id']},
            UpdateExpression='SET #s = :s, delivered_at = :d, updated_at = :now',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'DELIVERED', ':d': shipment['actual_delivery_date'],
                                       ':now': utc_now_iso()},
        )
        logger.log('INFO', "Order DELIVERED", order_id=order['order_id'])
    return order['order_id']


def handle_order_exception(payload, patient_id):
    exception = payload.get('order_exception') or payload.get('exception') or {}
    order_number = payload.get('order_number')
    actions = exception.get('order_actions') or []
    client_id = resolve_client_id(patient_id)
    order = find_order_for_shipment(client_id, order_number) if client_id else None

    if order:
        orders_table.update_item(
            Key={'order_id': order['order_id']},
            UpdateExpression='SET #s = :s, honeybee_exception = :x, updated_at = :now',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={
                ':s': 'NECTAR_ON_HOLD',
                ':x': {'id': exception.get('id'), 'name': exception.get('name'),
                       'message': exception.get('message'),
                       'order_actions': [{'event_key': a.get('event_key'), 'action': a.get('action')}
                                         for a in actions]},
                ':now': utc_now_iso(),
            },
        )
        logger.log('INFO', "Order NECTAR_ON_HOLD", order_id=order['order_id'], order_number=order_number)
    else:
        logger.log('ERROR', "ORDER_EXCEPTION for unknown order", patient_id=patient_id, order_number=order_number)

    action_lines = '\n'.join(f"  - {a.get('event_key')}: {a.get('action')}" for a in actions) or '  (none listed)'
    email_service.send_admin_alert(
        f"Honeybee order exception - {exception.get('name') or 'unknown'}",
        f"Honeybee reported an exception on a Nectar order and needs a decision.\n\n"
        f"Order ID: {order['order_id'] if order else 'unknown'}\n"
        f"Honeybee order number: {order_number}\n"
        f"Patient ID: {patient_id}\n"
        f"Exception: {exception.get('name')} (id {exception.get('id')})\n"
        f"Message: {exception.get('message')}\n\n"
        f"Available actions (event_key: description):\n{action_lines}\n")
    return order['order_id'] if order else None


HANDLERS = {
    'RX_RECEIVED': lambda p, pid, et: handle_rx_received(p, pid),
    'RX_UPDATED': handle_shipment,
    'SHIPMENT_UPDATED': handle_shipment,
    'ORDER_EXCEPTION': lambda p, pid, et: handle_order_exception(p, pid),
}


def lambda_handler(event, context):
    logger.log('INFO', "Received Honeybee webhook")
    raw_body = _raw_body(event)

    if not verify_request(event, raw_body):
        logger.log('ERROR', "Honeybee webhook signature invalid")
        return {'statusCode': 401, 'body': json.dumps('Invalid signature')}

    try:
        payload = json.loads(raw_body)
    except (ValueError, TypeError):
        logger.log('ERROR', "Honeybee webhook body is not JSON")
        return {'statusCode': 400, 'body': json.dumps('Invalid body')}
    logger.log('DEBUG', "Honeybee event", payload=payload)

    event_id = payload.get('event_id')
    event_type = payload.get('event_type') or ''
    patient_id = payload.get('patient_id')
    if not event_id or not event_type:
        logger.log('ERROR', "Honeybee event missing event_id/event_type")
        return {'statusCode': 400, 'body': json.dumps('Missing event_id or event_type')}

    if not claim_event(event_id, event_type, patient_id, raw_body):
        logger.log('INFO', "Duplicate Honeybee event ignored", event_id=event_id, event_type=event_type)
        return {'statusCode': 200, 'body': json.dumps('Duplicate event')}

    logger.log('INFO', "Processing Honeybee event", event_id=event_id, event_type=event_type,
               patient_id=patient_id)
    try:
        handler = HANDLERS.get(event_type)
        order_id = handler(payload, patient_id, event_type) if handler else None
        if not handler:
            logger.log('INFO', "Unhandled Honeybee event type stored", event_type=event_type)
        mark_event(event_id, True, order_id=order_id)
        return {'statusCode': 200, 'body': json.dumps('OK')}
    except Exception as e:
        logger.log('ERROR', "Error processing Honeybee event", event_id=event_id,
                   event_type=event_type, error=str(e))
        mark_event(event_id, False, error=str(e))
        return {'statusCode': 500, 'body': json.dumps('Processing failed')}
