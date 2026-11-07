"""admin-crud-handler: auth, generic CRUD, order actions."""
import json
import os

import boto3
import pytest


def api_event(method, path, body=None, groups='Administrators', query=None):
    return {
        'httpMethod': method,
        'path': path,
        'body': json.dumps(body) if body is not None else None,
        'queryStringParameters': query,
        'requestContext': {'authorizer': {'claims': {
            'sub': 'user-1', 'email': 'admin@test.example.com', 'cognito:groups': groups,
        }}},
    }


def body_of(response):
    return json.loads(response['body'])


def make_order(order_id, status='NEW', mode='shipping', client_id='100', **extra):
    order = {
        'order_id': order_id, 'client_id': client_id, 'client_name': 'Jane Doe',
        'email': 'jane@example.com', 'shipping_address': '1 Main St', 'city': 'Detroit',
        'state': 'MI', 'postal_code': '48201', 'country': 'USA', 'approval_date': '2026-11-07',
        'fulfillment_mode': mode, 'status': status, 'payment_tier': 'mail_150',
        'payment_amount': '150.00', 'created_at': '2026-11-07T00:00:00+00:00',
    }
    order.update(extra)
    return order


@pytest.fixture(scope='module')
def crud(lambda_module):
    return lambda_module('admin-crud-handler')


@pytest.fixture(scope='module', autouse=True)
def seed(crud):
    table = crud.orders_table
    table.put_item(Item=make_order('o-new-1'))
    table.put_item(Item=make_order('o-new-2', approval_date='2026-11-06'))
    table.put_item(Item=make_order('o-shipped', status='SHIPPED'))
    table.put_item(Item=make_order('o-nectar', mode='nectar', status='NECTAR_ON_HOLD'))
    crud.intakes_table.put_item(Item={'intake_id': 'i-1', 'client_id': '100',
                                      'created_at': '2026-11-01T00:00:00', 'status': 'PROCESSED'})


@pytest.fixture()
def fake_iq(crud, fake_intakeq, monkeypatch):
    fake = fake_intakeq(clients={'100': {'ClientId': 100, 'Email': 'jane@example.com', 'Tags': []}})
    monkeypatch.setattr(crud, 'intakeq', fake)
    return fake


def test_non_admin_is_rejected(crud):
    response = crud.lambda_handler(api_event('GET', '/orders', groups='Providers'), None)
    assert response['statusCode'] == 403
    response = crud.lambda_handler(api_event('GET', '/orders', groups=''), None)
    assert response['statusCode'] == 403


def test_options_preflight(crud):
    response = crud.lambda_handler({'httpMethod': 'OPTIONS', 'path': '/orders'}, None)
    assert response['statusCode'] == 200
    assert response['headers']['Access-Control-Allow-Origin'] == os.environ['CORS_ORIGIN']


def test_unknown_table(crud):
    assert crud.lambda_handler(api_event('GET', '/providers'), None)['statusCode'] == 400


def test_list_orders_by_status(crud):
    response = crud.lambda_handler(api_event('GET', '/orders', query={'status': 'NEW'}), None)
    assert response['statusCode'] == 200
    items = body_of(response)['items']
    assert {o['order_id'] for o in items} == {'o-new-1', 'o-new-2'}
    # newest approval first
    assert items[0]['order_id'] == 'o-new-1'


def test_list_orders_filter_mode(crud):
    response = crud.lambda_handler(api_event('GET', '/orders', query={'fulfillment_mode': 'nectar'}), None)
    items = body_of(response)['items']
    assert [o['order_id'] for o in items] == ['o-nectar']


def test_get_one_order_and_404(crud):
    assert body_of(crud.lambda_handler(api_event('GET', '/orders/o-shipped'), None))['status'] == 'SHIPPED'
    assert crud.lambda_handler(api_event('GET', '/orders/nope'), None)['statusCode'] == 404


def test_payment_tier_create_and_validation(crud):
    tier = {'tier_key': 'mail_150', 'label': 'Mail $150', 'pharmacy_choice': 'mail', 'amount': 150,
            'payment_link': 'https://pay.example.com/150', 'intake_answer_pattern': 'ship to me, $150'}
    response = crud.lambda_handler(api_event('POST', '/payment-tiers', tier), None)
    assert response['statusCode'] == 201
    assert body_of(response)['tier_key'] == 'mail_150'

    bad = dict(tier, tier_key='bad', pharmacy_choice='pickup')
    response = crud.lambda_handler(api_event('POST', '/payment-tiers', bad), None)
    assert response['statusCode'] == 400
    assert 'pharmacy_choice' in body_of(response)['message']

    missing = {k: v for k, v in tier.items() if k != 'payment_link'}
    missing['tier_key'] = 'missing'
    assert crud.lambda_handler(api_event('POST', '/payment-tiers', missing), None)['statusCode'] == 400


def test_eligibility_rule_create_uppercases_state(crud):
    response = crud.lambda_handler(
        api_event('POST', '/eligibility-rules', {'state': 'mi', 'served': True, 'min_age': 18}), None)
    assert response['statusCode'] == 201
    assert body_of(response)['state'] == 'MI'
    response = crud.lambda_handler(api_event('POST', '/eligibility-rules', {'state': 'Mich', 'served': True}), None)
    assert response['statusCode'] == 400


def test_read_only_table_rejects_writes(crud):
    assert crud.lambda_handler(api_event('POST', '/intakes', {'intake_id': 'x'}), None)['statusCode'] == 405
    assert crud.lambda_handler(api_event('DELETE', '/tracking-jobs/j1'), None)['statusCode'] == 405
    assert crud.lambda_handler(api_event('GET', '/intakes'), None)['statusCode'] == 200


def test_export_batch_marks_exported_and_returns_csv(crud):
    response = crud.lambda_handler(
        api_event('POST', '/orders/export-batch', {'order_ids': ['o-new-2', 'o-shipped', 'o-nectar', 'ghost']}), None)
    assert response['statusCode'] == 200
    payload = body_of(response)
    assert payload['exported_count'] == 1
    assert {s['order_id'] for s in payload['skipped']} == {'o-shipped', 'o-nectar', 'ghost'}
    lines = payload['csv'].splitlines()
    assert lines[0] == ','.join(crud.EXPORT_CSV_COLUMNS)
    assert lines[1].startswith('100,Jane Doe,jane@example.com,1 Main St')
    assert '150.00' in lines[1]

    exported = crud.orders_table.get_item(Key={'order_id': 'o-new-2'})['Item']
    assert exported['status'] == 'EXPORTED'
    assert exported['export_batch_id'] == payload['export_batch_id']
    assert exported['ttl'] > 0


def test_export_batch_requires_ids(crud):
    assert crud.lambda_handler(api_event('POST', '/orders/export-batch', {'order_ids': []}), None)['statusCode'] == 400


def test_mark_paid_tags_and_updates(crud, fake_iq):
    response = crud.lambda_handler(api_event('POST', '/orders/o-new-1/mark-paid', {'amount': 150}), None)
    assert response['statusCode'] == 200
    assert fake_iq.tags_added == ['PAID', '150.00']
    order = crud.orders_table.get_item(Key={'order_id': 'o-new-1'})['Item']
    assert order['payment_status'] == 'PAID'
    assert order['payment_amount'] == '150.00'
    intake = crud.intakes_table.get_item(Key={'intake_id': 'i-1'})['Item']
    assert intake['payment_status'] == 'PAID'
    assert intake['paid_amount'] == '150.00'


def test_mark_paid_bad_amount(crud, fake_iq):
    assert crud.lambda_handler(api_event('POST', '/orders/o-new-1/mark-paid', {'amount': 'lots'}), None)['statusCode'] == 400


def test_resubmit_nectar_queues_message(crud):
    sqs = boto3.client('sqs')
    url = os.environ['NECTAR_ORDER_QUEUE_URL']
    response = crud.lambda_handler(api_event('POST', '/orders/o-nectar/resubmit-nectar'), None)
    assert response['statusCode'] == 202
    message = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=1)['Messages'][0]
    assert json.loads(message['Body'])['order_id'] == 'o-nectar'
    sqs.delete_message(QueueUrl=url, ReceiptHandle=message['ReceiptHandle'])

    # shipping orders cannot be resubmitted
    assert crud.lambda_handler(api_event('POST', '/orders/o-new-1/resubmit-nectar'), None)['statusCode'] == 400


def test_cancel_order(crud):
    crud.orders_table.put_item(Item=make_order('o-cancel'))
    response = crud.lambda_handler(api_event('POST', '/orders/o-cancel/cancel'), None)
    assert response['statusCode'] == 200
    assert crud.orders_table.get_item(Key={'order_id': 'o-cancel'})['Item']['status'] == 'CANCELED'


def test_cancel_submitted_nectar_calls_honeybee(crud, monkeypatch):
    calls = []

    class FakeHoneybee:
        def cancel_order(self, order_number):
            calls.append(order_number)

    monkeypatch.setattr(crud, 'honeybee', FakeHoneybee())
    crud.orders_table.put_item(Item=make_order('o-hb', mode='nectar', status='SUBMITTED_TO_NECTAR',
                                               honeybee_order_number='HB123'))
    response = crud.lambda_handler(api_event('POST', '/orders/o-hb/cancel'), None)
    assert response['statusCode'] == 200
    assert calls == ['HB123']


def test_mark_shipped_records_tracking(crud, fake_iq):
    crud.orders_table.put_item(Item=make_order('o-ship-me', client_id='100'))
    response = crud.lambda_handler(
        api_event('POST', '/orders/o-ship-me/mark-shipped', {'tracking_number': '1Z999', 'carrier': 'UPS'}), None)
    assert response['statusCode'] == 200
    updated = crud.orders_table.get_item(Key={'order_id': 'o-ship-me'})['Item']
    assert updated['status'] == 'SHIPPED'
    assert updated['tracking_number'] == '1Z999'
    assert 'Tracking number' in fake_iq.client_updates[-1]['AdditionalInformation']

    assert crud.lambda_handler(api_event('POST', '/orders/o-ship-me/mark-shipped', {}), None)['statusCode'] == 400


def test_unknown_action_404(crud):
    assert crud.lambda_handler(api_event('POST', '/orders/o-new-1/explode'), None)['statusCode'] == 404
