"""
Settings helper: reads the `pillsbypost_<env>_settings` DynamoDB table so
operational configuration (sender addresses, the Nectar toggle, retention
periods...) is managed from the admin UI without a redeploy.

Keys are seeded by the ConfigInitializer custom resource in webui.yaml.
"""
import os

import boto3


class Settings:
    """
    Usage:
        settings = Settings(logger)
        if settings.fulfillment_mode == 'nectar': ...
    """

    def __init__(self, logger):
        self.logger = logger
        self.dynamodb = boto3.resource('dynamodb')
        self.table_name = os.environ['SETTINGS_TABLE']
        self._cache = {}

    def get(self, config_key):
        """Return the setting's value, or None if the key is absent."""
        if config_key in self._cache:
            return self._cache[config_key]

        table = self.dynamodb.Table(self.table_name)
        response = table.get_item(Key={'config_key': config_key})
        if 'Item' not in response:
            self.logger.log('ERROR', "Setting not found", config_key=config_key)
            return None

        value = response['Item'].get('value')
        self._cache[config_key] = value
        self.logger.log('DEBUG', "Setting retrieved", config_key=config_key)
        return value

    def clear_cache(self):
        self._cache = {}

    def _int(self, key, default):
        value = self.get(key)
        if value is None or str(value).strip() == '':
            return default
        return int(value)

    # ---- email -------------------------------------------------------------

    @property
    def client_sender_email(self):
        return self.get('client_sender_email')

    @property
    def admin_sender_email(self):
        return self.get('admin_sender_email')

    @property
    def admin_recipient_email(self):
        return self.get('admin_recipient_email')

    # ---- fulfillment -------------------------------------------------------

    @property
    def fulfillment_mode(self):
        """
        The Nectar toggle: 'shipping' or 'nectar'. Applies to NEW orders only.
        Raises on any other value so a typo in the UI fails loudly.
        """
        value = (self.get('fulfillment_mode') or '').strip().lower()
        if value not in ('shipping', 'nectar'):
            raise ValueError(f"fulfillment_mode must be 'shipping' or 'nectar', got {value!r}")
        return value

    @property
    def local_pharmacy_appointment_type_ids(self):
        """IntakeQ ServiceIds whose appointments are local-pharmacy orders."""
        raw = self.get('local_pharmacy_appointment_type_ids') or ''
        return [s.strip() for s in str(raw).split(',') if s.strip()]

    @property
    def intake_questionnaire_ids(self):
        """IntakeQ questionnaire ids treated as main intake forms (empty = use name heuristics)."""
        raw = self.get('intake_questionnaire_ids') or ''
        return [s.strip() for s in str(raw).split(',') if s.strip()]

    @property
    def practitioner_id(self):
        """IntakeQ PractitionerId of the provider (empty = not enforced)."""
        value = self.get('practitioner_id')
        return str(value).strip() if value else None

    @property
    def honeybee_shipping_method_id(self):
        value = self.get('honeybee_shipping_method_id')
        return str(value).strip() if value else None

    @property
    def honeybee_workflow(self):
        """'partner_requested' (we call POST /orders) or 'automatic' (Honeybee fills on receipt)."""
        value = (self.get('honeybee_workflow') or 'partner_requested').strip().lower()
        if value not in ('partner_requested', 'automatic'):
            raise ValueError(f"honeybee_workflow must be 'partner_requested' or 'automatic', got {value!r}")
        return value

    # ---- retention ---------------------------------------------------------

    @property
    def orders_retention_days(self):
        """Days before exported/shipped orders expire (TTL set when leaving NEW)."""
        return self._int('orders_retention_days', 90)

    @property
    def tracking_retention_days(self):
        return self._int('tracking_retention_days', 45)

    @property
    def stale_account_months(self):
        value = self.get('stale_account_months')
        if value is None or str(value).strip() == '':
            return None
        return int(value)
