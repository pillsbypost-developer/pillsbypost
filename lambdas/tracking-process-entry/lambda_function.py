"""
Single-entry tracking processor, invoked by the tracking-batch Step Functions
Map state (one entry per invocation, sequential to respect IntakeQ limits).

Event: { "job_id": "uuid", "entry": { "client_id", "tracking_number", "carrier"?, "notes"?, "ttl"? } }
"""
import os

import boto3

from common.logger import CustomLogger
from common.intakeq_client import IntakeQClient
from common.tracking_service import process_tracking_entry

# Environment variables (no fallbacks - Fail Fast)
ENV = os.environ['ENV']
TRACKING_TABLE = os.environ['TRACKING_TABLE']
TRACKING_JOBS_TABLE = os.environ['TRACKING_JOBS_TABLE']
ORDERS_TABLE = os.environ['ORDERS_TABLE']

logger = CustomLogger()
dynamodb = boto3.resource('dynamodb')
tracking_table = dynamodb.Table(TRACKING_TABLE)
jobs_table = dynamodb.Table(TRACKING_JOBS_TABLE)
orders_table = dynamodb.Table(ORDERS_TABLE)
intakeq = IntakeQClient(logger, api_delay=6.0)  # 6s delay = 10 req/min IntakeQ limit


def lambda_handler(event, context):
    logger.log('INFO', "Received single-entry tracking request")

    job_id = event['job_id']
    entry = event['entry']

    client_id = str(entry.get('client_id') or '').strip()
    tracking_number = str(entry.get('tracking_number') or '').strip()
    carrier = str(entry['carrier']).strip() if entry.get('carrier') else None
    notes = str(entry['notes']).strip() if entry.get('notes') else None
    ttl = entry.get('ttl')

    if not client_id or not tracking_number:
        logger.log('INFO', "Missing required fields in entry", client_id=client_id)
        result = {'client_id': client_id, 'tracking_number': tracking_number,
                  'status': 'error', 'error': 'Missing client_id or tracking_number'}
    else:
        try:
            result = process_tracking_entry(
                client_id, tracking_number, tracking_table, intakeq, logger,
                notes=notes, ttl=ttl, carrier=carrier, orders_table=orders_table,
            )
        except Exception as e:
            logger.log('ERROR', "Unexpected error processing entry", client_id=client_id, error=str(e))
            result = {'client_id': client_id, 'tracking_number': tracking_number,
                      'status': 'error', 'error': f'Unexpected error: {str(e)}'}

    counter = 'succeeded' if result['status'] == 'success' else 'failed'
    jobs_table.update_item(
        Key={'job_id': job_id},
        UpdateExpression=f'SET #r = list_append(#r, :result) ADD {counter} :one',
        ExpressionAttributeNames={'#r': 'results'},
        ExpressionAttributeValues={':result': [result], ':one': 1},
    )
    logger.log('INFO', "Job counters updated", job_id=job_id, status=result['status'])
    return result
