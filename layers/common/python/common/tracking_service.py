import uuid
from datetime import datetime, timezone

from common.dynamodb_helper import dynamodb_json

SHIPPABLE_STATUSES = ('NEW', 'EXPORTED', 'SUBMITTED_TO_NECTAR', 'NECTAR_ON_HOLD')


def process_tracking_entry(client_id, tracking_number, tracking_table, intakeq, logger,
                           notes=None, ttl=None, carrier=None, orders_table=None):
    """
    Record one tracking number for a client.

    1. Fetch the client from IntakeQ
    2. Write "Tracking number MM/DD/YYYY: <no>" to AdditionalInformation
    3. Write the tracking record (always, with synced_to_intakeq)
    4. If orders_table is given, mark the client's newest open order SHIPPED

    Returns {'client_id', 'tracking_number', 'status': 'success'|'error', ['error']}.
    """
    logger.log('INFO', "Processing tracking entry", client_id=client_id)
    logger.log('DEBUG', "Entry tracking number", tracking_number=tracking_number)

    client_data = intakeq.get_client_by_id(client_id)
    if not client_data:
        logger.log('INFO', "Client not found in IntakeQ", client_id=client_id)
        return {'client_id': client_id, 'tracking_number': tracking_number,
                'status': 'error', 'error': 'Client not found in IntakeQ'}

    today_str = datetime.now(timezone.utc).strftime('%m/%d/%Y')
    client_data['AdditionalInformation'] = f"Tracking number {today_str}: {tracking_number}"
    synced_to_intakeq = intakeq.update_client(client_data) is not None
    logger.log('INFO', "IntakeQ AdditionalInformation update",
               client_id=client_id, synced_to_intakeq=synced_to_intakeq)

    tracking_record = {
        'tracking_id': str(uuid.uuid4()),
        'client_id': str(client_id),
        'tracking_number': tracking_number,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'synced_to_intakeq': synced_to_intakeq,
    }
    if carrier:
        tracking_record['carrier'] = carrier
    if notes:
        tracking_record['notes'] = notes
    if ttl:
        tracking_record['ttl'] = ttl
    tracking_table.put_item(Item=dynamodb_json(tracking_record))

    if orders_table is not None:
        mark_order_shipped(orders_table, client_id, tracking_number, carrier, synced_to_intakeq, logger)

    if synced_to_intakeq:
        return {'client_id': client_id, 'tracking_number': tracking_number, 'status': 'success'}
    return {'client_id': client_id, 'tracking_number': tracking_number, 'status': 'error',
            'error': 'IntakeQ update failed - tracking record written with synced_to_intakeq=false'}


def mark_order_shipped(orders_table, client_id, tracking_number, carrier, synced, logger):
    """
    Attach tracking to the client's most recent order still awaiting shipment.
    No open order is not an error (tracking may arrive for a pre-platform
    order): it is logged and the tracking record stands alone.
    """
    from boto3.dynamodb.conditions import Key

    response = orders_table.query(
        IndexName='client-index',
        KeyConditionExpression=Key('client_id').eq(str(client_id)),
        ScanIndexForward=False,
    )
    candidates = [o for o in response.get('Items', []) if o.get('status') in SHIPPABLE_STATUSES]
    if not candidates:
        logger.log('INFO', "No open order for tracking entry", client_id=client_id)
        return None

    order = candidates[0]
    now = datetime.now(timezone.utc).isoformat()
    orders_table.update_item(
        Key={'order_id': order['order_id']},
        UpdateExpression='SET #s = :s, tracking_number = :t, carrier = :c, '
                         'tracking_synced_to_intakeq = :sync, shipped_at = :now, updated_at = :now',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={
            ':s': 'SHIPPED',
            ':t': tracking_number,
            ':c': carrier or order.get('carrier') or '',
            ':sync': synced,
            ':now': now,
        },
    )
    logger.log('INFO', "Order marked SHIPPED", order_id=order['order_id'], client_id=client_id)
    return order['order_id']
