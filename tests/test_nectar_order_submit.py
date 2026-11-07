"""nectar-order-submit: SQS consumer -> Honeybee POST /orders."""
import json
import os

import boto3
import pytest
import requests

from common.honeybee_client import HoneybeeError


class FakeHoneybee:
    def __init__(self):
        self.orders = []
        self.error = None
        self.prescriptions = []
        self.next_order_number = 'MXO72E'

    def get_active_prescription_ids(self, patient_id):
        return list(self.prescriptions)

    def create_order(self, patient_id, prescription_ids, shipping_method_id, address=None, signature_confirmation=None):
        if self.error:
            raise self.error
        self.orders.append({'patient_id': patient_id, 'prescription_ids': prescription_ids,
                            'shipping_method_id': shipping_method_id, 'address': address})
        return {'order_number': self.next_order_number, 'status_name': 'Processing'}


@pytest.fixture(scope='module')
def submit(lambda_module):
    return lambda_module('nectar-order-submit')


@pytest.fixture()
def env(submit, fake_intakeq, monkeypatch):
    iq = fake_intakeq(clients={700: {'ClientId': 700, 'Tags': []}})
    hb = FakeHoneybee()
    alerts = []
    monkeypatch.setattr(submit, 'intakeq', iq)
    monkeypatch.setattr(submit, 'honeybee', hb)
    monkeypatch.setattr(submit.email_service, 'send_admin_alert', lambda s, b, h=None: alerts.append((s, b)))
    submit.settings.clear_cache()
    return iq, hb, alerts


def put_order(submit, order_id, **extra):
    item = {'order_id': order_id, 'client_id': '700', 'status': 'NEW', 'fulfillment_mode': 'nectar',
            'approval_date': '2026-11-07', 'client_name': 'Nectar Persona', 'shipping_address': '1 Main St',
            'unit_number': '2B', 'city': 'Detroit', 'state': 'MI', 'postal_code': '48201',
            'honeybee_patient_id': '700', 'honeybee_prescription_ids': [183601], **extra}
    submit.orders_table.put_item(Item=item)


def get_order(submit, order_id):
    return submit.orders_table.get_item(Key={'order_id': order_id}).get('Item')


def sqs_event(order_id):
    return {'Records': [{'body': json.dumps({'order_id': order_id})}]}


def test_submits_and_marks_order(submit, env):
    iq, hb, alerts = env
    put_order(submit, 'n-1')
    submit.lambda_handler(sqs_event('n-1'), None)
    order = get_order(submit, 'n-1')
    assert order['status'] == 'SUBMITTED_TO_NECTAR'
    assert order['honeybee_order_number'] == 'MXO72E'
    assert 'ttl' in order and 'nectar_submitted_at' in order
    assert hb.orders == [{'patient_id': '700', 'prescription_ids': [183601], 'shipping_method_id': '7',
                          'address': {'first_name': 'Nectar', 'last_name': 'Persona', 'street1': '1 Main St',
                                      'street2': '2B', 'city': 'Detroit', 'state': 'MI', 'zip': '48201'}}]
    assert iq.tags_added == ['NECTAR-SUBMITTED']
    assert alerts == []


def test_already_submitted_is_skipped(submit, env):
    iq, hb, alerts = env
    put_order(submit, 'n-2', status='SUBMITTED_TO_NECTAR', honeybee_order_number='OLD1')
    submit.lambda_handler(sqs_event('n-2'), None)
    assert hb.orders == []
    assert get_order(submit, 'n-2')['honeybee_order_number'] == 'OLD1'


def test_non_submittable_status_skipped(submit, env):
    iq, hb, alerts = env
    put_order(submit, 'n-3', status='CANCELED')
    submit.lambda_handler(sqs_event('n-3'), None)
    assert hb.orders == []


def test_missing_order_dropped_without_error(submit, env):
    iq, hb, alerts = env
    submit.lambda_handler(sqs_event('does-not-exist'), None)
    assert hb.orders == []


def test_prescriptions_fetched_when_not_on_order(submit, env):
    iq, hb, alerts = env
    put_order(submit, 'n-4', honeybee_prescription_ids=[])
    hb.prescriptions = [77, 78]
    submit.lambda_handler(sqs_event('n-4'), None)
    assert hb.orders[0]['prescription_ids'] == [77, 78]
    assert [int(p) for p in get_order(submit, 'n-4')['honeybee_prescription_ids']] == [77, 78]


def test_no_prescription_puts_on_hold(submit, env):
    iq, hb, alerts = env
    put_order(submit, 'n-5', honeybee_prescription_ids=[])
    submit.lambda_handler(sqs_event('n-5'), None)
    order = get_order(submit, 'n-5')
    assert order['status'] == 'NECTAR_ON_HOLD'
    assert 'No active prescription' in order['honeybee_error']
    assert len(alerts) == 1
    assert hb.orders == []


def test_honeybee_4xx_puts_on_hold_and_consumes(submit, env):
    iq, hb, alerts = env
    put_order(submit, 'n-6')
    hb.error = HoneybeeError(422, '{"error":"Invalid address"}', 'orders')
    submit.lambda_handler(sqs_event('n-6'), None)
    order = get_order(submit, 'n-6')
    assert order['status'] == 'NECTAR_ON_HOLD'
    assert 'Invalid address' in order['honeybee_error']
    assert len(alerts) == 1


def test_honeybee_5xx_raises_for_retry(submit, env):
    iq, hb, alerts = env
    put_order(submit, 'n-7')
    hb.error = HoneybeeError(503, 'down', 'orders')
    with pytest.raises(HoneybeeError):
        submit.lambda_handler(sqs_event('n-7'), None)
    assert get_order(submit, 'n-7')['status'] == 'NEW'


def test_transport_error_raises_for_retry(submit, env):
    iq, hb, alerts = env
    put_order(submit, 'n-8')
    hb.error = requests.ConnectionError('timeout')
    with pytest.raises(requests.RequestException):
        submit.lambda_handler(sqs_event('n-8'), None)


def test_on_hold_order_can_be_requeued(submit, env):
    iq, hb, alerts = env
    put_order(submit, 'n-9', status='NECTAR_ON_HOLD', honeybee_error='old failure')
    submit.lambda_handler(sqs_event('n-9'), None)
    order = get_order(submit, 'n-9')
    assert order['status'] == 'SUBMITTED_TO_NECTAR'
    assert 'honeybee_error' not in order


def test_missing_shipping_method_setting_raises(submit, env):
    iq, hb, alerts = env
    table = boto3.resource('dynamodb').Table(os.environ['SETTINGS_TABLE'])
    table.put_item(Item={'config_key': 'honeybee_shipping_method_id', 'value': ''})
    submit.settings.clear_cache()
    put_order(submit, 'n-10')
    with pytest.raises(ValueError):
        submit.lambda_handler(sqs_event('n-10'), None)
    table.put_item(Item={'config_key': 'honeybee_shipping_method_id', 'value': '7'})
    submit.settings.clear_cache()
