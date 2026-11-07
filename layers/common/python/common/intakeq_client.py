import json
import os
import time

import boto3
import requests
from botocore.exceptions import ClientError

# (connect, read) timeouts in seconds for all IntakeQ API calls.
REQUEST_TIMEOUT = (5, 30)


class IntakeQClient:
    """
    Thin client for the IntakeQ Developer API (https://intakeq.com/api/v1).
    The API key lives in Secrets Manager; INTAKEQ_API_SECRET names the secret
    (ARN or name). Read-style methods return None/[] on request errors;
    write-style methods return True/False or the response body/None.
    """

    def __init__(self, logger, api_delay=0):
        self.logger = logger
        self.api_delay = api_delay
        self.base_url = "https://intakeq.com/api/v1"
        self.api_key = self._get_api_key()
        self.headers = {"X-Auth-Key": self.api_key, "Content-Type": "application/json"}

    def _get_api_key(self):
        secret_id = os.environ['INTAKEQ_API_SECRET']
        client = boto3.session.Session().client('secretsmanager')
        try:
            secret = json.loads(client.get_secret_value(SecretId=secret_id)['SecretString'])
            return secret['api_key']
        except ClientError as e:
            self.logger.log('ERROR', f"Error retrieving IntakeQ API key: {str(e)}")
            raise

    def _sleep(self):
        if self.api_delay:
            time.sleep(self.api_delay)

    # ---- clients -----------------------------------------------------------

    def _search_client_exact(self, client_id):
        """The /clients search returns partial matches; filter to the exact ClientId."""
        response = requests.get(f"{self.base_url}/clients", headers=self.headers,
                                params={"includeProfile": "true", "search": client_id},
                                timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        for client in response.json():
            if str(client.get('ClientId')) == str(client_id):
                return client
        return None

    def get_client_by_id(self, client_id):
        """Client profile by ClientId (exact match), or None on error/not found."""
        try:
            client = self._search_client_exact(client_id)
            self._sleep()
            return client
        except requests.RequestException as e:
            self.logger.log('ERROR', f"Error getting client data: {str(e)}")
            self._sleep()
            return None

    def search_clients_by_email(self, email):
        """Clients whose Email matches exactly (case-insensitive)."""
        try:
            response = requests.get(f"{self.base_url}/clients", headers=self.headers,
                                    params={"includeProfile": "true", "search": email},
                                    timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            wanted = email.lower().strip()
            matches = [c for c in response.json() if (c.get('Email') or '').lower().strip() == wanted]
            self._sleep()
            return matches
        except requests.RequestException as e:
            self.logger.log('ERROR', f"Error searching clients by email: {str(e)}")
            self._sleep()
            return []

    def search_clients_by_custom_field(self, field_id, value):
        """Clients whose custom field `field_id` equals value (phone duplicate checks)."""
        try:
            response = requests.get(f"{self.base_url}/clients", headers=self.headers,
                                    params={"includeProfile": "true", f"custom.{field_id}": value},
                                    timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self._sleep()
            return response.json()
        except requests.RequestException as e:
            self.logger.log('ERROR', f"Error searching clients by custom field: {str(e)}")
            self._sleep()
            return []

    def search_clients_by_name(self, full_name):
        try:
            response = requests.get(f"{self.base_url}/clients", headers=self.headers,
                                    params={"includeProfile": "true", "search": full_name},
                                    timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self._sleep()
            return response.json()
        except requests.RequestException as e:
            self.logger.log('ERROR', f"Error searching clients by name: {str(e)}")
            self._sleep()
            return []

    def update_client(self, client_data):
        """
        POST the full client object (IntakeQ requires GET -> modify -> POST).
        Returns the response body, or None on failure.
        """
        try:
            response = requests.post(f"{self.base_url}/clients", headers=self.headers,
                                     json=client_data, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self._sleep()
            return response.json()
        except requests.RequestException as e:
            detail = e.response.text if getattr(e, 'response', None) is not None else ''
            self.logger.log('ERROR', f"Error updating client: {str(e)}", response_body=detail)
            self._sleep()
            return None

    # ---- tags --------------------------------------------------------------

    def update_client_tags(self, client_id, tag, remove=False):
        """Add (remove=False) or remove (remove=True) one tag. Returns True/False."""
        try:
            if remove:
                response = requests.delete(f"{self.base_url}/clientTags", headers=self.headers,
                                           params={"clientId": client_id, "tag": tag},
                                           timeout=REQUEST_TIMEOUT)
            else:
                response = requests.post(f"{self.base_url}/clientTags", headers=self.headers,
                                         json={"ClientID": client_id, "Tag": tag},
                                         timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self._sleep()
            return True
        except requests.RequestException as e:
            self.logger.log('ERROR', f"Error {'removing' if remove else 'adding'} tag: {str(e)}", tag=tag)
            self._sleep()
            return False

    # ---- intakes -----------------------------------------------------------

    def get_intake_by_id(self, intake_id):
        try:
            response = requests.get(f"{self.base_url}/intakes/{intake_id}", headers=self.headers,
                                    timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self._sleep()
            return response.json()
        except requests.RequestException as e:
            self.logger.log('ERROR', f"Error getting intake data: {str(e)}")
            self._sleep()
            return None

    # ---- appointments ------------------------------------------------------

    def get_appointment(self, appointment_id):
        """Full appointment object (ServiceId, ServiceName, PractitionerId, Status...)."""
        try:
            response = requests.get(f"{self.base_url}/appointments/{appointment_id}",
                                    headers=self.headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            self._sleep()
            return response.json()
        except requests.RequestException as e:
            self.logger.log('ERROR', f"Error getting appointment: {str(e)}")
            self._sleep()
            return None

    def get_appointment_settings(self):
        """{'Services': [{Id, Name, ...}], 'Locations': [...], 'Practitioners': [...]}. Raises on error."""
        response = requests.get(f"{self.base_url}/appointments/settings", headers=self.headers,
                                timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def get_practitioners(self):
        try:
            response = requests.get(f"{self.base_url}/practitioners", headers=self.headers,
                                    timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json() or []
        except requests.RequestException as e:
            self.logger.log('ERROR', f"Error getting practitioners: {str(e)}")
            return []
