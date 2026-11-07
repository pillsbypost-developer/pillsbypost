"""
Payment webhook - Converge payment confirmation -> PAID tag.

DECISION D4 IS OPEN: the notification source may be the Converge hosted
page's return-URL POST (browser-driven, forgeable), a scheduled
reconciliation, or Elavon Payment Gateway webhooks. This handler therefore
accepts JSON or form-encoded bodies and NEVER trusts them: every
notification is verified against Converge's transaction query before any
tag is written.

Paths:
  no txn id / no client id      -> admin alert, 200
  verification fails            -> admin alert, 200 (no tags)
  client not found in IntakeQ   -> admin alert, 200
  verified                      -> PAID + <amount> tags; PAYMENT DISCREPANCY
                                   (+ alert) if the amount differs from the
                                   intake's payment tier; intake record updated
"""
import base64
import json
import os
from decimal import Decimal
from urllib.parse import parse_qs

import boto3
from boto3.dynamodb.conditions import Key

from common.logger import CustomLogger
from common.intakeq_client import IntakeQClient
from common.settings import Settings
from common.email_service import EmailService
from common.converge_client import ConvergeClient
from common.utils import utc_now_iso

# Environment variables (no fallbacks - Fail Fast)
ENV = os.environ['ENV']
INTAKES_TABLE = os.environ['INTAKES_TABLE']
ORDERS_TABLE = os.environ['ORDERS_TABLE']

logger = CustomLogger()
dynamodb = boto3.resource('dynamodb')
intakes_table = dynamodb.Table(INTAKES_TABLE)
orders_table = dynamodb.Table(ORDERS_TABLE)
intakeq = IntakeQClient(logger)
settings = Settings(logger)
email_service = EmailService(logger, settings=settings)
converge = ConvergeClient(logger)

FIELD_ALIASES = {
    'txn_id': ('ssl_txn_id', 'txn_id', 'transaction_id'),
    'client_id': ('ssl_customer_id', 'ssl_invoice_number', 'client_id', 'customer_id'),
    'amount': ('ssl_amount', 'amount'),
    'email': ('ssl_email', 'email'),
    'result': ('ssl_result', 'result'),
}


def parse_body(event):
    """Dict of notification fields from a JSON or form-encoded body (or a direct invoke)."""
    if 'body' not in event:
        return {k: v for k, v in event.items() if k not in ('headers', 'requestContext')}
    body = event.get('body') or ''
    if event.get('isBase64Encoded'):
        body = base64.b64decode(body).decode('utf-8')
    content_type = ''
    for key, value in (event.get('headers') or {}).items():
        if key.lower() == 'content-type':
            content_type = value or ''
    if 'json' in content_type.lower() or body.lstrip().startswith('{'):
        return json.loads(body)
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


def field(payload, name):
    for alias in FIELD_ALIASES[name]:
        value = payload.get(alias)
        if value not in (None, ''):
            return str(value).strip()
    return None


def format_amount(value):
    return f"{Decimal(str(value)):.2f}"


def latest_intake_for_client(client_id):
    response = intakes_table.query(
        IndexName='client-index',
        KeyConditionExpression=Key('client_id').eq(str(client_id)),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get('Items', [])
    return items[0] if items else None


def alert(subject, lines):
    email_service.send_admin_alert(subject, "\n".join(lines) + "\n")


def notification_summary(payload, client_id=None):
    return [
        f"Client ID: {client_id or field(payload, 'client_id') or '(none)'}",
        f"Transaction ID: {field(payload, 'txn_id') or '(none)'}",
        f"Amount: {field(payload, 'amount') or '(none)'}",
        f"Email: {field(payload, 'email') or '(none)'}",
    ]


def verify_transaction(txn_id, client_id):
    """(ok, txn, reason). ok only when Converge says approved for this customer."""
    txn = converge.query_transaction(txn_id)
    if not converge.is_approved(txn):
        return False, txn, f"Converge result {txn.get('ssl_result')!r} / status {txn.get('ssl_trans_status')!r} is not approved"
    verified_ids = {str(txn.get(k) or '').strip() for k in ('ssl_customer_id', 'ssl_invoice_number')}
    if str(client_id) not in verified_ids:
        return False, txn, "Converge customer/invoice id does not match the notified client id"
    return True, txn, None


def add_tag(client_id, tag):
    if not intakeq.update_client_tags(client_id, tag, remove=False):
        raise RuntimeError(f"Failed to add {tag} tag for client {client_id}")
    logger.log('INFO', "Added tag", client_id=client_id, tag=tag)


def record_payment(client_id, intake, paid_amount, txn_id):
    now = utc_now_iso()
    if intake:
        intakes_table.update_item(
            Key={'intake_id': intake['intake_id']},
            UpdateExpression='SET paid_at = :t, paid_amount = :a, converge_txn_id = :x, payment_status = :s',
            ExpressionAttributeValues={':t': now, ':a': Decimal(paid_amount), ':x': txn_id, ':s': 'PAID'},
        )
    response = orders_table.query(
        IndexName='client-index',
        KeyConditionExpression=Key('client_id').eq(str(client_id)),
        ScanIndexForward=False,
    )
    for order in response.get('Items', []):
        if order.get('status') == 'NEW' and order.get('payment_amount') is None:
            orders_table.update_item(
                Key={'order_id': order['order_id']},
                UpdateExpression='SET payment_amount = :a, updated_at = :now',
                ExpressionAttributeValues={':a': Decimal(paid_amount), ':now': now},
            )


def lambda_handler(event, context):
    logger.log('INFO', "Received payment notification")
    try:
        payload = parse_body(event)
        logger.log('DEBUG', "Payment notification payload", payload=payload)

        txn_id = field(payload, 'txn_id')
        client_id = field(payload, 'client_id')

        if not txn_id or not client_id:
            logger.log('ERROR', "Payment notification missing transaction id or client id")
            alert("Payment notification missing transaction or client id",
                  ["A payment notification arrived without enough information to verify it.",
                   "Reconcile it manually in Converge.", ""] + notification_summary(payload))
            return {'statusCode': 200, 'body': json.dumps('Incomplete notification - admin notified')}

        logger.log('INFO', "Verifying payment", client_id=client_id, txn_id=txn_id)
        ok, txn, reason = verify_transaction(txn_id, client_id)
        if not ok:
            logger.log('ERROR', "Payment verification failed", client_id=client_id, txn_id=txn_id, reason=reason)
            alert("Payment verification failed",
                  ["A payment notification could not be verified against Converge. No tags were applied.",
                   f"Reason: {reason}", ""] + notification_summary(payload, client_id))
            return {'statusCode': 200, 'body': json.dumps('Verification failed - admin notified')}

        paid_amount = format_amount(txn.get('ssl_amount') or field(payload, 'amount') or '0')

        client_data = intakeq.get_client_by_id(client_id)
        if not client_data:
            logger.log('ERROR', "Client not found in IntakeQ", client_id=client_id)
            alert("Payment received - client not found",
                  ["A verified payment references a client that no longer exists in IntakeQ "
                   "(possibly a stale payment link).", ""] + notification_summary(payload, client_id)
                  + [f"Verified amount: {paid_amount}"])
            return {'statusCode': 200, 'body': json.dumps('Client not found - admin notified')}

        add_tag(client_id, 'PAID')
        add_tag(client_id, paid_amount)

        intake = latest_intake_for_client(client_id)
        expected = intake.get('payment_amount') if intake else None
        if expected is not None and format_amount(expected) != paid_amount:
            logger.log('INFO', "Payment discrepancy", client_id=client_id, paid=paid_amount,
                       expected=format_amount(expected))
            add_tag(client_id, 'PAYMENT DISCREPANCY')
            alert(f"Payment discrepancy - client {client_id}",
                  [f"Paid: {paid_amount}", f"Expected (payment tier): {format_amount(expected)}",
                   f"Transaction ID: {txn_id}"])

        record_payment(client_id, intake, paid_amount, txn_id)
        logger.log('INFO', "Payment recorded", client_id=client_id, amount=paid_amount, txn_id=txn_id)
        return {'statusCode': 200, 'body': json.dumps('OK')}

    except RuntimeError as e:
        logger.log('ERROR', "Tag update failed", error=str(e))
        return {'statusCode': 500, 'body': json.dumps(f'Tag update failed: {str(e)}')}
    except Exception as e:
        logger.log('ERROR', "Error processing payment notification", error=str(e))
        return {'statusCode': 500, 'body': json.dumps('Internal server error')}
