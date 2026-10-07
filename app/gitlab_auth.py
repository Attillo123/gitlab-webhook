from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from collections.abc import Mapping


class GitLabAuthenticationError(ValueError):
    pass


class GitLabAuthenticationNotConfigured(RuntimeError):
    pass


def _decode_signing_key(token: str) -> bytes:
    encoded = token.removeprefix("whsec_")
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GitLabAuthenticationError("Invalid configured GitLab signing token") from exc
    if not key:
        raise GitLabAuthenticationError("Invalid configured GitLab signing token")
    return key


def verify_gitlab_request(
    headers: Mapping[str, str],
    body: bytes,
    signing_token: str,
    timestamp_tolerance: int = 300,
    now: float | None = None,
) -> str:
    """Verify GitLab's Standard Webhooks signature for one configured hook."""
    signing_token = signing_token.strip()
    received_signature = headers.get("webhook-signature", "")

    if not signing_token:
        raise GitLabAuthenticationNotConfigured("Configure a signing token for this GitLab hook ID")
    if not received_signature:
        raise GitLabAuthenticationError("Missing webhook signature")
    message_id = headers.get("webhook-id", "")
    timestamp = headers.get("webhook-timestamp", "")
    if not message_id or not timestamp:
        raise GitLabAuthenticationError("Missing signed webhook headers")
    try:
        signed_at = int(timestamp)
    except ValueError as exc:
        raise GitLabAuthenticationError("Invalid webhook timestamp") from exc
    current_time = time.time() if now is None else now
    if timestamp_tolerance < 0 or abs(current_time - signed_at) > timestamp_tolerance:
        raise GitLabAuthenticationError("Webhook timestamp is outside the allowed time window")

    key = _decode_signing_key(signing_token)
    signed_content = message_id.encode("utf-8") + b"." + timestamp.encode("ascii") + b"." + body
    digest = hmac.new(key, signed_content, hashlib.sha256).digest()
    expected = "v1," + base64.b64encode(digest).decode("ascii")
    if not any(hmac.compare_digest(expected, candidate) for candidate in received_signature.split()):
        raise GitLabAuthenticationError("Invalid webhook signature")
    return "signature"
