"""
Admin API CRUD handler (Administrators Cognito group only).

Routes:
  OPTIONS *                         CORS preflight
  GET    /{table}                   list (orders: ?status=, ?fulfillment_mode=)
  POST   /{table}                   create (not on read-only tables)
  GET    /{table}/{id}              read
  PUT    /{table}/{id}              update (not on read-only tables)
  DELETE /{table}/{id}              delete (not on read-only tables)
  POST   /orders/export-batch       mark NEW shipping orders EXPORTED, return CSV
  POST   /orders/{id}/mark-paid     PAID + amount tags in IntakeQ; order + intake updated
  POST   /orders/{id}/cancel        CANCELED (+ Honeybee cancel when submitted)
  POST   /orders/{id}/resubmit-nectar  re-queue a nectar order
  POST   /orders/{id}/mark-shipped  record tracking manually
"""
import csv
import io
import json
import os
import uuid

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common.logger import CustomLogger
from common.crud_handler import CRUDHandler
from common.settings import Settings
from common.intakeq_client import IntakeQClient
from common.honeybee_client import HoneybeeClient, HoneybeeError
from common.tracking_service import process_tracking_entry
from common.api_helpers import build_cors_headers, get_user_context
from common.utils import ttl_from_days, utc_now_iso
from common.dynamodb_helper import DecimalEncoder

from table_configs import get_table_config, READ_ONLY_TABLES

logger = CustomLogger()

# Environment variables (no fallbacks - Fail Fast)
ENV = os.environ['ENV']
CORS_ORIGIN = os.environ['CORS_ORIGIN']
ORDERS_TABLE = os.environ['ORDERS_TABLE']
INTAKES_TABLE = os.environ['INTAKES_TABLE']
TRACKING_TABLE = os.environ['TRACKING_TABLE']
NECTAR_ORDER_QUEUE_URL = os.environ['NECTAR_ORDER_QUEUE_URL']

dynamodb = boto3.resource('dynamodb')
sqs = boto3.client('sqs')
orders_table = dynamodb.Table(ORDERS_TABLE)
intakes_table = dynamodb.Table(INTAKES_TABLE)
tracking_table = dynamodb.Table(TRACKING_TABLE)
settings = Settings(logger)
intakeq = IntakeQClient(logger)
honeybee = HoneybeeClient(logger)

# Shipping-vendor export columns (decision D16: vendor CSV format TBD)
EXPORT_CSV_COLUMNS = [
    'client_id', 'client_name', 'email', 'shipping_address', 'unit_number', 'care_of',
    'city', 'state', 'postal_code', 'country', 'payment_tier', 'payment_amount', 'approval_date',
]

ORDER_ACTIONS = {'mark-paid', 'cancel', 'resubmit-nectar', 'mark-shipped'}


def respond(status_code, body):
    return {
        'statusCode': status_code,
        'headers': build_cors_headers(CORS_ORIGIN),
        'body': body if isinstance(body, str) else json.dumps(body, cls=DecimalEncoder),
    }


def error(status_code, title, message):
    return respond(status_code, {'error': title, 'message': message})


def parse_request_body(event):
    if not event.get('body'):
        return None, error(400, 'Missing request body', 'Request body is required')
    try:
        return json.loads(event['body']), None
    except json.JSONDecodeError:
        return None, error(400, 'Invalid JSON', 'Request body must be valid JSON')


# ---- generic CRUD -----------------------------------------------------------

def handle_get_list(handler, table_name, event, user_id):
    params = event.get('queryStringParameters') or {}

    if table_name == 'orders' and params.get('status'):
        items = query_orders_by_status(params['status'])
    else:
        items = handler.get_all()

    if table_name == 'orders' and params.get('fulfillment_mode'):
        items = [i for i in items if i.get('fulfillment_mode') == params['fulfillment_mode']]

    if table_name == 'orders':
        items.sort(key=lambda x: x.get('approval_date', ''), reverse=True)
    else:
        sort_field = handler.config.required_fields[0] if handler.config.required_fields else handler.config.primary_key
        items.sort(key=lambda x: str(x.get(sort_field, '')))

    logger.log('INFO', 'Items retrieved', table=handler.config.table_name, count=len(items), user_id=user_id)
    return respond(200, {'items': items, 'count': len(items)})


def query_orders_by_status(status):
    items = []
    kwargs = {'IndexName': 'status-index', 'KeyConditionExpression': Key('status').eq(status),
              'ScanIndexForward': False}
    response = orders_table.query(**kwargs)
    items.extend(response.get('Items', []))
    while 'LastEvaluatedKey' in response:
        response = orders_table.query(ExclusiveStartKey=response['LastEvaluatedKey'], **kwargs)
        items.extend(response.get('Items', []))
    return items


def handle_get_one(handler, record_id, user_id):
    item = handler.get_one(record_id)
    if not item:
        return error(404, 'Not found', f'Record with id {record_id} not found')
    logger.log('INFO', 'Item retrieved', table=handler.config.table_name, item_id=record_id, user_id=user_id)
    return respond(200, item)


def handle_create(handler, event, user_id):
    body, err = parse_request_body(event)
    if err:
        return err
    item, message = handler.create(body, user_id)
    if message:
        return error(409 if 'already exists' in message.lower() else 400, 'Create failed', message)
    logger.log('INFO', 'Item created', table=handler.config.table_name,
               item_id=item[handler.config.primary_key], user_id=user_id)
    return respond(201, item)


def handle_update(handler, record_id, event, user_id):
    body, err = parse_request_body(event)
    if err:
        return err
    item, message = handler.update(record_id, body, user_id)
    if message:
        status = 404 if message == "Record not found" else 409 if 'already exists' in message.lower() else 400
        return error(status, 'Update failed', message)
    logger.log('INFO', 'Item updated', table=handler.config.table_name, item_id=record_id, user_id=user_id)
    return respond(200, item)


def handle_delete(handler, record_id, user_id):
    success, message = handler.delete(record_id, user_id)
    if not success:
        return error(404 if message == "Record not found" else 400, 'Delete failed', message)
    logger.log('INFO', 'Item deleted', table=handler.config.table_name, item_id=record_id, user_id=user_id)
    return respond(200, {'message': 'Record deleted successfully', 'id': record_id})


# ---- order actions -----------------------------------------------------------

def get_order(order_id):
    response = orders_table.get_item(Key={'order_id': str(order_id)})
    return response.get('Item')


def update_order(order_id, fields):
    """SET the given fields on an order; every key goes through a name placeholder."""
    fields = dict(fields)
    fields['updated_at'] = utc_now_iso()
    names, values, parts = {}, {}, []
    for i, (key, value) in enumerate(fields.items()):
        names[f'#f{i}'] = key
        values[f':v{i}'] = value
        parts.append(f'#f{i} = :v{i}')
    orders_table.update_item(
        Key={'order_id': str(order_id)},
        UpdateExpression='SET ' + ', '.join(parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def build_export_csv(orders):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_CSV_COLUMNS, extrasaction='ignore')
    writer.writeheader()
    for order in orders:
        row = {col: order.get(col, '') for col in EXPORT_CSV_COLUMNS}
        if row.get('payment_amount') not in ('', None):
            row['payment_amount'] = f"{float(row['payment_amount']):.2f}"
        writer.writerow({k: ('' if v is None else v) for k, v in row.items()})
    return buffer.getvalue()


def handle_export_batch(event, user_id):
    body, err = parse_request_body(event)
    if err:
        return err
    order_ids = body.get('order_ids')
    if not order_ids or not isinstance(order_ids, list):
        return error(400, 'Invalid request', 'order_ids must be a non-empty list')

    export_batch_id = str(uuid.uuid4())
    exported_at = utc_now_iso()
    ttl_value = ttl_from_days(settings.orders_retention_days)
    exported, skipped = [], []

    for order_id in order_ids:
        order = get_order(order_id)
        if not order:
            skipped.append({'order_id': order_id, 'reason': 'not found'})
            continue
        if order.get('status') != 'NEW' or order.get('fulfillment_mode') != 'shipping':
            skipped.append({'order_id': order_id, 'reason': f"status {order.get('status')} / mode {order.get('fulfillment_mode')}"})
            continue
        update_order(order_id, {
            'status': 'EXPORTED',
            'exported_at': exported_at,
            'export_batch_id': export_batch_id,
            'ttl': ttl_value,
        })
        exported.append(order)

    logger.log('INFO', 'Export batch completed', export_batch_id=export_batch_id,
               exported_count=len(exported), skipped_count=len(skipped), user_id=user_id)
    return respond(200, {
        'export_batch_id': export_batch_id,
        'exported_count': len(exported),
        'exported_at': exported_at,
        'skipped': skipped,
        'csv': build_export_csv(exported),
    })


def latest_intake_for_client(client_id):
    response = intakes_table.query(
        IndexName='client-index',
        KeyConditionExpression=Key('client_id').eq(str(client_id)),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get('Items', [])
    return items[0] if items else None


def handle_mark_paid(order, event, user_id):
    body, err = parse_request_body(event)
    if err:
        return err
    try:
        amount = float(body.get('amount'))
    except (TypeError, ValueError):
        return error(400, 'Invalid request', 'amount must be a number')
    amount_tag = f"{amount:.2f}"
    client_id = order['client_id']

    if not intakeq.update_client_tags(client_id, 'PAID', remove=False):
        return error(502, 'IntakeQ error', 'Failed to add PAID tag')
    if not intakeq.update_client_tags(client_id, amount_tag, remove=False):
        return error(502, 'IntakeQ error', 'Failed to add amount tag')

    paid_at = utc_now_iso()
    update_order(order['order_id'], {'payment_amount': amount_tag, 'paid_at': paid_at,
                                     'payment_status': 'PAID'})

    intake = latest_intake_for_client(client_id)
    if intake:
        intakes_table.update_item(
            Key={'intake_id': intake['intake_id']},
            UpdateExpression='SET paid_at = :p, paid_amount = :a, payment_status = :s, payment_source = :src',
            ExpressionAttributeValues={':p': paid_at, ':a': amount_tag, ':s': 'PAID', ':src': 'manual'},
        )

    logger.log('INFO', 'Order marked paid', order_id=order['order_id'], client_id=client_id,
               amount=amount_tag, user_id=user_id)
    return respond(200, {'order_id': order['order_id'], 'payment_status': 'PAID',
                         'payment_amount': amount_tag, 'paid_at': paid_at})


def handle_cancel(order, user_id):
    if order.get('status') == 'CANCELED':
        return respond(200, {'order_id': order['order_id'], 'status': 'CANCELED'})
    if order.get('honeybee_order_number') and order.get('status') == 'SUBMITTED_TO_NECTAR':
        try:
            honeybee.cancel_order(order['honeybee_order_number'])
        except HoneybeeError as e:
            logger.log('ERROR', 'Honeybee cancel failed', order_id=order['order_id'], status=e.status_code)
            return error(409, 'Honeybee cancel failed',
                         f"Honeybee returned {e.status_code}; the order may already be filled")
    update_order(order['order_id'], {'status': 'CANCELED', 'canceled_at': utc_now_iso()})
    logger.log('INFO', 'Order canceled', order_id=order['order_id'], user_id=user_id)
    return respond(200, {'order_id': order['order_id'], 'status': 'CANCELED'})


def handle_resubmit_nectar(order, user_id):
    if order.get('fulfillment_mode') != 'nectar':
        return error(400, 'Invalid request', 'Only nectar orders can be resubmitted')
    if order.get('status') not in ('NEW', 'NECTAR_ON_HOLD'):
        return error(409, 'Invalid state', f"Order status {order.get('status')} cannot be resubmitted")
    sqs.send_message(QueueUrl=NECTAR_ORDER_QUEUE_URL,
                     MessageBody=json.dumps({'order_id': order['order_id'], 'source': 'admin-resubmit'}))
    logger.log('INFO', 'Nectar order re-queued', order_id=order['order_id'], user_id=user_id)
    return respond(202, {'order_id': order['order_id'], 'queued': True})


def handle_mark_shipped(order, event, user_id):
    body, err = parse_request_body(event)
    if err:
        return err
    tracking_number = str(body.get('tracking_number') or '').strip()
    if not tracking_number:
        return error(400, 'Invalid request', 'tracking_number is required')
    carrier = (body.get('carrier') or '').strip() or None

    result = process_tracking_entry(
        order['client_id'], tracking_number, tracking_table, intakeq, logger,
        ttl=ttl_from_days(settings.tracking_retention_days), carrier=carrier, orders_table=orders_table,
    )
    logger.log('INFO', 'Manual tracking entry', order_id=order['order_id'], status=result['status'], user_id=user_id)
    if result['status'] == 'success':
        return respond(200, result)
    return error(400, 'Tracking failed', result.get('error', 'Processing failed'))


def handle_order_action(action, record_id, event, user_id):
    order = get_order(record_id)
    if not order:
        return error(404, 'Not found', f'Order {record_id} not found')
    if action == 'mark-paid':
        return handle_mark_paid(order, event, user_id)
    if action == 'cancel':
        return handle_cancel(order, user_id)
    if action == 'resubmit-nectar':
        return handle_resubmit_nectar(order, user_id)
    if action == 'mark-shipped':
        return handle_mark_shipped(order, event, user_id)
    return error(404, 'Unknown action', action)


# ---- entry point ---------------------------------------------------------------

def lambda_handler(event, context):
    logger.log('INFO', 'CRUD request received', method=event.get('httpMethod'), path=event.get('path'))
    logger.log('DEBUG', 'CRUD event', event=event)

    try:
        http_method = event.get('httpMethod')
        if http_method == 'OPTIONS':
            return {'statusCode': 200, 'headers': build_cors_headers(CORS_ORIGIN), 'body': ''}

        user = get_user_context(event)
        user_id = user['user_id']
        if 'Administrators' not in user['groups']:
            logger.log('WARNING', 'Non-admin access attempt', user_id=user_id, groups=user['groups'])
            return error(403, 'Insufficient permissions', 'Administrators only')

        path_parts = [p for p in event.get('path', '').strip('/').split('/') if p]
        if not path_parts:
            return error(400, 'Invalid path', 'Path must include table name')
        table_name = path_parts[0]
        record_id = path_parts[1] if len(path_parts) > 1 else None
        action = path_parts[2] if len(path_parts) > 2 else None

        try:
            config = get_table_config(table_name)
        except ValueError as e:
            return error(400, 'Invalid table', str(e))
        handler = CRUDHandler(config)

        # Order actions
        if table_name == 'orders' and http_method == 'POST':
            if record_id == 'export-batch' and action is None:
                return handle_export_batch(event, user_id)
            if action in ORDER_ACTIONS:
                return handle_order_action(action, record_id, event, user_id)
        if action:
            return error(404, 'Unknown action', f'{table_name}/{record_id}/{action}')

        is_write = http_method in ('POST', 'PUT', 'DELETE')
        if is_write and table_name in READ_ONLY_TABLES:
            return error(405, 'Method not allowed', f'{table_name} is read-only')

        if http_method == 'GET':
            return handle_get_one(handler, record_id, user_id) if record_id else handle_get_list(handler, table_name, event, user_id)
        if http_method == 'POST':
            if record_id:
                return error(404, 'Unknown action', record_id)
            return handle_create(handler, event, user_id)
        if http_method == 'PUT':
            if not record_id:
                return error(400, 'Missing record ID', 'Record ID is required for update')
            return handle_update(handler, record_id, event, user_id)
        if http_method == 'DELETE':
            if not record_id:
                return error(400, 'Missing record ID', 'Record ID is required for delete')
            return handle_delete(handler, record_id, user_id)
        return error(405, 'Method not allowed', f'HTTP method {http_method} is not supported')

    except ClientError as e:
        logger.log('ERROR', 'DynamoDB error in CRUD handler', error_code=e.response['Error']['Code'], error=str(e))
        return error(500, 'Database error occurred', 'Unable to process request')
    except Exception as e:
        logger.log('ERROR', 'Unexpected error in CRUD handler', error=str(e))
        return error(500, 'Internal server error', 'An unexpected error occurred')
