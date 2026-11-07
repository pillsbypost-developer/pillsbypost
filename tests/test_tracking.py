"""tracking-initiate (admin upload) and tracking-process-entry (Step Functions worker)."""
import json

import pytest


def api_event(body, groups='Administrators', method='POST'):
    return {
        'httpMethod': method,
        'path': '/tracking-upload',
        'body': json.dumps(body) if body is not None else None,
        'requestContext': {'authorizer': {'claims': {
            'sub': 'user-1', 'email': 'admin@test.example.com', 'cognito:groups': groups,
        }}},
    }


def make_order(order_id, client_id, status='NEW'):
    return {'order_id': order_id, 'client_id': client_id, 'status': status, 'fulfillment_mode': 'shipping',
            'approval_date': '2026-11-07', 'client_name': 'Jane Doe'}


@pytest.fixture(scope='module')
def initiate(lambda_module):
    return lambda_module('tracking-initiate')


@pytest.fixture(scope='module')
def process(lambda_module):
    return lambda_module('tracking-process-entry')


@pytest.fixture()
def fake_iq(fake_intakeq):
    return fake_intakeq(clients={
        '200': {'ClientId': 200, 'Email': 'a@example.com', 'Tags': []},
        '201': {'ClientId': 201, 'Email': 'b@example.com', 'Tags': []},
    })


class TestInitiate:
    def test_non_admin_403(self, initiate):
        response = initiate.lambda_handler(api_event({'entries': [{'client_id': '200', 'tracking_number': 'x'}]},
                                                     groups=''), None)
        assert response['statusCode'] == 403

    def test_options(self, initiate):
        assert initiate.lambda_handler({'httpMethod': 'OPTIONS'}, None)['statusCode'] == 200

    @pytest.mark.parametrize('body', [{}, {'entries': []}, {'entries': 'nope'},
                                      {'entries': [{'client_id': '', 'tracking_number': 'x'}]}])
    def test_validation_400(self, initiate, body):
        assert initiate.lambda_handler(api_event(body), None)['statusCode'] == 400

    def test_single_entry_marks_order_shipped(self, initiate, fake_iq, monkeypatch):
        monkeypatch.setattr(initiate, 'intakeq', fake_iq)
        initiate.orders_table.put_item(Item=make_order('o-200', '200'))
        response = initiate.lambda_handler(
            api_event({'entries': [{'client_id': '200', 'tracking_number': '9400111', 'carrier': 'USPS'}]}), None)
        assert response['statusCode'] == 200
        assert json.loads(response['body'])['status'] == 'success'

        order = initiate.orders_table.get_item(Key={'order_id': 'o-200'})['Item']
        assert order['status'] == 'SHIPPED'
        assert order['tracking_number'] == '9400111'
        assert order['carrier'] == 'USPS'
        assert 'Tracking number' in fake_iq.client_updates[-1]['AdditionalInformation']

        records = initiate.tracking_table.scan()['Items']
        assert any(r['client_id'] == '200' and r['tracking_number'] == '9400111' and r['ttl'] > 0 for r in records)

    def test_single_entry_unknown_client_400(self, initiate, fake_iq, monkeypatch):
        monkeypatch.setattr(initiate, 'intakeq', fake_iq)
        response = initiate.lambda_handler(
            api_event({'entries': [{'client_id': '999', 'tracking_number': 'x'}]}), None)
        assert response['statusCode'] == 400

    def test_batch_writes_job_and_starts_execution(self, initiate, fake_iq, monkeypatch):
        started = []

        class FakeSF:
            def start_execution(self, **kwargs):
                started.append(kwargs)

        monkeypatch.setattr(initiate, 'intakeq', fake_iq)
        monkeypatch.setattr(initiate, 'sf_client', FakeSF())
        response = initiate.lambda_handler(api_event({'entries': [
            {'client_id': '200', 'tracking_number': 'a'},
            {'client_id': '201', 'tracking_number': 'b', 'notes': 'n'},
        ]}), None)
        assert response['statusCode'] == 202
        payload = json.loads(response['body'])
        assert payload['total'] == 2
        job = initiate.jobs_table.get_item(Key={'job_id': payload['job_id']})['Item']
        assert job['status'] == 'PENDING'
        assert len(job['entries']) == 2
        assert 'provider' not in job
        assert started[0]['name'] == payload['job_id']
        assert json.loads(started[0]['input'])['job_id'] == payload['job_id']


class TestProcessEntry:
    def test_success_updates_counters(self, process, fake_iq, monkeypatch):
        monkeypatch.setattr(process, 'intakeq', fake_iq)
        process.jobs_table.put_item(Item={'job_id': 'job-1', 'status': 'PROCESSING', 'total': 1,
                                          'succeeded': 0, 'failed': 0, 'results': []})
        process.orders_table.put_item(Item=make_order('o-201', '201'))
        result = process.lambda_handler({'job_id': 'job-1', 'entry': {
            'client_id': '201', 'tracking_number': 'TRK1', 'carrier': 'FedEx', 'ttl': 123}}, None)
        assert result['status'] == 'success'
        job = process.jobs_table.get_item(Key={'job_id': 'job-1'})['Item']
        assert job['succeeded'] == 1 and job['failed'] == 0
        assert job['results'][0]['tracking_number'] == 'TRK1'
        assert process.orders_table.get_item(Key={'order_id': 'o-201'})['Item']['status'] == 'SHIPPED'

    def test_missing_fields_counts_failed(self, process, fake_iq, monkeypatch):
        monkeypatch.setattr(process, 'intakeq', fake_iq)
        process.jobs_table.put_item(Item={'job_id': 'job-2', 'status': 'PROCESSING', 'total': 1,
                                          'succeeded': 0, 'failed': 0, 'results': []})
        result = process.lambda_handler({'job_id': 'job-2', 'entry': {'client_id': '', 'tracking_number': 'x'}}, None)
        assert result['status'] == 'error'
        job = process.jobs_table.get_item(Key={'job_id': 'job-2'})['Item']
        assert job['failed'] == 1

    def test_unknown_client_counts_failed(self, process, fake_iq, monkeypatch):
        monkeypatch.setattr(process, 'intakeq', fake_iq)
        process.jobs_table.put_item(Item={'job_id': 'job-3', 'status': 'PROCESSING', 'total': 1,
                                          'succeeded': 0, 'failed': 0, 'results': []})
        result = process.lambda_handler({'job_id': 'job-3', 'entry': {'client_id': '555', 'tracking_number': 'x'}}, None)
        assert result['status'] == 'error'
        assert process.jobs_table.get_item(Key={'job_id': 'job-3'})['Item']['failed'] == 1
