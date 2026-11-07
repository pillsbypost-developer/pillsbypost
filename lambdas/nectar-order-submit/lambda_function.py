"""
Nectar order submit - SQS consumer that sends the fill request to Honeybee.

Message body: {"order_id": "<IntakeQ appointment Id>"}. Enqueued by
appointment-webhook (prescription already at Honeybee), honeybee-webhook
(RX_RECEIVED) or the admin UI (re-queue). BatchSize 1, idempotent: an order
that already has a Honeybee order number is skipped.

Failure policy:
  - 4xx from Honeybee  -> order NECTAR_ON_HOLD + admin email, message consumed
  - 5xx / transport    -> raise, SQS redelivers, DLQ alarm after 3 attempts
  - config errors      -> raise (ValueError) so they surface in the DLQ
"""
import json
import os

import boto3
import requests

from common.logger import CustomLogger
from common.intakeq_client import IntakeQClient
from common.settings import Settings
from common.email_service import EmailService
from common.honeybee_client import HoneybeeClient, HoneybeeError
from common.utils import utc_now_iso, ttl_from_days

# Environment variables (no fallbacks - Fail Fast)
ENV = os.environ['ENV']
ORDERS_TABLE = os.environ['ORDERS_TABLE']

logger = CustomLogger()
dynamodb = boto3.resource('dynamodb')
orders_table = dynamodb.Table(ORDERS_TABLE)
intakeq = IntakeQClient(logger)
settings = Settings(logger)
email_service = EmailService(logger, settings=settings)
honeybee = HoneybeeClient(logger)

SUBMITTABLE_STATUSES = ('NEW', 'NECTAR_ON_HOLD')


def build_address(order):
    name = (order.get('client_name') or '').strip()
    first, _, last = name.partition(' ')
    address = {
        'first_name': first,
        'last_name': last or first,
        'street1': order.get('shipping_address') or '',
        'city': order.get('city') or '',
        'state': order.get('state') or '',
        'zip': order.get('postal_code') or '',
    }
    if order.get('unit_number'):
        address['street2'] = order['unit_number']
    return address


def put_on_hold(order_id, reason):
    orders_table.update_item(
        Key={'order_id': order_id},
        UpdateExpression='SET #s = :s, honeybee_error = :e, updated_at = :now',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'NECTAR_ON_HOLD', ':e': str(reason)[:1000], ':now': utc_now_iso()},
    )
    logger.log('ERROR', "Order NECTAR_ON_HOLD", order_id=order_id)
    email_service.send_admin_alert(
        f"Nectar order on hold - order {order_id}",
        f"The fill request for order {order_id} could not be submitted to Honeybee.\n\n"
        f"Reason: {reason}\n\n"
        f"Fix the cause and re-queue the order from the admin UI.\n")


def submit_order(order_id):
    order = orders_table.get_item(Key={'order_id': order_id}).get('Item')
    if not order:
        logger.log('ERROR', "Order not found - message dropped", order_id=order_id)
        return

    if order.get('honeybee_order_number'):
        logger.log('INFO', "Order already submitted to Honeybee - skipped",
                   order_id=order_id, order_number=order['honeybee_order_number'])
        return
    if order.get('status') not in SUBMITTABLE_STATUSES:
        logger.log('INFO', "Order not submittable - skipped", order_id=order_id, status=order.get('status'))
        return

    patient_id = order.get('honeybee_patient_id') or order['client_id']
    prescription_ids = [int(p) for p in order.get('honeybee_prescription_ids') or []]
    if not prescription_ids:
        prescription_ids = [int(p) for p in honeybee.get_active_prescription_ids(patient_id)]
    if not prescription_ids:
        put_on_hold(order_id, "No active prescription at Honeybee for this patient")
        return

    shipping_method_id = settings.honeybee_shipping_method_id
    if not shipping_method_id:
        raise ValueError("honeybee_shipping_method_id setting is not configured")

    logger.log('INFO', "Submitting Nectar order", order_id=order_id, patient_id=patient_id,
               prescription_count=len(prescription_ids))
    try:
        response = honeybee.create_order(
            patient_id=patient_id,
            prescription_ids=prescription_ids,
            shipping_method_id=shipping_method_id,
            address=build_address(order),
        )
    except HoneybeeError as e:
        if 400 <= e.status_code < 500:
            put_on_hold(order_id, f"Honeybee rejected the order (HTTP {e.status_code}): {e.body}")
            return
        raise

    order_number = response['order_number']
    orders_table.update_item(
        Key={'order_id': order_id},
        UpdateExpression='SET #s = :s, honeybee_order_number = :n, honeybee_patient_id = :pid, '
                         'honeybee_prescription_ids = :rx, nectar_submitted_at = :now, updated_at = :now, '
                         '#ttl = :ttl REMOVE honeybee_error',
        ExpressionAttributeNames={'#s': 'status', '#ttl': 'ttl'},
        ExpressionAttributeValues={
            ':s': 'SUBMITTED_TO_NECTAR',
            ':n': order_number,
            ':pid': str(patient_id),
            ':rx': prescription_ids,
            ':now': utc_now_iso(),
            ':ttl': ttl_from_days(settings.orders_retention_days),
        },
    )
    logger.log('INFO', "Order SUBMITTED_TO_NECTAR", order_id=order_id, order_number=order_number)

    if not intakeq.update_client_tags(order['client_id'], 'NECTAR-SUBMITTED', remove=False):
        logger.log('ERROR', "Failed to add NECTAR-SUBMITTED tag", client_id=order['client_id'])


def lambda_handler(event, context):
    for record in event['Records']:
        body = json.loads(record['body'])
        order_id = str(body['order_id'])
        try:
            submit_order(order_id)
        except requests.RequestException as e:
            logger.log('ERROR', "Honeybee transport error - will retry", order_id=order_id, error=str(e))
            raise
    return {'statusCode': 200}
