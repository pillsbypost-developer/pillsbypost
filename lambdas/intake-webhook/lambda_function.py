"""
Intake Webhook Lambda

Thin, fast receiver for IntakeQ intake webhooks. All slow IntakeQ
orchestration lives in the intake-worker lambda (fed by SQS).

Why this exists:
- API Gateway caps integrations at 29s; the full intake flow makes 10+
  IntakeQ calls and can exceed that, causing IntakeQ to retry and the
  client to receive duplicate emails.
- IntakeQ also retries on non-200 responses, so processing must be
  idempotent.

Flow:
1. Parse and minimally validate the webhook payload.
2. Claim the intake_id with a conditional put on the intakes table.
   A repeat delivery fails the condition and gets a 200 without enqueueing.
3. Enqueue the raw payload to SQS for the intake-worker and return 200.
   If the enqueue fails, the claim is released and a 500 is returned so
   IntakeQ retries.
"""
import json
import os
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

from common.logger import CustomLogger

logger = CustomLogger()

# Environment variables (no fallbacks per Fail Fast policy)
ENV = os.environ['ENV']
INTAKES_TABLE = os.environ['INTAKES_TABLE']
INTAKE_QUEUE_URL = os.environ['INTAKE_QUEUE_URL']

dynamodb = boto3.resource('dynamodb')
intakes_table = dynamodb.Table(INTAKES_TABLE)
sqs = boto3.client('sqs')


def claim_intake(intake_id, client_id):
    """
    Claim an intake_id so each webhook is processed at most once.

    Returns True if this delivery won the claim, False if the intake_id
    was already claimed (duplicate/retried delivery). Other DynamoDB
    errors propagate.
    """
    try:
        intakes_table.put_item(
            Item={
                'intake_id': str(intake_id),
                'client_id': str(client_id),
                'created_at': datetime.utcnow().isoformat(),
                'status': 'QUEUED'
            },
            ConditionExpression='attribute_not_exists(intake_id)'
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False
        raise


def release_claim(intake_id):
    """Delete a claim record so a retried webhook can claim again."""
    intakes_table.delete_item(Key={'intake_id': str(intake_id)})


def lambda_handler(event, context):
    """Main webhook handler: validate, claim, enqueue, return fast."""
    logger.log('INFO', "Received IntakeQ webhook")

    try:
        # Parse webhook payload
        if 'body' in event:
            body = json.loads(event['body'])
        else:
            body = event

        logger.log('DEBUG', "Parsed webhook data", body=body)

        client_id = body.get('ClientId')
        intake_id = body.get('IntakeId')

        if not client_id or not intake_id:
            logger.log('ERROR', "Missing required webhook fields")
            return {'statusCode': 400, 'body': json.dumps('Missing required fields')}

        # Claim the intake_id (idempotency guard against IntakeQ retries)
        if not claim_intake(intake_id, client_id):
            logger.log('INFO', "Duplicate webhook delivery ignored",
                      intake_id=intake_id, client_id=client_id)
            return {'statusCode': 200, 'body': json.dumps('Duplicate webhook ignored')}

        # Hand off to the intake-worker via SQS
        try:
            sqs.send_message(
                QueueUrl=INTAKE_QUEUE_URL,
                MessageBody=json.dumps(body)
            )
        except Exception:
            # Release the claim so IntakeQ's retry can be processed
            release_claim(intake_id)
            raise

        logger.log('INFO', "Intake queued for processing",
                  intake_id=intake_id, client_id=client_id)
        return {'statusCode': 200, 'body': json.dumps('Queued')}

    except Exception as e:
        logger.log('ERROR', "Error processing webhook", error=str(e))
        return {'statusCode': 500, 'body': json.dumps('Internal server error')}
