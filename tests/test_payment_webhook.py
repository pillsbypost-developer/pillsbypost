"""payment-webhook: Converge verification, PAID tags, discrepancy, admin alerts."""
import base64
import json

import pytest


class FakeConverge:
    def __init__(self):
        self.transactions = {}
        self.queries = []

    def query_transaction(self, txn_id):
        self.queries.append(txn_id)
        return self.transactions.get(txn_id, {'ssl_result': '1', 'ssl_result_message': 'NOT FOUND'})

    @staticmethod
    def is_approved(txn):
        return str(txn.get('ssl_result', '')) == '0'


@pytest.fixture(scope='module')
def pay(lambda_module):
    return lambda_module('payment-webhook')


@pytest.fixture()
def env(pay, fake_intakeq, monkeypatch):
    iq = fake_intakeq(clients={600: {'ClientId': 600, 'FirstName': 'Pay', 'LastName': 'Persona',
                                     'Email': 'pp@example.com', 'Tags': []}})
    cv = FakeConverge()
    cv.transactions['TXN-OK'] = {'ssl_result': '0', 'ssl_amount': '150.00', 'ssl_customer_id': '600',
                                 'ssl_trans_status': 'STL'}
    alerts = []
    monkeypatch.setattr(pay, 'intakeq', iq)
    monkeypatch.setattr(pay, 'converge', cv)
    monkeypatch.setattr(pay.email_service, 'send_admin_alert', lambda s, b, h=None: alerts.append((s, b)))
    pay.settings.clear_cache()
    return iq, cv, alerts


def json_event(payload):
    return {'body': json.dumps(payload), 'headers': {'Content-Type': 'application/json'}}


def form_event(payload, b64=False):
    body = '&'.join(f"{k}={v}" for k, v in payload.items())
    event = {'body': body, 'headers': {'content-type': 'application/x-www-form-urlencoded'}}
    if b64:
        event['body'] = base64.b64encode(body.encode()).decode()
        event['isBase64Encoded'] = True
    return event


def test_verified_payment_tags_paid_and_amount(pay, env):
    iq, cv, alerts = env
    pay.intakes_table.put_item(Item={'intake_id': 'in-600-a', 'client_id': '600',
                                     'created_at': '2026-11-01T00:00:00', 'payment_amount': 150})
    response = pay.lambda_handler(json_event({'ssl_txn_id': 'TXN-OK', 'ssl_customer_id': '600',
                                              'ssl_amount': '150.00'}), None)
    assert response['statusCode'] == 200
    assert iq.tags_added == ['PAID', '150.00']
    assert cv.queries == ['TXN-OK']
    assert alerts == []
    intake = pay.intakes_table.get_item(Key={'intake_id': 'in-600-a'})['Item']
    assert intake['payment_status'] == 'PAID'
    assert intake['converge_txn_id'] == 'TXN-OK'
    assert float(intake['paid_amount']) == 150.0
    pay.intakes_table.delete_item(Key={'intake_id': 'in-600-a'})


def test_form_encoded_and_base64_bodies(pay, env):
    iq, cv, alerts = env
    assert pay.lambda_handler(form_event({'ssl_txn_id': 'TXN-OK', 'ssl_customer_id': '600'}), None)['statusCode'] == 200
    assert pay.lambda_handler(form_event({'ssl_txn_id': 'TXN-OK', 'ssl_invoice_number': '600'}, b64=True), None)['statusCode'] == 200
    assert iq.tags_added.count('PAID') == 2


def test_discrepancy_tags_and_alerts(pay, env):
    iq, cv, alerts = env
    pay.intakes_table.put_item(Item={'intake_id': 'in-600-b', 'client_id': '600',
                                     'created_at': '2026-11-02T00:00:00', 'payment_amount': 125})
    response = pay.lambda_handler(json_event({'ssl_txn_id': 'TXN-OK', 'ssl_customer_id': '600'}), None)
    assert response['statusCode'] == 200
    assert iq.tags_added == ['PAID', '150.00', 'PAYMENT DISCREPANCY']
    assert len(alerts) == 1 and 'discrepancy' in alerts[0][0].lower()
    pay.intakes_table.delete_item(Key={'intake_id': 'in-600-b'})


def test_new_order_gets_payment_amount(pay, env):
    iq, cv, alerts = env
    pay.orders_table.put_item(Item={'order_id': 'o-600', 'client_id': '600', 'status': 'NEW',
                                    'approval_date': '2026-11-05', 'fulfillment_mode': 'shipping'})
    pay.lambda_handler(json_event({'ssl_txn_id': 'TXN-OK', 'ssl_customer_id': '600'}), None)
    order = pay.orders_table.get_item(Key={'order_id': 'o-600'})['Item']
    assert float(order['payment_amount']) == 150.0
    pay.orders_table.delete_item(Key={'order_id': 'o-600'})


def test_unverified_transaction_never_tags(pay, env):
    iq, cv, alerts = env
    response = pay.lambda_handler(json_event({'ssl_txn_id': 'TXN-FAKE', 'ssl_customer_id': '600',
                                              'ssl_amount': '150.00', 'ssl_result': '0'}), None)
    assert response['statusCode'] == 200
    assert iq.tags_added == []
    assert len(alerts) == 1 and 'verification failed' in alerts[0][0].lower()


def test_customer_mismatch_never_tags(pay, env):
    iq, cv, alerts = env
    response = pay.lambda_handler(json_event({'ssl_txn_id': 'TXN-OK', 'ssl_customer_id': '601'}), None)
    assert response['statusCode'] == 200
    assert iq.tags_added == []
    assert len(alerts) == 1 and 'does not match' in alerts[0][1]


def test_missing_ids_alert(pay, env):
    iq, cv, alerts = env
    assert pay.lambda_handler(json_event({'ssl_amount': '150.00'}), None)['statusCode'] == 200
    assert pay.lambda_handler(json_event({'ssl_txn_id': 'TXN-OK'}), None)['statusCode'] == 200
    assert len(alerts) == 2
    assert cv.queries == []
    assert iq.tags_added == []


def test_client_not_found_alerts(pay, env):
    iq, cv, alerts = env
    cv.transactions['TXN-GHOST'] = {'ssl_result': '0', 'ssl_amount': '150.00', 'ssl_customer_id': '999'}
    response = pay.lambda_handler(json_event({'ssl_txn_id': 'TXN-GHOST', 'ssl_customer_id': '999'}), None)
    assert response['statusCode'] == 200
    assert len(alerts) == 1 and 'not found' in alerts[0][0].lower()
    assert iq.tags_added == []


def test_tag_failure_500(pay, env):
    iq, cv, alerts = env
    iq.fail_tags = True
    response = pay.lambda_handler(json_event({'ssl_txn_id': 'TXN-OK', 'ssl_customer_id': '600'}), None)
    assert response['statusCode'] == 500
