import os

import boto3
from botocore.exceptions import ClientError

FOOTER = "\n---\nPills By Post integration"


class EmailService:
    """
    SES sender with template substitution and bounce/complaint suppression.

    Patient-facing templates live in the email_templates table (seeded by
    webui.yaml): payment, ineligible, order_shipped. Admin notifications are
    plain text built here. Variables: #NAME#, #EMAIL#, #PAYLINK#, #AMOUNT#,
    #TRACKING#, #CARRIER#.
    """

    def __init__(self, logger, settings, email_templates_table=None):
        self.logger = logger
        self.ses_client = boto3.client('ses')
        self._settings = settings
        self._email_templates_table = email_templates_table
        self._configuration_set = os.environ.get('SES_CONFIGURATION_SET')

        suppressions_table_name = os.environ.get('EMAIL_SUPPRESSIONS_TABLE')
        self._suppressions_table = (boto3.resource('dynamodb').Table(suppressions_table_name)
                                    if suppressions_table_name else None)

    @property
    def client_sender_email(self):
        return self._settings.client_sender_email

    @property
    def admin_sender_email(self):
        return self._settings.admin_sender_email

    @property
    def admin_recipient_email(self):
        return self._settings.admin_recipient_email

    # ---- templates ---------------------------------------------------------

    def get_template(self, template_id):
        if self._email_templates_table is None:
            self.logger.log('ERROR', "Email templates table not configured")
            return None
        response = self._email_templates_table.get_item(Key={'template_id': template_id})
        if 'Item' not in response:
            self.logger.log('ERROR', "Email template not found", template_id=template_id)
            return None
        return response['Item']

    @staticmethod
    def replace_template_variables(text, client_data, payment_url=None, payment_amount=None,
                                   tracking_number=None, carrier=None):
        if not text:
            return text
        text = text.replace('#NAME#', client_data.get('FirstName', '') or '')
        text = text.replace('#EMAIL#', client_data.get('Email', '') or '')
        if payment_url:
            text = text.replace('#PAYLINK#', payment_url)
        if payment_amount is not None:
            text = text.replace('#AMOUNT#', f"{float(payment_amount):.2f}")
        if tracking_number:
            text = text.replace('#TRACKING#', tracking_number)
        if carrier:
            text = text.replace('#CARRIER#', carrier)
        return text

    # ---- sending -----------------------------------------------------------

    def _is_suppressed(self, email):
        if self._suppressions_table is None:
            return False
        response = self._suppressions_table.get_item(Key={'email': email.lower().strip()})
        if 'Item' in response:
            self.logger.log('INFO', "Email address is suppressed - skipping send",
                            email=email, reason=response['Item'].get('reason', 'unknown'))
            return True
        return False

    def send_email(self, to_addresses, subject, text_body, html_body=None, sender_email=None):
        """Send via SES. Returns True on success, False otherwise (never raises)."""
        sender = sender_email or self.client_sender_email
        if not sender:
            self.logger.log('ERROR', "Sender email not configured")
            return False
        if not to_addresses:
            self.logger.log('ERROR', "No recipient addresses provided")
            return False
        for addr in to_addresses:
            if self._is_suppressed(addr):
                return False

        message = {
            'Subject': {'Data': subject, 'Charset': 'UTF-8'},
            'Body': {'Text': {'Data': text_body, 'Charset': 'UTF-8'}},
        }
        if html_body:
            message['Body']['Html'] = {'Data': html_body, 'Charset': 'UTF-8'}

        kwargs = {'Source': sender, 'Destination': {'ToAddresses': to_addresses}, 'Message': message}
        if self._configuration_set:
            kwargs['ConfigurationSetName'] = self._configuration_set

        try:
            response = self.ses_client.send_email(**kwargs)
            self.logger.log('INFO', "Email sent", message_id=response.get('MessageId'), to=to_addresses[0])
            return True
        except ClientError as e:
            self.logger.log('ERROR', "Failed to send email",
                            error_code=e.response['Error']['Code'],
                            error_message=e.response['Error']['Message'])
            return False
        except Exception as e:
            self.logger.log('ERROR', "Unexpected error sending email", error=str(e))
            return False

    def send_template_email(self, template_id, client_data, recipient_email=None, **variables):
        """Load a template from DynamoDB, substitute variables, send to the client."""
        template = self.get_template(template_id)
        if not template:
            return False
        subject = self.replace_template_variables(template.get('subject', ''), client_data, **variables)
        body = self.replace_template_variables(template.get('body', ''), client_data, **variables)
        if not subject.strip() or not body.strip():
            self.logger.log('ERROR', "Email template is empty - not sending", template_id=template_id)
            return False

        to_email = recipient_email or client_data.get('Email')
        if not to_email:
            self.logger.log('ERROR', "No recipient email available", template_id=template_id)
            return False
        return self.send_email([to_email], subject, body, sender_email=self.client_sender_email)

    # ---- patient-facing ----------------------------------------------------

    def send_client_payment_email(self, client_data, payment_url, payment_amount):
        return self.send_template_email('payment', client_data,
                                        payment_url=payment_url, payment_amount=payment_amount)

    def send_client_ineligible_email(self, client_data):
        return self.send_template_email('ineligible', client_data)

    def send_order_shipped_email(self, client_data, tracking_number, carrier=None):
        return self.send_template_email('order_shipped', client_data,
                                        tracking_number=tracking_number, carrier=carrier)

    # ---- admin notifications -----------------------------------------------

    def send_admin_alert(self, subject, text_body, html_body=None):
        """Plain admin notification. Returns False (and logs) if admin email is unset."""
        admin_email = self.admin_recipient_email
        if not admin_email:
            self.logger.log('ERROR', "Admin recipient email not configured", subject=subject)
            return False
        return self.send_email([admin_email], subject, text_body + FOOTER, html_body,
                               sender_email=self.admin_sender_email)

    def send_admin_review_email(self, client_data, reason):
        """An intake needs a human. Contains client id/name/email/state (no clinical data)."""
        client_id = client_data.get('ClientId', 'unknown')
        body = f"""Admin Review Required
=====================

An intake requires manual review.

- Client ID: {client_id}
- Name: {client_data.get('FirstName', '')} {client_data.get('LastName', '')}
- Email: {client_data.get('Email', '')}
- State: {client_data.get('StateShort', '')}
- Reason: {reason}

Please review this client in IntakeQ and take appropriate action.
"""
        return self.send_admin_alert(f"Admin Review Required - Client {client_id}", body)

    def send_stale_reactivation_alert(self, client_id):
        """A stale account submitted a new intake. ClientId only (no PII)."""
        body = f"""Stale Account Reactivation Alert
==================================

A client whose account was previously stale has submitted a new intake.

Client ID: {client_id}
"""
        return self.send_admin_alert("Pills By Post Alert - Stale Account was Reactivated", body)
