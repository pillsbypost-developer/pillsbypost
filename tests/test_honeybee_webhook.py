"""honeybee-webhook: signature, dedupe, RX_RECEIVED, SHIPMENT_UPDATED, ORDER_EXCEPTION."""
import json
import os

import boto3
import pytest

from common.honeybee_client import HoneybeeClient, HoneybeeError

CLIENT_SECRET = 'hb-secret'  # from the moto secret in conftest
URL = 'https://abc.execute-api.us-east-2.amazonaws.com/test/honeybee/tok'


class FakeHoneybee:
    """Signature verification delegates to the real algorithm with the test secret."""

    def __init__(self):
        self.patients = {}
        self.patient_error = None

    def verify_signature(self, signature_header, method, url, body):
        return signature_header.strip() == HoneybeeClient.compute_signature(CLIENT_SECRET, method, url, body).strip()

    def verify_secret_header(self, header, webhook_secret):
        return header.strip() == HoneybeeClient.compute_secret_header(CLIENT_SECRET, webhook_secret).strip()

    def get_patient(self, patient_id):
        if self.patient_error:
            raise self.patient_error
        return self.patients.get(str(patient_id))


class FakeSQS:
    def __init__(self):
        self.messages = []

    def send_message(self, QueueUrl, MessageBody):
        self.messages.append(json.loads(MessageBody))


@pytest.fixture(scope='module')
def hook(lambda_module):
    return lambda_module('honeybee-webhook')


@pytest.fixture()
def env(hook, fake_intakeq, monkeypatch):
    client = {'ClientId': 700, 'FirstName': 'Nectar', 'LastName': 'Persona', 'Email': 'np@example.com',
              'Tags': []}
    iq = fake_intakeq(clients={700: client})
    hb = FakeHoneybee()
    sqs = FakeSQS()
    alerts = []
    shipped_emails = []
    monkeypatch.setattr(hook, 'intakeq', iq)
    monkeypatch.setattr(hook, 'honeybee', hb)
    monkeypatch.setattr(hook, 'sqs', sqs)
    monkeypatch.setattr(hook.email_service, 'send_admin_alert', lambda s, b, h=None: alerts.append((s, b)))
    monkeypatch.setattr(hook.email_service, 'send_order_shipped_email',
                        lambda c, t, carrier=None: shipped_emails.append((c['ClientId'], t, carrier)))
    hook.settings.clear_cache()
    return iq, hb, sqs, alerts, shipped_emails


def signed_event(payload, signature=None, extra_headers=None):
    body = json.dumps(payload)
    sig = signature if signature is not None else HoneybeeClient.compute_signature(CLIENT_SECRET, 'POST', URL, body)
    headers = {'x-honeybee-signature': sig, 'Content-Type': 'application/json'}
    headers.update(extra_headers or {})
    return {
        'body': body,
        'headers': headers,
        'requestContext': {'domainName': 'abc.execute-api.us-east-2.amazonaws.com', 'path': '/test/honeybee/tok'},
        'path': '/honeybee/tok',
    }


def put_order(hook, order_id, client_id='700', status='NEW', mode='nectar', **extra):
    item = {'order_id': order_id, 'client_id': client_id, 'status': status, 'fulfillment_mode': mode,
            'approval_date': extra.pop('approval_date', '2026-11-07'), 'client_name': 'Nectar Persona', **extra}
    hook.orders_table.put_item(Item=item)
    return item


def get_order(hook, order_id):
    return hook.orders_table.get_item(Key={'order_id': order_id}).get('Item')


def get_event(hook, event_id):
    return hook.honeybee_events_table.get_item(Key={'event_id': event_id}).get('Item')


_counter = {'n': 0}


def eid():
    _counter['n'] += 1
    return f"evt-{_counter['n']}"


def test_invalid_signature_401(hook, env):
    response = hook.lambda_handler(signed_event({'event_id': eid(), 'event_type': 'RX_RECEIVED',
                                                 'patient_id': '700'}, signature='bogus'), None)
    assert response['statusCode'] == 401
    response = hook.lambda_handler({'body': '{}', 'headers': {}, 'path': '/honeybee/tok'}, None)
    assert response['statusCode'] == 401


def test_secret_header_accepted_when_configured(hook, env):
    boto3.client('secretsmanager').put_secret_value(
        SecretId=os.environ['HONEYBEE_WEBHOOK_SECRET'], SecretString=json.dumps({'signing_secret': 'hook-secret'}))
    event_id = eid()
    event = signed_event({'event_id': event_id, 'event_type': 'NOISE', 'patient_id': '700'}, signature='bogus',
                         extra_headers={'X-Honeybee-Secret': HoneybeeClient.compute_secret_header(CLIENT_SECRET, 'hook-secret')})
    response = hook.lambda_handler(event, None)
    assert response['statusCode'] == 200
    assert get_event(hook, event_id)['processed'] is True


def test_duplicate_event_ignored(hook, env):
    event_id = eid()
    payload = {'event_id': event_id, 'event_type': 'NOISE', 'patient_id': '700'}
    assert hook.lambda_handler(signed_event(payload), None)['statusCode'] == 200
    response = hook.lambda_handler(signed_event(payload), None)
    assert response['statusCode'] == 200
    assert 'Duplicate' in response['body']


def test_missing_event_fields_400(hook, env):
    assert hook.lambda_handler(signed_event({'patient_id': '700'}), None)['statusCode'] == 400


def test_rx_received_attaches_and_queues(hook, env):
    iq, hb, sqs, alerts, shipped = env
    put_order(hook, 'ord-rx-1', honeybee_prescription_ids=[1])
    event_id = eid()
    payload = {'event_id': event_id, 'event_type': 'RX_RECEIVED', 'patient_id': '700',
               'medication_requests': [{'prescription_id': 183601, 'drug_name': 'X'}, {'id': 1}]}
    response = hook.lambda_handler(signed_event(payload), None)
    assert response['statusCode'] == 200
    order = get_order(hook, 'ord-rx-1')
    assert [int(p) for p in order['honeybee_prescription_ids']] == [1, 183601]
    assert order['honeybee_patient_id'] == '700'
    assert sqs.messages == [{'order_id': 'ord-rx-1'}]
    stored = get_event(hook, event_id)
    assert stored['processed'] is True and stored['order_id'] == 'ord-rx-1'
    hook.orders_table.delete_item(Key={'order_id': 'ord-rx-1'})


def test_rx_received_automatic_workflow_does_not_queue(hook, env):
    iq, hb, sqs, alerts, shipped = env
    boto3.resource('dynamodb').Table(os.environ['SETTINGS_TABLE']).put_item(
        Item={'config_key': 'honeybee_workflow', 'value': 'automatic'})
    hook.settings.clear_cache()
    put_order(hook, 'ord-rx-auto')
    hook.lambda_handler(signed_event({'event_id': eid(), 'event_type': 'RX_RECEIVED', 'patient_id': '700',
                                      'medication_requests': [{'prescription_id': 5}]}), None)
    assert sqs.messages == []
    assert [int(p) for p in get_order(hook, 'ord-rx-auto')['honeybee_prescription_ids']] == [5]
    boto3.resource('dynamodb').Table(os.environ['SETTINGS_TABLE']).put_item(
        Item={'config_key': 'honeybee_workflow', 'value': 'partner_requested'})
    hook.settings.clear_cache()
    hook.orders_table.delete_item(Key={'order_id': 'ord-rx-auto'})


def test_rx_received_resolves_client_by_email_when_patient_id_unknown(hook, env):
    iq, hb, sqs, alerts, shipped = env
    hb.patients['hb-xyz'] = {'email': 'NP@example.com'}
    put_order(hook, 'ord-rx-email')
    hook.lambda_handler(signed_event({'event_id': eid(), 'event_type': 'RX_RECEIVED', 'patient_id': 'hb-xyz',
                                      'medication_requests': [{'prescription_id': 9}]}), None)
    order = get_order(hook, 'ord-rx-email')
    assert order['honeybee_patient_id'] == 'hb-xyz'
    assert sqs.messages == [{'order_id': 'ord-rx-email'}]
    hook.orders_table.delete_item(Key={'order_id': 'ord-rx-email'})


def test_rx_received_without_order_is_stored_only(hook, env):
    iq, hb, sqs, alerts, shipped = env
    hb.patient_error = HoneybeeError(404, 'nope', 'patients/zzz')
    event_id = eid()
    response = hook.lambda_handler(signed_event({'event_id': event_id, 'event_type': 'RX_RECEIVED',
                                                 'patient_id': 'zzz', 'medication_requests': []}), None)
    assert response['statusCode'] == 200
    assert get_event(hook, event_id)['processed'] is True
    assert sqs.messages == []


def test_shipment_updated_records_tracking_and_marks_shipped(hook, env):
    iq, hb, sqs, alerts, shipped = env
    put_order(hook, 'ord-ship-1', status='SUBMITTED_TO_NECTAR', honeybee_order_number='MXO72E')
    payload = {'event_id': eid(), 'event_type': 'SHIPMENT_UPDATED', 'patient_id': '700', 'order_number': 'MXO72E',
               'shipment': {'tracking_no': '9405511298370701053281', 'carrier': 'USPS',
                            'tracking_url': 'https://tools.usps.com/x'}}
    response = hook.lambda_handler(signed_event(payload), None)
    assert response['statusCode'] == 200
    order = get_order(hook, 'ord-ship-1')
    assert order['status'] == 'SHIPPED'
    assert order['tracking_number'] == '9405511298370701053281'
    assert order['carrier'] == 'USPS'
    assert 'SHIPPED' in iq.tags_added
    assert shipped == [(700, '9405511298370701053281', 'USPS')]
    assert any('Tracking number' in u.get('AdditionalInformation', '') for u in iq.client_updates)
    tracking = hook.tracking_table.scan()['Items']
    assert any(t['tracking_number'] == '9405511298370701053281' and t['client_id'] == '700' for t in tracking)
    hook.orders_table.delete_item(Key={'order_id': 'ord-ship-1'})


def test_shipment_nested_in_dispense_and_delivered(hook, env):
    iq, hb, sqs, alerts, shipped = env
    put_order(hook, 'ord-ship-2', status='SUBMITTED_TO_NECTAR')
    payload = {'event_id': eid(), 'event_type': 'RX_UPDATED', 'patient_id': '700',
               'medication_requests': [{'prescription_id': 1, 'medication_dispenses': [
                   {'order_number': 'NEWNUM', 'shipment': {'tracking_no': 'TRK2', 'carrier': 'UPS',
                                                           'actual_delivery_date': '2026-11-09T01:00:00Z'}}]}]}
    hook.lambda_handler(signed_event(payload), None)
    order = get_order(hook, 'ord-ship-2')
    assert order['status'] == 'DELIVERED'
    assert order['tracking_number'] == 'TRK2'
    assert order['honeybee_order_number'] == 'NEWNUM'
    hook.orders_table.delete_item(Key={'order_id': 'ord-ship-2'})


def test_shipment_update_without_tracking_is_noop(hook, env):
    iq, hb, sqs, alerts, shipped = env
    put_order(hook, 'ord-ship-3', status='SUBMITTED_TO_NECTAR')
    event_id = eid()
    hook.lambda_handler(signed_event({'event_id': event_id, 'event_type': 'SHIPMENT_UPDATED', 'patient_id': '700',
                                      'shipment': {'carrier': 'USPS'}}), None)
    assert get_order(hook, 'ord-ship-3')['status'] == 'SUBMITTED_TO_NECTAR'
    assert get_event(hook, event_id)['processed'] is True
    hook.orders_table.delete_item(Key={'order_id': 'ord-ship-3'})


def test_order_exception_puts_on_hold_and_alerts(hook, env):
    iq, hb, sqs, alerts, shipped = env
    put_order(hook, 'ord-exc-1', status='SUBMITTED_TO_NECTAR', honeybee_order_number='EXC1')
    payload = {'event_id': eid(), 'event_type': 'ORDER_EXCEPTION', 'patient_id': '700', 'order_number': 'EXC1',
               'order_exception': {'id': 42, 'name': 'Address Invalid', 'message': 'Cannot validate address',
                                   'order_actions': [{'event_key': 'ACCEPT_SUGGESTED', 'action': 'Accept suggestion'}]}}
    response = hook.lambda_handler(signed_event(payload), None)
    assert response['statusCode'] == 200
    order = get_order(hook, 'ord-exc-1')
    assert order['status'] == 'NECTAR_ON_HOLD'
    assert order['honeybee_exception']['name'] == 'Address Invalid'
    assert order['honeybee_exception']['order_actions'][0]['event_key'] == 'ACCEPT_SUGGESTED'
    assert len(alerts) == 1 and 'ACCEPT_SUGGESTED' in alerts[0][1]
    hook.orders_table.delete_item(Key={'order_id': 'ord-exc-1'})


def test_handler_exception_marks_event_unprocessed_and_500(hook, env, monkeypatch):
    event_id = eid()

    def boom(*a, **k):
        raise RuntimeError('db down')

    monkeypatch.setattr(hook, 'handle_rx_received', boom)
    monkeypatch.setitem(hook.HANDLERS, 'RX_RECEIVED', lambda p, pid, et: hook.handle_rx_received(p, pid))
    response = hook.lambda_handler(signed_event({'event_id': event_id, 'event_type': 'RX_RECEIVED',
                                                 'patient_id': '700'}), None)
    assert response['statusCode'] == 500
    stored = get_event(hook, event_id)
    assert stored['processed'] is False and 'db down' in stored['error']
