"""
Elavon Converge client - transaction verification for payment confirmations.

DECISION D4 IS OPEN: how Converge notifies us of a completed payment is not
settled (browser return-URL POST vs scheduled reconciliation vs Elavon
Payment Gateway webhooks). Whatever the input side turns out to be, a
notification must never be trusted on its own: payment-webhook calls
`query_transaction` and only tags PAID when Converge confirms the
transaction was approved for the expected customer id.

Credentials: Secrets Manager CONVERGE_SECRET {"merchant_id", "user_id", "pin"}.
Endpoint: CONVERGE_API_BASE + /processxml.do (XML API, ssl_transaction_type=txnquery).
"""
import json
import os
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import boto3
import requests

REQUEST_TIMEOUT = (5, 30)


class ConvergeClient:
    def __init__(self, logger):
        self.logger = logger
        self.base_url = os.environ['CONVERGE_API_BASE'].rstrip('/')
        self._secret_id = os.environ['CONVERGE_SECRET']
        self._credentials = None

    def _load_credentials(self):
        if self._credentials is None:
            client = boto3.session.Session().client('secretsmanager')
            self._credentials = json.loads(client.get_secret_value(SecretId=self._secret_id)['SecretString'])
        return self._credentials

    def _xml_request(self, fields):
        creds = self._load_credentials()
        all_fields = {
            'ssl_merchant_id': creds['merchant_id'],
            'ssl_user_id': creds['user_id'],
            'ssl_pin': creds['pin'],
            **fields,
        }
        xml = '<txn>' + ''.join(f'<{k}>{escape(str(v))}</{k}>' for k, v in all_fields.items()) + '</txn>'
        response = requests.post(f"{self.base_url}/processxml.do", data={'xmldata': xml},
                                 timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        return {child.tag: (child.text or '') for child in root}

    def query_transaction(self, txn_id):
        """
        Look up a transaction by ssl_txn_id. Returns the parsed field dict
        (ssl_result, ssl_result_message, ssl_amount, ssl_customer_id,
        ssl_invoice_number, ssl_trans_status...) or raises on transport error.
        Never logs card data; Converge does not return PAN in txnquery.
        """
        self.logger.log('INFO', "Querying Converge transaction", txn_id=txn_id)
        return self._xml_request({'ssl_transaction_type': 'txnquery', 'ssl_txn_id': txn_id})

    @staticmethod
    def is_approved(txn):
        """Approved = ssl_result == '0' (Converge convention) and a settled/approved status."""
        return str(txn.get('ssl_result', '')).strip() == '0' and \
            str(txn.get('ssl_trans_status', 'STL')).strip().upper() in ('STL', 'OPN', 'PEN', 'APPROVED')
