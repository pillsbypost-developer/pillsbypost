"""
Tracking upload initiate handler (admin API, POST /tracking-upload).

Request body: { "entries": [{ "client_id": "12345", "tracking_number": "1Z...",
                              "carrier": "UPS", "notes": "..." }] }
Single entry: processed inline (200/400). Multiple: job record + Step
Functions execution (202 {job_id, total}). Administrators only.
"""
import json
import os
import uuid

import boto3

from common.logger import CustomLogger
from common.dynamodb_helper import dynamodb_json
from common.intakeq_client import IntakeQClient
from common.tracking_service import process_tracking_entry
from common.settings import Settings
from common.api_helpers import build_cors_headers, is_admin
from common.utils import ttl_from_days, utc_now_iso

# Environment variables (no fallbacks - Fail Fast)
ENV = os.environ['ENV']
TRACKING_TABLE = os.environ['TRACKING_TABLE']
TRACKING_JOBS_TABLE = os.environ['TRACKING_JOBS_TABLE']
ORDERS_TABLE = os.environ['ORDERS_TABLE']
STATE_MACHINE_ARN = os.environ['STATE_MACHINE_ARN']
CORS_ORIGIN = os.environ['CORS_ORIGIN']

logger = CustomLogger()
dynamodb = boto3.resource('dynamodb')
sf_client = boto3.client('stepfunctions')
tracking_table = dynamodb.Table(TRACKING_TABLE)
jobs_table = dynamodb.Table(TRACKING_JOBS_TABLE)
orders_table = dynamodb.Table(ORDERS_TABLE)
intakeq = IntakeQClient(logger)
settings = Settings(logger)


def get_cors_headers():
    return build_cors_headers(CORS_ORIGIN, methods='POST,OPTIONS')


def respond(status_code, body):
    return {'statusCode': status_code, 'headers': get_cors_headers(), 'body': json.dumps(body)}


def stamp_entry(entry, ttl):
    stamped = {
        'client_id': str(entry.get('client_id') or '').strip(),
        'tracking_number': str(entry.get('tracking_number') or '').strip(),
        'ttl': ttl,
    }
    if entry.get('carrier'):
        stamped['carrier'] = str(entry['carrier']).strip()
    if entry.get('notes'):
        stamped['notes'] = str(entry['notes']).strip()
    return stamped


def lambda_handler(event, context):
    logger.log('INFO', "Received tracking upload initiate request")

    if event.get('httpMethod') == 'OPTIONS':
        return {'statusCode': 200, 'headers': get_cors_headers(), 'body': ''}

    try:
        if not is_admin(event):
            logger.log('WARNING', "Non-admin tracking upload attempt")
            return respond(403, {'error': 'Administrators only'})

        body = json.loads(event.get('body') or '{}')
        entries = body.get('entries')
        if not entries or not isinstance(entries, list):
            return respond(400, {'error': 'entries array is required and must not be empty'})

        ttl = ttl_from_days(settings.tracking_retention_days)
        stamped_entries = [stamp_entry(e, ttl) for e in entries]
        invalid = [e for e in stamped_entries if not e['client_id'] or not e['tracking_number']]
        if invalid:
            return respond(400, {'error': 'Every entry needs client_id and tracking_number',
                                 'invalid_count': len(invalid)})
        logger.log('INFO', "Entries prepared", count=len(stamped_entries))

        if len(stamped_entries) == 1:
            entry = stamped_entries[0]
            logger.log('INFO', "Single entry - processing inline", client_id=entry['client_id'])
            result = process_tracking_entry(
                entry['client_id'], entry['tracking_number'], tracking_table, intakeq, logger,
                notes=entry.get('notes'), ttl=ttl, carrier=entry.get('carrier'), orders_table=orders_table,
            )
            if result['status'] == 'success':
                return respond(200, result)
            return respond(400, {'error': result.get('error', 'Processing failed')})

        job_id = str(uuid.uuid4())
        job_record = {
            'job_id': job_id,
            'status': 'PENDING',
            'total': len(stamped_entries),
            'succeeded': 0,
            'failed': 0,
            'entries': stamped_entries,
            'results': [],
            'created_at': utc_now_iso(),
            'ttl': ttl,
        }
        jobs_table.put_item(Item=dynamodb_json(job_record))
        logger.log('INFO', "Job record written", job_id=job_id, total=len(stamped_entries))

        sf_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=job_id,
            input=json.dumps({'job_id': job_id, 'entries': stamped_entries}),
        )
        logger.log('INFO', "Step Function execution started", job_id=job_id)
        return respond(202, {'job_id': job_id, 'total': len(stamped_entries)})

    except Exception as e:
        logger.log('ERROR', "Unhandled error in tracking-initiate", error=str(e))
        return respond(500, {'error': 'Internal server error'})
