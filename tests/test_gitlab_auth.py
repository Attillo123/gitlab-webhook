import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import replace

import httpx
import pytest

from app import main
from app.gitlab_auth import (
    GitLabAuthenticationError,
    GitLabAuthenticationNotConfigured,
    verify_gitlab_request,
)


BOT_ID = "c33921dd-bd41-415a-b4cb-7b1339da8e86"
RAW_SIGNING_KEY = b"local-test-signing-key"
SIGNING_TOKEN = "whsec_" + base64.b64encode(RAW_SIGNING_KEY).decode("ascii")
LEGACY_TOKEN = "legacy-secret-token"


def signed_headers(body: bytes, *, message_id="msg-123", timestamp="1700000000", extra_signatures=()):
    message = message_id.encode() + b"." + timestamp.encode() + b"." + body
    digest = hmac.new(RAW_SIGNING_KEY, message, hashlib.sha256).digest()
    signature = "v1," + base64.b64encode(digest).decode("ascii")
    values = [*extra_signatures, signature]
    return {
        "webhook-id": message_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": " ".join(values),
    }


def test_verifies_gitlab_official_hmac_format():
    body = b'{"event_name":"user_create"}'
    headers = signed_headers(body, timestamp="1700000000", extra_signatures=("v2,ignored", "v1,invalid"))
    assert verify_gitlab_request(headers, body, SIGNING_TOKEN, "", now=1700000000) == "signature"


def test_signature_covers_exact_raw_body():
    body = b'{"value": 1}'
    headers = signed_headers(body, timestamp="1700000000")
    with pytest.raises(GitLabAuthenticationError, match="Invalid webhook signature"):
        verify_gitlab_request(headers, b'{"value":1}', SIGNING_TOKEN, "", now=1700000000)


def test_signature_rejects_missing_signed_headers():
    with pytest.raises(GitLabAuthenticationError, match="Missing signed webhook headers"):
        verify_gitlab_request({"webhook-signature": "v1,bad"}, b"{}", SIGNING_TOKEN, LEGACY_TOKEN, now=1700000000)


def test_signature_rejects_stale_or_future_timestamp():
    for timestamp in ("1699999699", "1700000301"):
        with pytest.raises(GitLabAuthenticationError, match="outside the allowed time window"):
            verify_gitlab_request(signed_headers(b"{}", timestamp=timestamp), b"{}", SIGNING_TOKEN, "", now=1700000000)


def test_signature_accepts_timestamp_inside_window():
    headers = signed_headers(b"{}", timestamp="1699999700")
    assert verify_gitlab_request(headers, b"{}", SIGNING_TOKEN, "", now=1700000000) == "signature"


def test_invalid_signature_does_not_fall_back_to_legacy_token():
    headers = signed_headers(b"{}", timestamp="1700000000")
    headers["webhook-signature"] = "v1,invalid"
    headers["x-gitlab-token"] = LEGACY_TOKEN
    with pytest.raises(GitLabAuthenticationError, match="Invalid webhook signature"):
        verify_gitlab_request(headers, b"{}", SIGNING_TOKEN, LEGACY_TOKEN, now=1700000000)


def test_unsigned_legacy_delivery_is_supported_during_migration():
    assert verify_gitlab_request({"x-gitlab-token": LEGACY_TOKEN}, b"{}", SIGNING_TOKEN, LEGACY_TOKEN, now=1700000000) == "secret_token"
    with pytest.raises(GitLabAuthenticationError, match="missing signature"):
        verify_gitlab_request({"x-gitlab-token": "wrong"}, b"{}", SIGNING_TOKEN, LEGACY_TOKEN, now=1700000000)


def test_signing_only_mode_rejects_unsigned_requests():
    with pytest.raises(GitLabAuthenticationError, match="Missing webhook signature"):
        verify_gitlab_request({}, b"{}", SIGNING_TOKEN, "", now=1700000000)


def test_no_configured_authentication_fails_closed():
    with pytest.raises(GitLabAuthenticationNotConfigured):
        verify_gitlab_request({}, b"{}", "", "", now=1700000000)


def test_invalid_signing_token_fails_closed():
    headers = signed_headers(b"{}", timestamp="1700000000")
    with pytest.raises(GitLabAuthenticationError, match="Invalid configured"):
        verify_gitlab_request(headers, b"{}", "whsec_%%%", LEGACY_TOKEN, now=1700000000)


def test_signature_authentication_at_endpoint(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(
        main.settings,
        gitlab_signing_token=SIGNING_TOKEN,
        gitlab_secret_token=LEGACY_TOKEN,
        gitlab_webhook_tolerance_seconds=300,
    ))
    monkeypatch.setattr(main, "dedupe", main.DedupeCache(60))
    sent = []

    async def send(message, *args):
        sent.append(message)
        return {"code": 0}

    monkeypatch.setattr(main, "send_to_feishu", send)
    timestamp = str(int(__import__("time").time()))
    body = json.dumps({"event_name": "user_create", "name": "Test User", "user_id": 1}, separators=(",", ":")).encode()
    headers = signed_headers(body, message_id="endpoint-message", timestamp=timestamp)
    headers["X-Gitlab-Event"] = "System Hook"
    headers["X-Gitlab-Token"] = LEGACY_TOKEN

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.post(f"/webhook/gitlab/{BOT_ID}", content=body, headers=headers)

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json()["event"] == "system"
    assert len(sent) == 1


def test_invalid_signature_is_rejected_by_endpoint_even_with_legacy_header(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(
        main.settings,
        gitlab_signing_token=SIGNING_TOKEN,
        gitlab_secret_token=LEGACY_TOKEN,
        gitlab_webhook_tolerance_seconds=300,
    ))
    monkeypatch.setattr(main, "dedupe", main.DedupeCache(60))
    body = b'{"object_kind":"push"}'
    headers = signed_headers(body, timestamp=str(int(__import__("time").time())))
    headers["webhook-signature"] = "v1,invalid"
    headers["X-Gitlab-Token"] = LEGACY_TOKEN

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.post(f"/webhook/gitlab/{BOT_ID}", content=body, headers=headers)

    response = asyncio.run(request())
    assert response.status_code == 401

