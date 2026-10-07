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
HOOK_ID = "project-a"
RAW_SIGNING_KEY = b"local-test-signing-key"
SIGNING_TOKEN = "whsec_" + base64.b64encode(RAW_SIGNING_KEY).decode("ascii")


def signed_headers(body: bytes, *, message_id="msg-123", timestamp="1700000000", extra_signatures=(), key=RAW_SIGNING_KEY):
    message = message_id.encode() + b"." + timestamp.encode() + b"." + body
    digest = hmac.new(key, message, hashlib.sha256).digest()
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
    assert verify_gitlab_request(headers, body, SIGNING_TOKEN, now=1700000000) == "signature"


def test_signature_covers_exact_raw_body():
    body = b'{"value": 1}'
    headers = signed_headers(body, timestamp="1700000000")
    with pytest.raises(GitLabAuthenticationError, match="Invalid webhook signature"):
        verify_gitlab_request(headers, b'{"value":1}', SIGNING_TOKEN, now=1700000000)


def test_signature_rejects_missing_signed_headers():
    with pytest.raises(GitLabAuthenticationError, match="Missing signed webhook headers"):
        verify_gitlab_request({"webhook-signature": "v1,bad"}, b"{}", SIGNING_TOKEN, now=1700000000)


def test_signature_rejects_stale_or_future_timestamp():
    for timestamp in ("1699999699", "1700000301"):
        with pytest.raises(GitLabAuthenticationError, match="outside the allowed time window"):
            verify_gitlab_request(signed_headers(b"{}", timestamp=timestamp), b"{}", SIGNING_TOKEN, now=1700000000)


def test_signature_accepts_timestamp_inside_window():
    headers = signed_headers(b"{}", timestamp="1699999700")
    assert verify_gitlab_request(headers, b"{}", SIGNING_TOKEN, now=1700000000) == "signature"


def test_invalid_signature_is_rejected():
    headers = signed_headers(b"{}", timestamp="1700000000")
    headers["webhook-signature"] = "v1,invalid"
    with pytest.raises(GitLabAuthenticationError, match="Invalid webhook signature"):
        verify_gitlab_request(headers, b"{}", SIGNING_TOKEN, now=1700000000)


def test_unsigned_request_is_rejected_even_with_secret_token_header():
    with pytest.raises(GitLabAuthenticationError, match="Missing webhook signature"):
        verify_gitlab_request({"x-gitlab-token": "anything"}, b"{}", SIGNING_TOKEN, now=1700000000)


def test_no_configured_authentication_fails_closed():
    with pytest.raises(GitLabAuthenticationNotConfigured):
        verify_gitlab_request({}, b"{}", "", now=1700000000)


def test_invalid_signing_token_fails_closed():
    headers = signed_headers(b"{}", timestamp="1700000000")
    with pytest.raises(GitLabAuthenticationError, match="Invalid configured"):
        verify_gitlab_request(headers, b"{}", "whsec_%%%", now=1700000000)


def test_endpoint_uses_signing_token_selected_by_hook_id(monkeypatch):
    other_key = b"other-test-signing-key"
    other_token = "whsec_" + base64.b64encode(other_key).decode("ascii")
    monkeypatch.setattr(main, "settings", replace(
        main.settings,
        gitlab_signing_tokens={HOOK_ID: SIGNING_TOKEN, "project-b": other_token},
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
    headers = signed_headers(body, message_id="endpoint-hook-message", timestamp=timestamp)
    headers["X-Gitlab-Event"] = "System Hook"

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.post(f"/webhook/gitlab/{BOT_ID}/{HOOK_ID}", content=body, headers=headers)

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json()["event"] == "system"
    assert len(sent) == 1

    wrong_key_headers = signed_headers(
        body,
        message_id="wrong-hook-key-message",
        timestamp=timestamp,
        key=other_key,
    )

    async def wrong_key_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.post(f"/webhook/gitlab/{BOT_ID}/{HOOK_ID}", content=body, headers=wrong_key_headers)

    assert asyncio.run(wrong_key_request()).status_code == 401


def test_unknown_hook_id_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, gitlab_signing_tokens={HOOK_ID: SIGNING_TOKEN}))

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.post(f"/webhook/gitlab/{BOT_ID}/unknown", json={})

    assert asyncio.run(request()).status_code == 404


def test_signature_authentication_at_endpoint(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(
        main.settings,
        gitlab_signing_tokens={HOOK_ID: SIGNING_TOKEN},
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

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.post(f"/webhook/gitlab/{BOT_ID}/{HOOK_ID}", content=body, headers=headers)

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json()["event"] == "system"
    assert len(sent) == 1


def test_invalid_signature_is_rejected_by_endpoint(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(
        main.settings,
        gitlab_signing_tokens={HOOK_ID: SIGNING_TOKEN},
        gitlab_webhook_tolerance_seconds=300,
    ))
    monkeypatch.setattr(main, "dedupe", main.DedupeCache(60))
    body = b'{"object_kind":"push"}'
    headers = signed_headers(body, timestamp=str(int(__import__("time").time())))
    headers["webhook-signature"] = "v1,invalid"

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.post(f"/webhook/gitlab/{BOT_ID}/{HOOK_ID}", content=body, headers=headers)

    response = asyncio.run(request())
    assert response.status_code == 401

