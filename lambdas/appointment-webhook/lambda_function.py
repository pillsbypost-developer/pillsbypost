"""
Appointment webhook - provider approval -> order record.

When the provider reviews and approves a case she creates an "appointment"
in IntakeQ; IntakeQ posts AppointmentCreated here. One order per
appointment (order_id = appointment Id). fulfillment_mode is resolved and
frozen on the record at creation:
  - ServiceId in settings.local_pharmacy_appointment_type_ids -> local_pharmacy
  - else settings.fulfillment_mode (shipping | nectar, the "Nectar mode" toggle)

AppointmentCanceled / AppointmentDeleted -> order CANCELED (and the Honeybee
order canceled when one exists and is still cancelable).
"""
import json
import os
import re
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common.logger import CustomLogger
from common.intakeq_client import IntakeQClient
from common.settings import Settings
from common.email_service import EmailService
from common.honeybee_client import HoneybeeClient, HoneybeeError
from common.dynamodb_helper import dynamodb_json
from common.utils import dob_ms_to_iso, utc_now_iso

# Environment variables (no fallbacks - Fail Fast)
ENV = os.environ['ENV']
INTAKES_TABLE = os.environ['INTAKES_TABLE']
ORDERS_TABLE = os.environ['ORDERS_TABLE']
NECTAR_ORDER_QUEUE_URL = os.environ['NECTAR_ORDER_QUEUE_URL']

logger = CustomLogger()
dynamodb = boto3.resource('dynamodb')
orders_table = dynamodb.Table(ORDERS_TABLE)
intakes_table = dynamodb.Table(INTAKES_TABLE)
sqs = boto3.client('sqs')
intakeq = IntakeQClient(logger)
settings = Settings(logger)
email_service = EmailService(logger, settings=settings)
honeybee = HoneybeeClient(logger)

TAG_BY_MODE = {'shipping': 'SENT', 'nectar': 'NECTAR', 'local_pharmacy': 'LOCAL-PHARMACY'}
CANCELABLE_NECTAR_STATUSES = ('SUBMITTED_TO_NECTAR', 'NECTAR_ON_HOLD')


def extract_payment_amount_from_tags(tags):
    """First purely numeric tag (e.g. '150', '115.00') as float, else None."""
    for tag in tags or []:
        tag_str = str(tag).strip()
        if re.match(r'^\d+(?:\.\d+)?$', tag_str):
            return float(tag_str)
    return None


def latest_intake_for_client(client_id):
    """Newest intakes record for the client (payment tier/amount source), or None."""
    response = intakes_table.query(
        IndexName='client-index',
        KeyConditionExpression=Key('client_id').eq(str(client_id)),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get('Items', [])
    return items[0] if items else None


def resolve_fulfillment_mode(service_id):
    if service_id and str(service_id) in settings.local_pharmacy_appointment_type_ids:
        return 'local_pharmacy'
    return settings.fulfillment_mode


def add_tag(client_id, tag):
    if not intakeq.update_client_tags(client_id, tag, remove=False):
        raise RuntimeError(f"Failed to add {tag} tag for client {client_id}")
    logger.log('INFO', "Added tag", client_id=client_id, tag=tag)


def build_order_record(appointment, client_data, fulfillment_mode):
    client_id = str(client_data.get('ClientId'))
    intake = latest_intake_for_client(client_id)
    payment_amount = None
    payment_tier = None
    if intake:
        payment_amount = intake.get('payment_amount')
        payment_tier = intake.get('payment_tier')
        if payment_amount is not None:
            payment_amount = float(payment_amount)
    if payment_amount is None:
        payment_amount = extract_payment_amount_from_tags(client_data.get('Tags', []))

    now = utc_now_iso()
    record = {
        'order_id': str(appointment['Id']),
        'client_id': client_id,
        'client_name': appointment.get('ClientName')
                       or f"{client_data.get('FirstName', '')} {client_data.get('LastName', '')}".strip(),
        'email': appointment.get('ClientEmail') or client_data.get('Email'),
        'dob': dob_ms_to_iso(client_data.get('DateOfBirth')),
        'shipping_address': client_data.get('StreetAddress') or '',
        'unit_number': client_data.get('UnitNumber'),
        'care_of': (client_data.get('PrimaryInsuranceHolderName') or '').strip() or None,
        'city': client_data.get('City'),
        'state': client_data.get('StateShort'),
        'postal_code': client_data.get('PostalCode'),
        'country': 'USA',
        'approval_date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'appointment_type_id': appointment.get('ServiceId'),
        'appointment_type_name': appointment.get('ServiceName'),
        'fulfillment_mode': fulfillment_mode,
        'status': 'LOCAL_PHARMACY' if fulfillment_mode == 'local_pharmacy' else 'NEW',
        'payment_amount': payment_amount,
        'payment_tier': payment_tier,
        'tracking_synced_to_intakeq': False,
        'created_at': now,
        'updated_at': now,
    }
    if fulfillment_mode == 'nectar':
        # D11 convention: the IntakeQ ClientId is sent as the EHR MRN on the
        # e-Rx, so Honeybee's patient_id == ClientId.
        record['honeybee_patient_id'] = client_id
    return record


def put_order(record):
    """Returns False if the order already exists (webhook redelivery)."""
    try:
        orders_table.put_item(Item=dynamodb_json(record),
                              ConditionExpression='attribute_not_exists(order_id)')
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False
        raise


def try_queue_nectar_order(order_id, client_id):
    """
    If Honeybee already holds an active prescription for this patient, store
    the ids and queue the fill request now. Otherwise RX_RECEIVED does it.
    """
    try:
        prescription_ids = honeybee.get_active_prescription_ids(client_id)
    except HoneybeeError as e:
        if e.status_code == 404:
            logger.log('INFO', "Patient not yet known to Honeybee - awaiting RX_RECEIVED",
                       order_id=order_id, patient_id=client_id)
        else:
            logger.log('ERROR', "Honeybee lookup failed - order left NEW",
                       order_id=order_id, status=e.status_code)
        return False

    if not prescription_ids:
        logger.log('INFO', "No active Honeybee prescription yet - awaiting RX_RECEIVED",
                   order_id=order_id, patient_id=client_id)
        return False

    orders_table.update_item(
        Key={'order_id': order_id},
        UpdateExpression='SET honeybee_prescription_ids = :p, updated_at = :now',
        ExpressionAttributeValues={':p': [int(p) for p in prescription_ids], ':now': utc_now_iso()},
    )
    sqs.send_message(QueueUrl=NECTAR_ORDER_QUEUE_URL, MessageBody=json.dumps({'order_id': order_id}))
    logger.log('INFO', "Nectar fill request queued", order_id=order_id,
               prescription_count=len(prescription_ids))
    return True


def handle_created(appointment, client_id):
    practitioner_id = appointment.get('PractitionerId')
    expected = settings.practitioner_id
    if expected and str(practitioner_id) != expected:
        logger.log('ERROR', "Appointment from unexpected practitioner - ignored",
                   appointment_id=appointment.get('Id'), practitioner_id=practitioner_id)
        return {'statusCode': 200, 'body': json.dumps('Practitioner not configured - ignored')}

    fulfillment_mode = resolve_fulfillment_mode(appointment.get('ServiceId'))

    client_data = intakeq.get_client_by_id(client_id)
    if not client_data:
        logger.log('ERROR', "Failed to retrieve client from IntakeQ", client_id=client_id)
        return {'statusCode': 400, 'body': json.dumps('Client not found')}
    logger.log('DEBUG', "Retrieved client data", client_data=client_data)

    record = build_order_record(appointment, client_data, fulfillment_mode)
    if not put_order(record):
        logger.log('INFO', "Duplicate appointment webhook ignored", order_id=record['order_id'])
        return {'statusCode': 200, 'body': json.dumps('Duplicate - order exists')}

    logger.log('INFO', "Order created", order_id=record['order_id'], client_id=client_id,
               fulfillment_mode=fulfillment_mode, status=record['status'])

    # PAID stays on the client (PBP wants it visible); add the mode tag
    add_tag(client_id, TAG_BY_MODE[fulfillment_mode])

    if fulfillment_mode == 'nectar':
        try_queue_nectar_order(record['order_id'], client_id)

    return {'statusCode': 200, 'body': json.dumps('OK')}


def handle_canceled(appointment_id, event_type):
    response = orders_table.get_item(Key={'order_id': str(appointment_id)})
    order = response.get('Item')
    if not order:
        logger.log('INFO', "Cancel for unknown order ignored", order_id=appointment_id)
        return {'statusCode': 200, 'body': json.dumps('No order')}

    if order.get('status') in ('SHIPPED', 'DELIVERED', 'CANCELED'):
        logger.log('INFO', "Cancel ignored - order already terminal",
                   order_id=order['order_id'], status=order.get('status'))
        return {'statusCode': 200, 'body': json.dumps('Order already terminal')}

    if order.get('honeybee_order_number') and order.get('status') in CANCELABLE_NECTAR_STATUSES:
        try:
            honeybee.cancel_order(order['honeybee_order_number'])
            logger.log('INFO', "Honeybee order canceled", order_id=order['order_id'],
                       order_number=order['honeybee_order_number'])
        except HoneybeeError as e:
            logger.log('ERROR', "Honeybee cancel failed", order_id=order['order_id'],
                       order_number=order['honeybee_order_number'], status=e.status_code)
            email_service.send_admin_alert(
                f"Honeybee cancel failed - order {order['order_id']}",
                f"The IntakeQ appointment was canceled but Honeybee refused to cancel "
                f"order {order['honeybee_order_number']} (HTTP {e.status_code}).\n"
                f"Cancel it manually in the Honeybee partner dashboard.\n\n"
                f"Order ID: {order['order_id']}\nClient ID: {order.get('client_id')}\n")

    orders_table.update_item(
        Key={'order_id': order['order_id']},
        UpdateExpression='SET #s = :s, canceled_at = :now, cancel_reason = :r, updated_at = :now',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'CANCELED', ':now': utc_now_iso(), ':r': event_type},
    )
    logger.log('INFO', "Order canceled", order_id=order['order_id'], event_type=event_type)
    return {'statusCode': 200, 'body': json.dumps('Order canceled')}


def lambda_handler(event, context):
    logger.log('INFO', "Received appointment webhook")
    try:
        body = json.loads(event['body']) if 'body' in event else event
        logger.log('DEBUG', "Parsed webhook data", body=body)

        event_type = body.get('EventType', '') or ''
        appointment = body.get('Appointment') or {}
        appointment_id = appointment.get('Id') or body.get('Id')
        client_id = appointment.get('ClientId') or body.get('ClientId')

        if not appointment_id:
            logger.log('ERROR', "Missing appointment ID in webhook")
            return {'statusCode': 400, 'body': json.dumps('Missing appointment ID')}

        if 'Canceled' in event_type or 'Deleted' in event_type:
            return handle_canceled(appointment_id, event_type)

        if 'Created' not in event_type:
            logger.log('INFO', "Skipping event", event_type=event_type)
            return {'statusCode': 200, 'body': json.dumps('Event ignored')}

        if not client_id:
            logger.log('ERROR', "Missing ClientId in appointment webhook")
            return {'statusCode': 400, 'body': json.dumps('Missing ClientId')}

        logger.log('INFO', "Processing appointment", appointment_id=appointment_id,
                   client_id=client_id, event_type=event_type)
        return handle_created(appointment, str(client_id))

    except RuntimeError as e:
        logger.log('ERROR', "Tag update failed", error=str(e))
        return {'statusCode': 500, 'body': json.dumps(f'Tag update failed: {str(e)}')}
    except Exception as e:
        logger.log('ERROR', "Error processing appointment webhook", error=str(e))
        return {'statusCode': 500, 'body': json.dumps('Internal server error')}
