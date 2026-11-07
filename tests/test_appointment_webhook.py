"""appointment-webhook: approval -> order record, fulfillment_mode routing, tags, cancel."""
import json
import os

import boto3
import pytest

from common.honeybee_client import HoneybeeError


class FakeHoneybee:
    def __init__(self, prescriptions=None, error=None):
        self.prescriptions = prescriptions or []
        self.error = error
        self.canceled = []
        self.cancel_error = None

    def get_active_prescription_ids(self, patient_id):
        if self.error:
            raise self.error
        return list(self.prescriptions)

    def cancel_order(self, order_number):
        if self.cancel_error:
            raise self.cancel_error
        self.canceled.append(order_number)


class FakeSQS:
    def __init__(self):
        self.messages = []

    def send_message(self, QueueUrl, MessageBody):
        self.messages.append(json.loads(MessageBody))


@pytest.fixture(scope='module')
def appt(lambda_module):
    return lambda_module('appointment-webhook')


@pytest.fixture()
def env(appt, fake_intakeq, monkeypatch):
    client = {
        'ClientId': 500, 'FirstName': 'Test', 'LastName': 'Persona', 'Email': 'tp@example.com',
        'DateOfBirth': 631152000000, 'StreetAddress': '1 Main St', 'UnitNumber': '2B',
        'City': 'Detroit', 'StateShort': 'MI', 'PostalCode': '48201', 'Tags': ['PAID', '150'],
    }
    iq = fake_intakeq(clients={500: client})
    hb = FakeHoneybee()
    sqs = FakeSQS()
    alerts = []
    monkeypatch.setattr(appt, 'intakeq', iq)
    monkeypatch.setattr(appt, 'honeybee', hb)
    monkeypatch.setattr(appt, 'sqs', sqs)
    monkeypatch.setattr(appt.email_service, 'send_admin_alert', lambda s, b, h=None: alerts.append(s))
    appt.settings.clear_cache()
    return iq, hb, sqs, alerts


def set_setting(key, value):
    boto3.resource('dynamodb').Table(os.environ['SETTINGS_TABLE']).put_item(
        Item={'config_key': key, 'value': value})


def make_event(appointment_id, event_type='AppointmentCreated', service_id='svc-mail',
               practitioner_id='prac-1', client_id=500):
    return {'body': json.dumps({
        'EventType': event_type, 'ClientId': client_id,
        'Appointment': {'Id': appointment_id, 'ClientId': client_id, 'ServiceId': service_id,
                        'ServiceName': 'Mail order', 'PractitionerId': practitioner_id,
                        'ClientName': 'Test Persona', 'ClientEmail': 'tp@example.com'},
    })}


def get_order(appt, order_id):
    return appt.orders_table.get_item(Key={'order_id': order_id}).get('Item')


def test_shipping_order_created_with_sent_tag(appt, env):
    iq, hb, sqs, alerts = env
    set_setting('fulfillment_mode', 'shipping'); appt.settings.clear_cache()
    response = appt.lambda_handler(make_event('appt-ship-1'), None)
    assert response['statusCode'] == 200
    order = get_order(appt, 'appt-ship-1')
    assert order['status'] == 'NEW'
    assert order['fulfillment_mode'] == 'shipping'
    assert order['client_id'] == '500'
    assert order['dob'] == '1990-01-01'
    assert order['postal_code'] == '48201'
    assert float(order['payment_amount']) == 150.0  # from numeric tag
    assert 'honeybee_patient_id' not in order
    assert iq.tags_added == ['SENT']
    assert 'PAID' not in iq.tags_removed
    assert sqs.messages == []


def test_payment_from_latest_intake_record(appt, env):
    iq, hb, sqs, alerts = env
    appt.intakes_table.put_item(Item={'intake_id': 'i-old', 'client_id': '500', 'created_at': '2026-01-01T00:00:00',
                                      'payment_amount': 100, 'payment_tier': 'mail_100'})
    appt.intakes_table.put_item(Item={'intake_id': 'i-new', 'client_id': '500', 'created_at': '2026-02-01T00:00:00',
                                      'payment_amount': 125, 'payment_tier': 'mail_125'})
    appt.lambda_handler(make_event('appt-ship-2'), None)
    order = get_order(appt, 'appt-ship-2')
    assert float(order['payment_amount']) == 125.0
    assert order['payment_tier'] == 'mail_125'
    appt.intakes_table.delete_item(Key={'intake_id': 'i-old'})
    appt.intakes_table.delete_item(Key={'intake_id': 'i-new'})


def test_local_pharmacy_by_service_id(appt, env):
    iq, hb, sqs, alerts = env
    response = appt.lambda_handler(make_event('appt-local-1', service_id='svc-local'), None)
    assert response['statusCode'] == 200
    order = get_order(appt, 'appt-local-1')
    assert order['fulfillment_mode'] == 'local_pharmacy'
    assert order['status'] == 'LOCAL_PHARMACY'
    assert iq.tags_added == ['LOCAL-PHARMACY']


def test_nectar_order_queued_when_prescription_exists(appt, env):
    iq, hb, sqs, alerts = env
    set_setting('fulfillment_mode', 'nectar'); appt.settings.clear_cache()
    hb.prescriptions = [183601]
    response = appt.lambda_handler(make_event('appt-nectar-1'), None)
    assert response['statusCode'] == 200
    order = get_order(appt, 'appt-nectar-1')
    assert order['fulfillment_mode'] == 'nectar'
    assert order['honeybee_patient_id'] == '500'
    assert [int(p) for p in order['honeybee_prescription_ids']] == [183601]
    assert sqs.messages == [{'order_id': 'appt-nectar-1'}]
    assert iq.tags_added == ['NECTAR']
    set_setting('fulfillment_mode', 'shipping'); appt.settings.clear_cache()


def test_nectar_order_waits_for_rx_when_patient_unknown(appt, env):
    iq, hb, sqs, alerts = env
    set_setting('fulfillment_mode', 'nectar'); appt.settings.clear_cache()
    hb.error = HoneybeeError(404, 'not found', 'patients/500/rx')
    response = appt.lambda_handler(make_event('appt-nectar-2'), None)
    assert response['statusCode'] == 200
    order = get_order(appt, 'appt-nectar-2')
    assert order['status'] == 'NEW'
    assert 'honeybee_prescription_ids' not in order
    assert sqs.messages == []
    set_setting('fulfillment_mode', 'shipping'); appt.settings.clear_cache()


def test_duplicate_delivery_does_not_recreate_or_retag(appt, env):
    iq, hb, sqs, alerts = env
    appt.lambda_handler(make_event('appt-dup'), None)
    iq.tags_added.clear()
    response = appt.lambda_handler(make_event('appt-dup'), None)
    assert response['statusCode'] == 200
    assert 'Duplicate' in response['body']
    assert iq.tags_added == []


def test_wrong_practitioner_ignored(appt, env):
    iq, hb, sqs, alerts = env
    response = appt.lambda_handler(make_event('appt-wrong-prac', practitioner_id='prac-other'), None)
    assert response['statusCode'] == 200
    assert get_order(appt, 'appt-wrong-prac') is None
    assert iq.tags_added == []


def test_client_not_found_400(appt, env):
    response = appt.lambda_handler(make_event('appt-no-client', client_id=999), None)
    assert response['statusCode'] == 400
    assert get_order(appt, 'appt-no-client') is None


def test_tag_failure_500(appt, env):
    iq, hb, sqs, alerts = env
    iq.fail_tags = True
    response = appt.lambda_handler(make_event('appt-tagfail'), None)
    assert response['statusCode'] == 500
    # order was written before the tag step; redelivery is a no-op
    assert get_order(appt, 'appt-tagfail') is not None


def test_non_created_event_ignored(appt, env):
    response = appt.lambda_handler(make_event('appt-resched', event_type='AppointmentRescheduled'), None)
    assert response['statusCode'] == 200
    assert get_order(appt, 'appt-resched') is None


def test_missing_fields_400(appt, env):
    assert appt.lambda_handler({'body': json.dumps({'EventType': 'AppointmentCreated'})}, None)['statusCode'] == 400
    assert appt.lambda_handler({'body': json.dumps({'EventType': 'AppointmentCreated',
                                                    'Appointment': {'Id': 'x'}})}, None)['statusCode'] == 400


def test_cancel_marks_order_canceled_and_cancels_honeybee(appt, env):
    iq, hb, sqs, alerts = env
    appt.lambda_handler(make_event('appt-cancel-1'), None)
    appt.orders_table.update_item(
        Key={'order_id': 'appt-cancel-1'},
        UpdateExpression='SET #s = :s, honeybee_order_number = :n',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'SUBMITTED_TO_NECTAR', ':n': 'MXO72E'})
    response = appt.lambda_handler(make_event('appt-cancel-1', event_type='AppointmentCanceled'), None)
    assert response['statusCode'] == 200
    order = get_order(appt, 'appt-cancel-1')
    assert order['status'] == 'CANCELED'
    assert order['cancel_reason'] == 'AppointmentCanceled'
    assert hb.canceled == ['MXO72E']
    assert alerts == []


def test_cancel_when_honeybee_refuses_alerts_admin(appt, env):
    iq, hb, sqs, alerts = env
    appt.lambda_handler(make_event('appt-cancel-2'), None)
    appt.orders_table.update_item(
        Key={'order_id': 'appt-cancel-2'},
        UpdateExpression='SET #s = :s, honeybee_order_number = :n',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'SUBMITTED_TO_NECTAR', ':n': 'LOCKED1'})
    hb.cancel_error = HoneybeeError(422, 'already filled', 'orders/LOCKED1')
    response = appt.lambda_handler(make_event('appt-cancel-2', event_type='AppointmentDeleted'), None)
    assert response['statusCode'] == 200
    assert get_order(appt, 'appt-cancel-2')['status'] == 'CANCELED'
    assert len(alerts) == 1 and 'cancel failed' in alerts[0].lower()


def test_cancel_unknown_or_shipped_order_is_noop(appt, env):
    iq, hb, sqs, alerts = env
    assert appt.lambda_handler(make_event('nope', event_type='AppointmentCanceled'), None)['statusCode'] == 200
    appt.lambda_handler(make_event('appt-shipped'), None)
    appt.orders_table.update_item(Key={'order_id': 'appt-shipped'}, UpdateExpression='SET #s = :s',
                                  ExpressionAttributeNames={'#s': 'status'},
                                  ExpressionAttributeValues={':s': 'SHIPPED'})
    appt.lambda_handler(make_event('appt-shipped', event_type='AppointmentCanceled'), None)
    assert get_order(appt, 'appt-shipped')['status'] == 'SHIPPED'
