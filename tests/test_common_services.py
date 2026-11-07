"""common.settings, common.email_service, common.tracking_service against moto."""
import os
from datetime import datetime, timezone

import boto3
import pytest

from common.email_service import EmailService
from common.logger import CustomLogger
from common.settings import Settings
from common.tracking_service import mark_order_shipped, process_tracking_entry
from conftest import FakeIntakeQ


@pytest.fixture()
def settings(aws_stack):
    return Settings(CustomLogger())


class TestSettings:
    def test_seeded_values(self, settings):
        assert settings.fulfillment_mode == 'shipping'
        assert settings.local_pharmacy_appointment_type_ids == ['svc-local']
        assert settings.practitioner_id == 'prac-1'
        assert settings.honeybee_shipping_method_id == '7'
        assert settings.honeybee_workflow == 'partner_requested'
        assert settings.orders_retention_days == 90
        assert settings.tracking_retention_days == 45
        assert settings.stale_account_months == 9

    def test_invalid_fulfillment_mode_raises(self, settings, settings_table):
        settings_table.put_item(Item={'config_key': 'fulfillment_mode', 'value': 'carrier pigeon'})
        settings.clear_cache()
        with pytest.raises(ValueError):
            settings.fulfillment_mode
        settings_table.put_item(Item={'config_key': 'fulfillment_mode', 'value': 'shipping'})

    def test_missing_key_returns_none(self, settings):
        assert settings.get('does_not_exist') is None

    def test_cache_until_cleared(self, settings, settings_table):
        assert settings.get('practitioner_id') == 'prac-1'
        settings_table.put_item(Item={'config_key': 'practitioner_id', 'value': 'prac-2'})
        assert settings.get('practitioner_id') == 'prac-1'
        settings.clear_cache()
        assert settings.get('practitioner_id') == 'prac-2'
        settings_table.put_item(Item={'config_key': 'practitioner_id', 'value': 'prac-1'})


@pytest.fixture()
def email_service(settings, dynamodb_resource):
    templates = dynamodb_resource.Table(os.environ['EMAIL_TEMPLATES_TABLE'])
    templates.put_item(Item={'template_id': 'payment', 'subject': 'Pay #AMOUNT#',
                             'body': 'Hi #NAME#, pay at #PAYLINK#'})
    templates.put_item(Item={'template_id': 'ineligible', 'subject': '', 'body': ''})
    return EmailService(CustomLogger(), settings, email_templates_table=templates)


CLIENT = {'ClientId': 100, 'FirstName': 'Jane', 'LastName': 'Doe', 'Email': 'jane@example.com', 'StateShort': 'MI'}


class TestEmailService:
    def test_variable_substitution(self):
        text = EmailService.replace_template_variables(
            '#NAME# #EMAIL# #PAYLINK# #AMOUNT# #TRACKING# #CARRIER#', CLIENT,
            payment_url='https://pay', payment_amount=150, tracking_number='1Z', carrier='UPS')
        assert text == 'Jane jane@example.com https://pay 150.00 1Z UPS'

    def test_payment_email_sent(self, email_service):
        assert email_service.send_client_payment_email(CLIENT, 'https://pay/1', 150.0) is True

    def test_empty_template_not_sent(self, email_service):
        assert email_service.send_client_ineligible_email(CLIENT) is False

    def test_missing_template_not_sent(self, email_service):
        assert email_service.send_template_email('nope', CLIENT) is False

    def test_suppressed_recipient_skipped(self, email_service, dynamodb_resource):
        dynamodb_resource.Table(os.environ['EMAIL_SUPPRESSIONS_TABLE']).put_item(
            Item={'email': 'bounced@example.com', 'reason': 'bounce'})
        client = {**CLIENT, 'Email': 'bounced@example.com'}
        assert email_service.send_client_payment_email(client, 'https://pay', 1) is False

    def test_admin_alert_and_review(self, email_service):
        assert email_service.send_admin_alert('Subject', 'body') is True
        assert email_service.send_admin_review_email(CLIENT, 'no tier matched') is True
        assert email_service.send_stale_reactivation_alert(100) is True

    def test_admin_alert_without_recipient(self, email_service, settings_table):
        settings_table.put_item(Item={'config_key': 'admin_recipient_email', 'value': ''})
        email_service._settings.clear_cache()
        assert email_service.send_admin_alert('Subject', 'body') is False
        settings_table.put_item(Item={'config_key': 'admin_recipient_email', 'value': 'admin@test.example.com'})
        email_service._settings.clear_cache()


class TestTrackingService:
    def _order(self, orders, order_id, client_id, status, approval_date):
        orders.put_item(Item={'order_id': order_id, 'client_id': str(client_id), 'status': status,
                              'approval_date': approval_date, 'fulfillment_mode': 'shipping'})

    def test_process_entry_writes_tracking_and_ships_order(self, dynamodb_resource):
        orders = dynamodb_resource.Table(os.environ['ORDERS_TABLE'])
        tracking = dynamodb_resource.Table(os.environ['TRACKING_TABLE'])
        self._order(orders, 'ord-old', 501, 'SHIPPED', '2026-01-01')
        self._order(orders, 'ord-new', 501, 'EXPORTED', '2026-02-01')
        intakeq = FakeIntakeQ(clients={501: {'ClientId': 501, 'Email': 'a@b.c'}})

        result = process_tracking_entry('501', '9400111', tracking, intakeq, CustomLogger(),
                                        carrier='USPS', ttl=123, orders_table=orders)

        assert result['status'] == 'success'
        assert 'Tracking number' in intakeq.client_updates[-1]['AdditionalInformation']
        assert '9400111' in intakeq.client_updates[-1]['AdditionalInformation']
        order = orders.get_item(Key={'order_id': 'ord-new'})['Item']
        assert order['status'] == 'SHIPPED'
        assert order['tracking_number'] == '9400111'
        assert order['carrier'] == 'USPS'
        assert orders.get_item(Key={'order_id': 'ord-old'})['Item']['status'] == 'SHIPPED'
        rows = tracking.scan()['Items']
        assert any(r['client_id'] == '501' and r['tracking_number'] == '9400111' and r['ttl'] == 123 for r in rows)

    def test_client_not_found(self, dynamodb_resource):
        tracking = dynamodb_resource.Table(os.environ['TRACKING_TABLE'])
        result = process_tracking_entry('999', '1Z', tracking, FakeIntakeQ(), CustomLogger())
        assert result['status'] == 'error'

    def test_no_open_order_is_not_an_error(self, dynamodb_resource):
        orders = dynamodb_resource.Table(os.environ['ORDERS_TABLE'])
        assert mark_order_shipped(orders, '777', '1Z', None, True, CustomLogger()) is None
