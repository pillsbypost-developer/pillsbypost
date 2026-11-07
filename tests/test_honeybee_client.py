"""common.honeybee_client: signature algorithm, token caching, request/error handling."""
import base64
import hashlib
import hmac
import json
from urllib.parse import quote

import pytest

from common import honeybee_client as hb
from common.honeybee_client import HoneybeeClient, HoneybeeError
from common.logger import CustomLogger


class FakeResponse:
    def __init__(self, status_code, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else (json.dumps(payload) if payload is not None else '')
        self.content = self.text.encode()

    def json(self):
        return self._payload


@pytest.fixture()
def client(aws_stack):
    hb._TOKEN_CACHE.update({'token': None, 'expires_at': 0})
    return HoneybeeClient(CustomLogger())


class TestSignature:
    def test_matches_reference_algorithm(self):
        secret, method, url, body = 'hb-secret', 'POST', 'https://x.example/hook', '{"a":1}'
        base = quote(f"{method}{url}{body}", safe='')
        key = hashlib.sha256(secret.encode()).hexdigest().encode()
        expected = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode() + "\n"
        assert HoneybeeClient.compute_signature(secret, method, url, body) == expected

    def test_verify_accepts_escaped_and_raw_bases(self, client):
        url, body = 'https://x.example/hook', '{"event_id":"e1"}'
        good = HoneybeeClient.compute_signature('hb-secret', 'POST', url, body)
        assert client.verify_signature(good, 'POST', url, body)
        key = hashlib.sha256(b'hb-secret').hexdigest().encode()
        raw = base64.b64encode(hmac.new(key, f"POST{url}{body}".encode(), hashlib.sha1).digest()).decode()
        assert client.verify_signature(raw, 'POST', url, body)

    def test_verify_rejects_bad_or_missing(self, client):
        url, body = 'https://x.example/hook', '{}'
        assert not client.verify_signature('nope', 'POST', url, body)
        assert not client.verify_signature('', 'POST', url, body)
        assert not client.verify_signature(None, 'POST', url, body)
        good = HoneybeeClient.compute_signature('hb-secret', 'POST', url, body)
        assert not client.verify_signature(good, 'POST', url, body + 'x')

    def test_secret_header(self, client):
        expected = HoneybeeClient.compute_secret_header('hb-secret', 'hook-secret')
        assert client.verify_secret_header(expected, 'hook-secret')
        assert not client.verify_secret_header(expected, 'other')
        assert not client.verify_secret_header(None, 'hook-secret')


class TestRequests:
    def test_token_cached_and_bearer_sent(self, client, monkeypatch):
        calls = []

        def fake_post(url, data=None, headers=None, timeout=None):
            calls.append(('POST', url, data))
            return FakeResponse(200, {'access_token': 'tok-1', 'expires_in': 43200})

        def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
            calls.append((method, url, headers['Authorization']))
            return FakeResponse(200, {'patient_id': 'p1', 'email': 'a@b.c'})

        monkeypatch.setattr(hb.requests, 'post', fake_post)
        monkeypatch.setattr(hb.requests, 'request', fake_request)

        assert client.get_patient('p1')['patient_id'] == 'p1'
        assert client.get_patient('p1')['patient_id'] == 'p1'
        token_calls = [c for c in calls if c[0] == 'POST' and c[1].endswith('/oauth/token')]
        assert len(token_calls) == 1
        assert token_calls[0][2]['scope'] == 'partners'
        assert all(c[2] == 'Bearer tok-1' for c in calls if c[0] == 'GET')

    def test_401_refreshes_once(self, client, monkeypatch):
        tokens = iter(['old', 'new'])
        monkeypatch.setattr(hb.requests, 'post', lambda *a, **k: FakeResponse(200, {'access_token': next(tokens), 'expires_in': 43200}))
        seen = []

        def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
            seen.append(headers['Authorization'])
            return FakeResponse(401, text='expired') if headers['Authorization'] == 'Bearer old' else FakeResponse(200, {'ok': True})

        monkeypatch.setattr(hb.requests, 'request', fake_request)
        assert client.get_order('X1') == {'ok': True}
        assert seen == ['Bearer old', 'Bearer new']

    def test_error_raises_honeybee_error(self, client, monkeypatch):
        monkeypatch.setattr(hb.requests, 'post', lambda *a, **k: FakeResponse(200, {'access_token': 't', 'expires_in': 100}))
        monkeypatch.setattr(hb.requests, 'request', lambda *a, **k: FakeResponse(422, text='{"error":"bad rx"}'))
        with pytest.raises(HoneybeeError) as exc:
            client.create_order('p1', [1], 7)
        assert exc.value.status_code == 422
        assert 'orders' in exc.value.path

    def test_create_order_payload(self, client, monkeypatch):
        monkeypatch.setattr(hb.requests, 'post', lambda *a, **k: FakeResponse(200, {'access_token': 't', 'expires_in': 100}))
        captured = {}

        def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
            captured.update(method=method, url=url, body=json)
            return FakeResponse(201, {'order_number': 'MXO72E'})

        monkeypatch.setattr(hb.requests, 'request', fake_request)
        result = client.create_order('p1', ['183601'], '7', address={'city': 'Detroit'}, signature_confirmation=False)
        assert result['order_number'] == 'MXO72E'
        assert captured['method'] == 'POST' and captured['url'].endswith('/v1/orders')
        assert captured['body'] == {
            'patient': {'patient_id': 'p1'},
            'items': [{'prescription_id': 183601}],
            'shipping': {'method_id': 7, 'signature_confirmation': False},
            'address': {'city': 'Detroit'},
        }

    def test_active_prescription_ids(self, client, monkeypatch):
        monkeypatch.setattr(hb.requests, 'post', lambda *a, **k: FakeResponse(200, {'access_token': 't', 'expires_in': 100}))
        monkeypatch.setattr(hb.requests, 'request', lambda *a, **k: FakeResponse(200, {'medication_requests': [
            {'id': 1, 'active': True}, {'id': 2, 'active': False}, {'id': 3, 'active': True}]}))
        assert client.get_active_prescription_ids('p1') == [1, 3]
