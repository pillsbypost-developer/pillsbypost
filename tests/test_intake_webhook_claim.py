"""
intake-webhook claim/enqueue behavior: conditional-put idempotency, SQS
handoff, and claim release when the enqueue fails.
"""
import json

import boto3
import pytest


@pytest.fixture(scope='module')
def webhook(lambda_module):
    return lambda_module('intake-webhook')


def make_event(intake_id, client_id=100):
    return {'body': json.dumps({'ClientId': client_id, 'IntakeId': intake_id,
                                'EventType': 'Intake Submitted'})}


def queue_depth(queue_url):
    sqs = boto3.client('sqs')
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=['ApproximateNumberOfMessages'])
    return int(attrs['Attributes']['ApproximateNumberOfMessages'])


def test_first_delivery_claims_and_enqueues(webhook):
    before = queue_depth(webhook.INTAKE_QUEUE_URL)
    response = webhook.lambda_handler(make_event('intake-first'), None)

    assert response['statusCode'] == 200
    assert 'Queued' in response['body']
    assert queue_depth(webhook.INTAKE_QUEUE_URL) == before + 1

    record = webhook.intakes_table.get_item(Key={'intake_id': 'intake-first'})['Item']
    assert record['status'] == 'QUEUED'
    assert record['client_id'] == '100'


def test_duplicate_delivery_ignored_without_enqueueing(webhook):
    webhook.lambda_handler(make_event('intake-dup'), None)
    before = queue_depth(webhook.INTAKE_QUEUE_URL)

    response = webhook.lambda_handler(make_event('intake-dup'), None)

    assert response['statusCode'] == 200
    assert 'Duplicate' in response['body']
    assert queue_depth(webhook.INTAKE_QUEUE_URL) == before


def test_missing_fields_rejected(webhook):
    response = webhook.lambda_handler({'body': json.dumps({'ClientId': 100})}, None)
    assert response['statusCode'] == 400


def test_direct_invocation_format_supported(webhook):
    """Webhook payload without API Gateway 'body' wrapper."""
    response = webhook.lambda_handler(
        {'ClientId': 100, 'IntakeId': 'intake-direct'}, None)
    assert response['statusCode'] == 200


def test_enqueue_failure_releases_claim_for_retry(webhook, monkeypatch):
    class ExplodingSQS:
        def send_message(self, **kwargs):
            raise RuntimeError('SQS unavailable')

    monkeypatch.setattr(webhook, 'sqs', ExplodingSQS())
    response = webhook.lambda_handler(make_event('intake-sqs-fail'), None)
    assert response['statusCode'] == 500

    # Claim must be released so IntakeQ's retry can be processed
    assert 'Item' not in webhook.intakes_table.get_item(
        Key={'intake_id': 'intake-sqs-fail'})

    monkeypatch.undo()
    response = webhook.lambda_handler(make_event('intake-sqs-fail'), None)
    assert response['statusCode'] == 200
    assert 'Queued' in response['body']
