"""
intake-worker: eligibility -> payment tier -> payment email, plus the
skip/validation/duplicate paths. Uses moto tables for eligibility rules,
payment tiers and the intakes claim record; fakes IntakeQ and email sends.
"""
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from conftest import FakeIntakeQ


def dob_ms(years_ago):
    today = datetime.now(timezone.utc)
    return int(datetime(today.year - years_ago, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.fixture(scope='module')
def worker(lambda_module):
    module = lambda_module('intake-worker')

    rules = module.eligibility_rules_table
    rules.put_item(Item={'state': 'MI', 'served': True, 'min_age': 18})
    rules.put_item(Item={'state': 'OH', 'served': True, 'min_age': 21})
    rules.put_item(Item={'state': 'NY', 'served': False, 'min_age': 18})

    tiers = module.payment_tiers_table
    tiers.put_item(Item={'tier_key': 'mail_150', 'label': 'Mail $150', 'pharmacy_choice': 'mail',
                         'amount': Decimal('150'), 'payment_link': 'https://pay.example.com/mail150',
                         'intake_answer_pattern': r'mail my pills.*\$150', 'active': True})
    tiers.put_item(Item={'tier_key': 'local_75', 'label': 'Local $75', 'pharmacy_choice': 'local',
                         'amount': Decimal('75'), 'payment_link': 'https://pay.example.com/local?x=1',
                         'intake_answer_pattern': r'local pharmacy.*\$75', 'active': True})
    tiers.put_item(Item={'tier_key': 'retired', 'label': 'Old', 'pharmacy_choice': 'mail',
                         'amount': Decimal('10'), 'payment_link': 'https://pay.example.com/old',
                         'intake_answer_pattern': r'mail my pills', 'active': False})
    return module


def make_client(client_id=100, state='MI', years_old=30, **overrides):
    client = {
        'ClientId': client_id,
        'FirstName': 'Jane',
        'LastName': 'Doe',
        'Email': f'jane{client_id}@example.com',
        'DateOfBirth': dob_ms(years_old),
        'StateShort': state,
        'Tags': [],
        'CustomFields': [],
    }
    client.update(overrides)
    return client


def make_intake(intake_id, client, answers=('Mail my pills. Yes, I can pay $150.',),
                questionnaire='Pills By Post Intake'):
    return {
        'Id': intake_id,
        'ClientId': client['ClientId'],
        'ClientEmail': client['Email'],
        'DateCreated': 1700000000000,
        'QuestionnaireName': questionnaire,
        'Questions': [{'Text': f'Q{i}', 'Answer': a} for i, a in enumerate(answers)],
    }


@pytest.fixture()
def harness(worker, monkeypatch):
    """Install FakeIntakeQ and email recorders; return a runner."""
    emails = []
    monkeypatch.setattr(worker.email_service, 'send_client_payment_email',
                        lambda client, url, amount: emails.append(('payment', url, amount)) or True)
    monkeypatch.setattr(worker.email_service, 'send_client_ineligible_email',
                        lambda client: emails.append(('ineligible', None, None)) or True)
    monkeypatch.setattr(worker.email_service, 'send_admin_review_email',
                        lambda client, reason: emails.append(('admin_review', reason, None)) or True)
    monkeypatch.setattr(worker.email_service, 'send_stale_reactivation_alert',
                        lambda client_id: emails.append(('stale', client_id, None)) or True)

    def run(client, intake, extra_clients=()):
        clients = {client['ClientId']: client}
        for c in extra_clients:
            clients[c['ClientId']] = c
        fake = FakeIntakeQ(clients=clients, intakes={intake['Id']: intake})
        monkeypatch.setattr(worker, 'intakeq', fake)
        worker.intakes_table.put_item(Item={'intake_id': str(intake['Id']), 'client_id': str(client['ClientId']),
                                            'created_at': 'now', 'status': 'QUEUED'})
        body = {'ClientId': client['ClientId'], 'IntakeId': intake['Id'], 'EventType': 'Intake Submitted'}
        worker.lambda_handler({'Records': [{'body': json.dumps(body)}]}, None)
        record = worker.intakes_table.get_item(Key={'intake_id': str(intake['Id'])})['Item']
        return fake, record

    return run, emails


def test_eligible_matched_tier_sends_payment_link(harness):
    run, emails = harness
    client = make_client(100)
    fake, record = run(client, make_intake('i-100', client))

    assert record['status'] == 'PROCESSED'
    assert record['workflow_path'] == 'payment'
    assert record['payment_tier'] == 'mail_150'
    assert record['payment_amount'] == Decimal('150')
    assert record['pharmacy_choice'] == 'mail'
    assert record['email_template_sent'] == 'payment'
    assert record['eligibility_result'] == 'eligible'
    assert record['age'] == 30
    assert record['state'] == 'MI'

    assert 'AWAITING-PAYMENT' in fake.tags_added
    update = fake.client_updates[-1]
    assert 'PAYMENT LINK' in update['AdditionalInformation']
    assert 'ssl_customer_id=100' in update['AdditionalInformation']
    assert 'PractitionerId' not in update

    kind, url, amount = emails[-1]
    assert kind == 'payment'
    assert url.startswith('https://pay.example.com/mail150?ssl_customer_id=100')
    assert 'ssl_invoice_number=100' in url
    assert amount == Decimal('150')


def test_link_with_existing_query_string_uses_ampersand(harness):
    run, emails = harness
    client = make_client(101)
    _, record = run(client, make_intake('i-101', client, answers=('I will use my local pharmacy and pay $75',)))
    assert record['payment_tier'] == 'local_75'
    assert record['pharmacy_choice'] == 'local'
    assert record['payment_url'].startswith('https://pay.example.com/local?x=1&ssl_customer_id=101')


def test_state_not_served_is_ineligible(harness):
    run, emails = harness
    client = make_client(102, state='NY')
    fake, record = run(client, make_intake('i-102', client))
    assert record['status'] == 'INELIGIBLE'
    assert record['eligibility_result'] == 'state_not_served:NY'
    assert 'INELIGIBLE' in fake.tags_added
    assert emails[-1][0] == 'ineligible'
    assert 'payment_tier' not in record


def test_unconfigured_state_is_ineligible(harness):
    run, _ = harness
    client = make_client(103, state='TX')
    _, record = run(client, make_intake('i-103', client))
    assert record['status'] == 'INELIGIBLE'
    assert record['eligibility_result'] == 'state_not_configured:TX'


def test_under_min_age_is_ineligible(harness):
    run, emails = harness
    client = make_client(104, state='OH', years_old=19)
    fake, record = run(client, make_intake('i-104', client))
    assert record['status'] == 'INELIGIBLE'
    assert record['eligibility_result'] == 'under_min_age:21'
    assert 'INELIGIBLE' in fake.tags_added


def test_no_tier_match_goes_to_admin_review(harness):
    run, emails = harness
    client = make_client(105)
    fake, record = run(client, make_intake('i-105', client, answers=('I would like to talk to someone',)))
    assert record['status'] == 'PROCESSED'
    assert record['workflow_path'] == 'admin_review'
    assert record['admin_review_reason'] == 'no_match'
    assert 'ADMIN' in fake.tags_added
    assert 'AWAITING-PAYMENT' not in fake.tags_added
    assert emails[-1][0] == 'admin_review'
    assert 'no_match' in emails[-1][1]


def test_multiple_tier_matches_go_to_admin_review(harness):
    run, emails = harness
    client = make_client(106)
    _, record = run(client, make_intake(
        'i-106', client, answers=('Mail my pills, I can pay $150, or local pharmacy for $75',)))
    assert record['workflow_path'] == 'admin_review'
    assert record['admin_review_reason'].startswith('multiple_matches:')


def test_duplicate_by_email_tagged_and_still_processed(harness):
    run, _ = harness
    client = make_client(200, Email='same@example.com')
    older = make_client(50, Email='same@example.com')
    fake, record = run(client, make_intake('i-200', client), extra_clients=[older])
    assert 'DUPLICATE' in fake.tags_added
    assert record['is_duplicate'] is True
    assert record['workflow_path'] == 'payment'


def test_newer_client_with_same_email_is_not_a_duplicate(harness):
    run, _ = harness
    client = make_client(201, Email='other@example.com')
    newer = make_client(999, Email='other@example.com')
    fake, record = run(client, make_intake('i-201', client), extra_clients=[newer])
    assert 'DUPLICATE' not in fake.tags_added
    assert record['is_duplicate'] is False


def test_non_intake_questionnaire_skipped(harness):
    run, emails = harness
    client = make_client(300)
    fake, record = run(client, make_intake('i-300', client, questionnaire='Follow-up Questionnaire'))
    assert record['status'] == 'SKIPPED_NON_INTAKE'
    assert fake.tags_added == []
    assert emails == []


def test_validation_failure_recorded(harness):
    run, emails = harness
    client = make_client(301, DateOfBirth=None)
    fake, record = run(client, make_intake('i-301', client))
    assert record['status'] == 'VALIDATION_FAILED'
    assert record['validation_error'] == 'DateOfBirth missing'
    assert fake.tags_added == []
    assert emails == []


def test_missing_intakeq_data_raises_for_sqs_retry(worker, monkeypatch):
    monkeypatch.setattr(worker, 'intakeq', FakeIntakeQ())
    body = {'ClientId': 1, 'IntakeId': 'i-missing', 'EventType': 'Intake Submitted'}
    with pytest.raises(RuntimeError):
        worker.lambda_handler({'Records': [{'body': json.dumps(body)}]}, None)


def test_comparephone_synced_to_phone_fields(harness):
    run, _ = harness
    client = make_client(302, CustomFields=[{'Text': 'COMPAREPHONE', 'FieldId': 'f1', 'Value': '5551234567'}])
    fake, _ = run(client, make_intake('i-302', client))
    assert fake.client_updates[0]['Phone'] == '5551234567'
    assert fake.client_updates[0]['MobilePhone'] == '5551234567'


def test_stale_account_alert(harness):
    run, emails = harness
    old_ms = int((datetime.now(timezone.utc).timestamp() - 400 * 86400) * 1000)
    client = make_client(303, LastActivityDate=old_ms)
    run(client, make_intake('i-303', client))
    assert ('stale', 303, None) in emails


@pytest.mark.parametrize('name,expected', [
    ('Pills By Post Intake', True),
    ('Follow-up Questionnaire', False),
    ('Consent Form', False),
    ('Appointment Reminder', False),
    ('Research Survey', False),
    ('', False),
    (None, False),
])
def test_is_main_intake_form(worker, name, expected):
    assert worker.is_main_intake_form(name) is expected
