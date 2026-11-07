"""
Table configurations for the admin API.

Read-only tables (intakes, honeybee-events, tracking-jobs) are listed in
READ_ONLY_TABLES; the handler rejects writes on them.
"""
import os

import boto3

from common import (
    TableConfig,
    validate_state_code,
    validate_boolean,
    validate_non_negative_number,
    validate_pharmacy_choice,
    validate_url,
    validate_email,
    validate_template_id,
    validate_template_content,
    validate_non_empty_string,
)

dynamodb = boto3.resource('dynamodb')

# Environment variables (no fallbacks - Fail Fast)
SETTINGS_TABLE = os.environ['SETTINGS_TABLE']
EMAIL_TEMPLATES_TABLE = os.environ['EMAIL_TEMPLATES_TABLE']
EMAIL_SUPPRESSIONS_TABLE = os.environ['EMAIL_SUPPRESSIONS_TABLE']
PAYMENT_TIERS_TABLE = os.environ['PAYMENT_TIERS_TABLE']
ELIGIBILITY_RULES_TABLE = os.environ['ELIGIBILITY_RULES_TABLE']
ORDERS_TABLE = os.environ['ORDERS_TABLE']
INTAKES_TABLE = os.environ['INTAKES_TABLE']
HONEYBEE_EVENTS_TABLE = os.environ['HONEYBEE_EVENTS_TABLE']
TRACKING_TABLE = os.environ['TRACKING_TABLE']
TRACKING_JOBS_TABLE = os.environ['TRACKING_JOBS_TABLE']

# Fields on an order record (CLAUDE.md orders schema + tracking/payment fields)
ORDER_FIELDS = [
    'client_id', 'client_name', 'email', 'dob', 'shipping_address', 'unit_number',
    'care_of', 'city', 'state', 'postal_code', 'country', 'approval_date',
    'appointment_type_id', 'appointment_type_name', 'fulfillment_mode', 'status',
    'payment_amount', 'payment_tier', 'payment_status', 'paid_at',
    'tracking_number', 'carrier', 'tracking_url', 'tracking_synced_to_intakeq',
    'honeybee_patient_id', 'honeybee_prescription_ids', 'honeybee_order_number',
    'honeybee_status', 'honeybee_exception', 'exported_at', 'export_batch_id',
    'shipped_at', 'delivered_at', 'canceled_at', 'notes', 'ttl', 'created_at', 'updated_at',
]

INTAKE_FIELDS = [
    'client_id', 'status', 'payment_tier', 'payment_amount', 'pharmacy_choice', 'state',
    'age', 'eligibility_result', 'email_template_sent', 'created_at', 'processed_at',
    'questionnaire_name', 'event_type', 'validation_error', 'client_email', 'workflow_path',
    'payment_status', 'paid_at', 'paid_amount',
]

HONEYBEE_EVENT_FIELDS = [
    'event_type', 'patient_id', 'received_at', 'raw', 'processed', 'order_id',
    'order_number', 'error', 'ttl',
]

TRACKING_JOB_FIELDS = ['status', 'total', 'succeeded', 'failed', 'entries', 'results',
                       'created_at', 'completed_at', 'ttl']


def _config(table_name, primary_key, required=None, optional=None, validators=None,
            auto_uppercase=None, read_only=None, unique=None, indexes=None):
    return TableConfig(
        table_name=table_name,
        table_resource=dynamodb.Table(table_name),
        primary_key=primary_key,
        required_fields=required or [],
        optional_fields=optional or [],
        validators=validators or {},
        unique_constraints=unique or [],
        indexes=indexes or {},
        auto_uppercase_fields=auto_uppercase or [],
        read_only_fields=read_only or [],
    )


def get_settings_config():
    return _config(SETTINGS_TABLE, 'config_key',
                   required=['config_key', 'value'],
                   optional=['description'],
                   read_only=['createdAt', 'createdBy'])


def get_email_templates_config():
    return _config(EMAIL_TEMPLATES_TABLE, 'template_id',
                   required=['template_id', 'template_name', 'subject', 'body'],
                   optional=['description', 'category'],
                   validators={
                       'template_id': validate_template_id,
                       'template_name': validate_template_content,
                       'subject': validate_template_content,
                       'body': validate_template_content,
                   },
                   read_only=['createdAt', 'createdBy'])


def get_email_suppressions_config():
    return _config(EMAIL_SUPPRESSIONS_TABLE, 'email',
                   required=['email', 'reason'],
                   optional=['notes', 'bounce_type', 'bounce_sub_type', 'source_email',
                             'sns_message_id', 'raw_notification'],
                   validators={'email': validate_email},
                   read_only=['created_at'])


def get_payment_tiers_config():
    return _config(PAYMENT_TIERS_TABLE, 'tier_key',
                   required=['tier_key', 'label', 'pharmacy_choice', 'amount', 'payment_link',
                             'intake_answer_pattern'],
                   optional=['active', 'notes'],
                   validators={
                       'tier_key': validate_non_empty_string,
                       'label': validate_non_empty_string,
                       'pharmacy_choice': validate_pharmacy_choice,
                       'amount': validate_non_negative_number,
                       'payment_link': validate_url,
                       'intake_answer_pattern': validate_non_empty_string,
                       'active': validate_boolean,
                   },
                   read_only=['createdAt', 'createdBy'])


def get_eligibility_rules_config():
    return _config(ELIGIBILITY_RULES_TABLE, 'state',
                   required=['state', 'served'],
                   optional=['min_age', 'notes'],
                   validators={
                       'state': validate_state_code,
                       'served': validate_boolean,
                       'min_age': validate_non_negative_number,
                   },
                   auto_uppercase=['state'],
                   read_only=['createdAt', 'createdBy'])


def get_orders_config():
    return _config(ORDERS_TABLE, 'order_id',
                   required=[],
                   optional=ORDER_FIELDS,
                   read_only=['order_id', 'client_id', 'created_at', 'createdAt', 'createdBy'])


def get_intakes_config():
    return _config(INTAKES_TABLE, 'intake_id',
                   optional=INTAKE_FIELDS,
                   read_only=['intake_id'] + INTAKE_FIELDS)


def get_honeybee_events_config():
    return _config(HONEYBEE_EVENTS_TABLE, 'event_id',
                   optional=HONEYBEE_EVENT_FIELDS,
                   read_only=['event_id'] + HONEYBEE_EVENT_FIELDS)


def get_tracking_config():
    return _config(TRACKING_TABLE, 'tracking_id',
                   required=['client_id', 'tracking_number'],
                   optional=['carrier', 'notes', 'synced_to_intakeq'],
                   read_only=['tracking_id', 'createdAt', 'createdBy'])


def get_tracking_jobs_config():
    return _config(TRACKING_JOBS_TABLE, 'job_id',
                   optional=TRACKING_JOB_FIELDS,
                   read_only=['job_id'] + TRACKING_JOB_FIELDS)


TABLE_CONFIGS = {
    'settings': get_settings_config,
    'email-templates': get_email_templates_config,
    'email-suppressions': get_email_suppressions_config,
    'payment-tiers': get_payment_tiers_config,
    'eligibility-rules': get_eligibility_rules_config,
    'orders': get_orders_config,
    'intakes': get_intakes_config,
    'honeybee-events': get_honeybee_events_config,
    'tracking': get_tracking_config,
    'tracking-jobs': get_tracking_jobs_config,
}

# Tables the admin UI may only read
READ_ONLY_TABLES = {'intakes', 'honeybee-events', 'tracking-jobs'}


def get_table_config(table_name: str) -> TableConfig:
    if table_name not in TABLE_CONFIGS:
        raise ValueError(f"Unknown table: {table_name}. Available tables: {list(TABLE_CONFIGS.keys())}")
    return TABLE_CONFIGS[table_name]()
