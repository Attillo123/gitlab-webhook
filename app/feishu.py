from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time
from typing import Any

import httpx


class FeishuDeliveryError(RuntimeError):
    pass


def signature(timestamp: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


async def send_to_feishu(payload: dict[str, Any], url: str, secret: str, timeout: float, retries: int, backoff: float) -> dict[str, Any]:
    if not url:
        raise FeishuDeliveryError("FEISHU_WEBHOOK_URL is not configured")
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(retries + 1):
            body = dict(payload)
            if secret:
                timestamp = str(int(time.time()))
                body["timestamp"] = timestamp
                body["sign"] = signature(timestamp, secret)
            try:
                response = await client.post(url, json=body)
                response.raise_for_status()
                result = response.json()
                if isinstance(result, dict) and result.get("code", 0) not in (0, None):
                    raise FeishuDeliveryError(f"Feishu rejected message: {result}")
                return result if isinstance(result, dict) else {"data": result}
            except (httpx.HTTPError, ValueError, FeishuDeliveryError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(backoff * (2**attempt))
    raise FeishuDeliveryError(f"Feishu delivery failed after {retries + 1} attempts: {last_error}")

