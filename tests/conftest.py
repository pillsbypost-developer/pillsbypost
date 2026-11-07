"""
Shared test fixtures.

Lambda modules create boto3 resources and read Secrets Manager at import
time, so the moto mock must be active and the fake AWS resources created
BEFORE any lambda module is imported. Use the `lambda_module` fixture to
import lambdas - never import them at the top of a test file.

Table names/schemas mirror pillsbypost.yaml and webui.yaml.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# common/ on the import path (mirrors the bundled package in each Lambda zip)
sys.path.insert(0, str(REPO_ROOT / 'layers' / 'common' / 'python'))

# Fake AWS credentials/region - must be set before boto3 clients are created
os.environ['AWS_ACCESS_KEY_ID'] = 'testing'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'testing'
os.environ['AWS_SECURITY_TOKEN'] = 'testing'
os.environ['AWS_SESSION_TOKEN'] = 'testing'
os.environ['AWS_REGION'] = 'us-east-2'
os.environ['AWS_DEFAULT_REGION'] = 'us-east-2'

ENV_VARS = {
    'ENV': 'test',
    'LOG_LEVEL': 'DEBUG',
    # operational tables (pillsbypost.yaml)
    'INTAKES_TABLE': 'test_intakes',
    'ORDERS_TABLE': 'test_orders',
    'HONEYBEE_EVENTS_TABLE': 'test_honeybee_events',
    'TRACKING_TABLE': 'test_tracking',
    'TRACKING_JOBS_TABLE': 'test_tracking_jobs',
    # config tables (webui.yaml)
    'SETTINGS_TABLE': 'pillsbypost_test_settings',
    'EMAIL_TEMPLATES_TABLE': 'pillsbypost_test_email_templates',
    'EMAIL_SUPPRESSIONS_TABLE': 'pillsbypost_test_email_suppressions',
    'PAYMENT_TIERS_TABLE': 'pillsbypost_test_payment_tiers',
    'ELIGIBILITY_RULES_TABLE': 'pillsbypost_test_eligibility_rules',
    # secrets
    'INTAKEQ_API_SECRET': 'pillsbypost/test/intakeq-api-key',
    'HONEYBEE_OAUTH_SECRET': 'pillsbypost/test/honeybee-oauth',
    'HONEYBEE_WEBHOOK_SECRET': 'pillsbypost/test/honeybee-webhook-secret',
    'CONVERGE_SECRET': 'pillsbypost/test/converge',
    # endpoints
    'HONEYBEE_API_BASE': 'https://partners.sandbox.honeybeehealth.com/v1',
    'HONEYBEE_AUTH_URL': 'https://auth.honeybeehealth.com/oauth/token',
    'CONVERGE_API_BASE': 'https://api.demo.convergepay.com/VirtualMerchantDemo',
    'SES_CONFIGURATION_SET': 'pillsbypost-test-ses-config',
    'STATE_MACHINE_ARN': 'arn:aws:states:us-east-2:123456789012:stateMachine:test',
    'CORS_ORIGIN': 'https://test.example.com',
    'INTAKEQ_API_DELAY': '0',
}
os.environ.update(ENV_VARS)

import boto3  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

_mock = mock_aws()


def _table(dynamodb, name, pk, gsis=None, extra_attrs=None):
    attrs = [{'AttributeName': pk, 'AttributeType': 'S'}]
    for a in (extra_attrs or []):
        attrs.append({'AttributeName': a, 'AttributeType': 'S'})
    kwargs = {
        'TableName': name,
        'BillingMode': 'PAY_PER_REQUEST',
        'AttributeDefinitions': attrs,
        'KeySchema': [{'AttributeName': pk, 'KeyType': 'HASH'}],
    }
    if gsis:
        kwargs['GlobalSecondaryIndexes'] = [
            {'IndexName': name_, 'KeySchema': keys, 'Projection': {'ProjectionType': 'ALL'}}
            for name_, keys in gsis
        ]
    dynamodb.create_table(**kwargs)


def _create_tables(dynamodb):
    """Create moto versions of every table the lambdas touch (real schemas)."""
    e = os.environ
    _table(dynamodb, e['INTAKES_TABLE'], 'intake_id',
           gsis=[('client-index', [{'AttributeName': 'client_id', 'KeyType': 'HASH'},
                                   {'AttributeName': 'created_at', 'KeyType': 'RANGE'}])],
           extra_attrs=['client_id', 'created_at'])
    _table(dynamodb, e['ORDERS_TABLE'], 'order_id',
           gsis=[('client-index', [{'AttributeName': 'client_id', 'KeyType': 'HASH'},
                                   {'AttributeName': 'approval_date', 'KeyType': 'RANGE'}]),
                 ('status-index', [{'AttributeName': 'status', 'KeyType': 'HASH'},
                                   {'AttributeName': 'approval_date', 'KeyType': 'RANGE'}])],
           extra_attrs=['client_id', 'approval_date', 'status'])
    _table(dynamodb, e['HONEYBEE_EVENTS_TABLE'], 'event_id',
           gsis=[('patient-index', [{'AttributeName': 'patient_id', 'KeyType': 'HASH'},
                                    {'AttributeName': 'received_at', 'KeyType': 'RANGE'}])],
           extra_attrs=['patient_id', 'received_at'])
    _table(dynamodb, e['TRACKING_TABLE'], 'tracking_id',
           gsis=[('client-tracking-index', [{'AttributeName': 'client_id', 'KeyType': 'HASH'}])],
           extra_attrs=['client_id'])
    _table(dynamodb, e['TRACKING_JOBS_TABLE'], 'job_id',
           gsis=[('status-index', [{'AttributeName': 'status', 'KeyType': 'HASH'}])],
           extra_attrs=['status'])
    _table(dynamodb, e['SETTINGS_TABLE'], 'config_key')
    _table(dynamodb, e['EMAIL_TEMPLATES_TABLE'], 'template_id')
    _table(dynamodb, e['EMAIL_SUPPRESSIONS_TABLE'], 'email')
    _table(dynamodb, e['PAYMENT_TIERS_TABLE'], 'tier_key')
    _table(dynamodb, e['ELIGIBILITY_RULES_TABLE'], 'state')


DEFAULT_SETTINGS = {
    'client_sender_email': 'care@test.example.com',
    'admin_sender_email': 'alerts@test.example.com',
    'admin_recipient_email': 'admin@test.example.com',
    'fulfillment_mode': 'shipping',
    'local_pharmacy_appointment_type_ids': 'svc-local',
    'practitioner_id': 'prac-1',
    'intake_questionnaire_ids': '',
    'honeybee_shipping_method_id': '7',
    'honeybee_workflow': 'partner_requested',
    'tracking_retention_days': '45',
    'orders_retention_days': '90',
    'stale_account_months': '9',
}


def seed_settings(dynamodb_resource, overrides=None):
    table = dynamodb_resource.Table(os.environ['SETTINGS_TABLE'])
    for key, value in {**DEFAULT_SETTINGS, **(overrides or {})}.items():
        table.put_item(Item={'config_key': key, 'value': value})


@pytest.fixture(scope='session', autouse=True)
def aws_stack():
    """Session-wide moto mock with secrets, tables, queues and SES identities pre-created."""
    _mock.start()

    sm = boto3.client('secretsmanager')
    sm.create_secret(Name=os.environ['INTAKEQ_API_SECRET'], SecretString=json.dumps({'api_key': 'test-api-key'}))
    sm.create_secret(Name=os.environ['HONEYBEE_OAUTH_SECRET'],
                     SecretString=json.dumps({'client_id': 'hb-id', 'client_secret': 'hb-secret'}))
    sm.create_secret(Name=os.environ['HONEYBEE_WEBHOOK_SECRET'],
                     SecretString=json.dumps({'signing_secret': 'hook-secret'}))
    sm.create_secret(Name=os.environ['CONVERGE_SECRET'],
                     SecretString=json.dumps({'merchant_id': 'm', 'user_id': 'u', 'pin': 'p'}))

    _create_tables(boto3.client('dynamodb'))
    seed_settings(boto3.resource('dynamodb'))

    sqs = boto3.client('sqs')
    os.environ['INTAKE_QUEUE_URL'] = sqs.create_queue(QueueName='test-intake-queue')['QueueUrl']
    os.environ['NECTAR_ORDER_QUEUE_URL'] = sqs.create_queue(QueueName='test-nectar-order-queue')['QueueUrl']

    ses = boto3.client('ses')
    for addr in ('care@test.example.com', 'alerts@test.example.com'):
        ses.verify_email_identity(EmailAddress=addr)

    yield

    _mock.stop()


@pytest.fixture(scope='session')
def lambda_module(aws_stack):
    """
    Import a lambda by directory name (hyphenated dirs aren't importable
    normally). Cached per session; imports run inside the moto mock. Extra
    modules beside the handler (e.g. table_configs.py) are importable while
    the lambda loads.
    """
    cache = {}

    def _load(name):
        if name not in cache:
            lambda_dir = REPO_ROOT / 'lambdas' / name
            sys.path.insert(0, str(lambda_dir))
            try:
                path = lambda_dir / 'lambda_function.py'
                module_name = name.replace('-', '_') + '_lambda'
                spec = importlib.util.spec_from_file_location(module_name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                cache[name] = module
            finally:
                sys.path.remove(str(lambda_dir))
        return cache[name]

    return _load


@pytest.fixture()
def dynamodb_resource(aws_stack):
    return boto3.resource('dynamodb')


@pytest.fixture()
def settings_table(dynamodb_resource):
    return dynamodb_resource.Table(os.environ['SETTINGS_TABLE'])


class FakeIntakeQ:
    """
    Records tag and client-update calls; serves canned clients/intakes/appointments.
    Use with monkeypatch.setattr(module, 'intakeq', FakeIntakeQ(...)).
    """

    def __init__(self, clients=None, intakes=None, appointments=None):
        self.clients = {str(k): v for k, v in (clients or {}).items()}
        self.intakes = {str(k): v for k, v in (intakes or {}).items()}
        self.appointments = {str(k): v for k, v in (appointments or {}).items()}
        self.tags_added = []
        self.tags_removed = []
        self.client_updates = []
        self.fail_tags = False

    def get_client_by_id(self, client_id):
        return self.clients.get(str(client_id))

    def get_intake_by_id(self, intake_id):
        return self.intakes.get(str(intake_id))

    def get_appointment(self, appointment_id):
        return self.appointments.get(str(appointment_id))

    def search_clients_by_email(self, email):
        return [c for c in self.clients.values() if (c.get('Email') or '').lower() == email.lower()]

    def search_clients_by_custom_field(self, field_id, value):
        return [c for c in self.clients.values()
                if any(f.get('FieldId') == field_id and f.get('Value') == value
                       for f in c.get('CustomFields', []))]

    def search_clients_by_name(self, full_name):
        return [c for c in self.clients.values()
                if f"{c.get('FirstName', '')} {c.get('LastName', '')}".strip() == full_name]

    def update_client_tags(self, client_id, tag, remove=False):
        if self.fail_tags:
            return False
        (self.tags_removed if remove else self.tags_added).append(tag)
        client = self.clients.get(str(client_id))
        if client is not None:
            tags = client.setdefault('Tags', [])
            if remove and tag in tags:
                tags.remove(tag)
            elif not remove and tag not in tags:
                tags.append(tag)
        return True

    def update_client(self, client_data):
        self.client_updates.append(dict(client_data))
        self.clients[str(client_data.get('ClientId'))] = dict(client_data)
        return dict(client_data)


@pytest.fixture()
def fake_intakeq():
    return FakeIntakeQ
