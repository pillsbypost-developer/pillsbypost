"""
Honeybee Health Partners API client (Nectar fulfillment).

Auth: OAuth2 client-credentials at HONEYBEE_AUTH_URL with scope=partners;
tokens last ~12h and are cached in module memory (survives warm Lambda
invocations). Credentials come from Secrets Manager (HONEYBEE_OAUTH_SECRET:
{"client_id", "client_secret"}). Base URL from HONEYBEE_API_BASE
(sandbox in dev, production in prod).

Webhook verification (spec "Webhook Security"):
    base = escape(method + url + body)
    key  = sha256_hex_digest(client_secret)
    X-Honeybee-Signature = base64(hmac_sha1(key, base)) + "\\n"

PHI note: patient names/DOB and drug names/NDC/sig are DEBUG-only in logs;
patient_id and order_number are opaque and fine at INFO.
"""
import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import quote

import boto3
import requests

REQUEST_TIMEOUT = (5, 30)

# Module-level token cache: (access_token, expires_at_epoch)
_TOKEN_CACHE = {'token': None, 'expires_at': 0}


class HoneybeeError(RuntimeError):
    """Non-2xx response from the Partners API. Carries status and body."""

    def __init__(self, status_code, body, path):
        super().__init__(f"Honeybee {status_code} on {path}: {body[:500] if body else ''}")
        self.status_code = status_code
        self.body = body
        self.path = path


class HoneybeeClient:
    def __init__(self, logger):
        self.logger = logger
        self.base_url = os.environ['HONEYBEE_API_BASE'].rstrip('/')
        self.auth_url = os.environ['HONEYBEE_AUTH_URL']
        self._secret_id = os.environ['HONEYBEE_OAUTH_SECRET']
        self._credentials = None

    # ---- auth --------------------------------------------------------------

    def _load_credentials(self):
        if self._credentials is None:
            client = boto3.session.Session().client('secretsmanager')
            secret = json.loads(client.get_secret_value(SecretId=self._secret_id)['SecretString'])
            self._credentials = (secret['client_id'], secret['client_secret'])
        return self._credentials

    @property
    def client_secret(self):
        return self._load_credentials()[1]

    def _token(self, force_refresh=False):
        if not force_refresh and _TOKEN_CACHE['token'] and time.time() < _TOKEN_CACHE['expires_at'] - 300:
            return _TOKEN_CACHE['token']

        client_id, client_secret = self._load_credentials()
        response = requests.post(
            self.auth_url,
            data={'grant_type': 'client_credentials', 'client_id': client_id,
                  'client_secret': client_secret, 'scope': 'partners'},
            headers={'Accept': 'application/json'},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            raise HoneybeeError(response.status_code, response.text, 'oauth/token')
        payload = response.json()
        _TOKEN_CACHE['token'] = payload['access_token']
        _TOKEN_CACHE['expires_at'] = time.time() + int(payload.get('expires_in', 43200))
        self.logger.log('INFO', "Honeybee token refreshed", expires_in=payload.get('expires_in'))
        return _TOKEN_CACHE['token']

    def _request(self, method, path, json_body=None, params=None, _retry=True):
        """Authenticated request; refreshes the token once on 401. Raises HoneybeeError on non-2xx."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {'Authorization': f"Bearer {self._token()}", 'Accept': 'application/json'}
        response = requests.request(method, url, headers=headers, json=json_body, params=params,
                                    timeout=REQUEST_TIMEOUT)
        if response.status_code == 401 and _retry:
            self._token(force_refresh=True)
            return self._request(method, path, json_body=json_body, params=params, _retry=False)
        if response.status_code >= 300:
            self.logger.log('ERROR', "Honeybee API error", method=method, path=path,
                            status=response.status_code)
            self.logger.log('DEBUG', "Honeybee error body", body=response.text)
            raise HoneybeeError(response.status_code, response.text, path)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ---- patients / prescriptions ------------------------------------------

    def get_patient(self, patient_id):
        return self._request('GET', f"patients/{patient_id}")

    def get_prescriptions(self, patient_id):
        """List of medication_requests (each has id/prescription_id, active, drug_name, ndc...)."""
        data = self._request('GET', f"patients/{patient_id}/rx") or {}
        return data.get('medication_requests', [])

    def get_active_prescription_ids(self, patient_id):
        return [rx['id'] for rx in self.get_prescriptions(patient_id) if rx.get('active')]

    # ---- orders ------------------------------------------------------------

    def create_order(self, patient_id, prescription_ids, shipping_method_id, address=None,
                     signature_confirmation=None):
        """
        POST /orders (partner-requested fill). Returns the order dict
        (order_number, status_name, ...). Raises HoneybeeError on failure.
        """
        body = {
            'patient': {'patient_id': str(patient_id)},
            'items': [{'prescription_id': int(pid)} for pid in prescription_ids],
            'shipping': {'method_id': int(shipping_method_id)},
        }
        if signature_confirmation is not None:
            body['shipping']['signature_confirmation'] = bool(signature_confirmation)
        if address:
            body['address'] = address
        self.logger.log('INFO', "Creating Honeybee order", patient_id=patient_id,
                        prescription_count=len(prescription_ids), shipping_method_id=shipping_method_id)
        self.logger.log('DEBUG', "Honeybee order payload", payload=body)
        return self._request('POST', 'orders', json_body=body)

    def get_order(self, order_number):
        return self._request('GET', f"orders/{order_number}")

    def cancel_order(self, order_number):
        """DELETE /orders/{order_number}; only succeeds before the order is filled."""
        self.logger.log('INFO', "Canceling Honeybee order", order_number=order_number)
        return self._request('DELETE', f"orders/{order_number}")

    def request_order_action(self, order_number, event_key, reason_description=None):
        """POST /orders/{n}/partner_order_action_requests - resolve an ORDER_EXCEPTION."""
        body = {'event_key': event_key}
        if reason_description:
            body['reason_description'] = reason_description
        return self._request('POST', f"orders/{order_number}/partner_order_action_requests", json_body=body)

    def get_shipping_methods(self):
        data = self._request('GET', 'shipping_methods') or {}
        return data.get('shipping_methods', [])

    # ---- sandbox / webhooks ------------------------------------------------

    def simulate_rx_received(self, medication_requests):
        """Sandbox only: POST /simulate fires an RX_RECEIVED webhook at our endpoint."""
        return self._request('POST', 'simulate', json_body={
            'simulation_type': 'event',
            'simulation': {'event_type': 'RX_RECEIVED', 'medication_requests': medication_requests},
        })

    def replay_webhook(self, event_id):
        return self._request('PATCH', f"webhooks/{event_id}")

    # ---- webhook signature -------------------------------------------------

    @staticmethod
    def compute_signature(client_secret, method, url, body):
        """
        Reference implementation of the X-Honeybee-Signature algorithm.
        body is the raw request body string (not re-serialised JSON).
        """
        base = quote(f"{method.upper()}{url}{body or ''}", safe='')
        key = hashlib.sha256(client_secret.encode('utf-8')).hexdigest().encode('utf-8')
        digest = hmac.new(key, base.encode('utf-8'), hashlib.sha1).digest()
        return base64.b64encode(digest).decode('ascii') + "\n"

    def verify_signature(self, signature_header, method, url, body):
        """
        Constant-time comparison of the header against our computation.
        Honeybee URL-escapes the base string; escape() in their pseudo-code is
        ambiguous, so both the escaped and unescaped bases are accepted.
        """
        if not signature_header:
            return False
        provided = signature_header.strip()
        secret = self.client_secret
        candidates = [self.compute_signature(secret, method, url, body).strip()]
        key = hashlib.sha256(secret.encode('utf-8')).hexdigest().encode('utf-8')
        raw_base = f"{method.upper()}{url}{body or ''}".encode('utf-8')
        candidates.append(base64.b64encode(hmac.new(key, raw_base, hashlib.sha1).digest()).decode('ascii'))
        return any(hmac.compare_digest(provided, c) for c in candidates)

    @staticmethod
    def compute_secret_header(client_secret, webhook_secret):
        """X-Honeybee-Secret = base64(hmac_sha1(sha256_hex(client_secret), webhook_secret)) + '\\n'."""
        key = hashlib.sha256(client_secret.encode('utf-8')).hexdigest().encode('utf-8')
        digest = hmac.new(key, webhook_secret.encode('utf-8'), hashlib.sha1).digest()
        return base64.b64encode(digest).decode('ascii') + "\n"

    def verify_secret_header(self, secret_header, webhook_secret):
        if not secret_header or not webhook_secret:
            return False
        expected = self.compute_secret_header(self.client_secret, webhook_secret).strip()
        return hmac.compare_digest(secret_header.strip(), expected)
