from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .config import settings
from .events import event_kind, normalize_event
from .feishu import FeishuDeliveryError, send_to_feishu
from .formatter import format_event

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("gitlab-webhook")
app = FastAPI(title="GitLab Feishu Webhook Relay", version="1.0.0")


class DedupeCache:
    def __init__(self, ttl: int, max_items: int = 10000) -> None:
        self.ttl = ttl
        self.max_items = max_items
        self.items: OrderedDict[str, float] = OrderedDict()

    def seen(self, key: str) -> bool:
        now = time.time()
        for old_key, timestamp in list(self.items.items()):
            if now - timestamp > self.ttl:
                self.items.pop(old_key, None)
        if key in self.items:
            self.items.move_to_end(key)
            return True
        self.items[key] = now
        while len(self.items) > self.max_items:
            self.items.popitem(last=False)
        return False

    def discard(self, key: str) -> None:
        self.items.pop(key, None)


dedupe = DedupeCache(settings.dedupe_ttl_seconds)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook/gitlab")
async def gitlab_webhook(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    if settings.gitlab_secret_token and request.headers.get("x-gitlab-token") != settings.gitlab_secret_token:
        raise HTTPException(status_code=401, detail="Invalid GitLab webhook token")
    header = request.headers.get("x-gitlab-event", "")
    # Duo callbacks explicitly reuse payload event_id across delivery retries.
    flow_event_id = payload.get("event_id") if event_kind(header, payload) == "duo_workflow" else None
    event_id = (flow_event_id if isinstance(flow_event_id, str) else None) or request.headers.get("idempotency-key") or request.headers.get("webhook-id") or request.headers.get("x-gitlab-webhook-uuid") or request.headers.get("x-gitlab-event-uuid")
    if not event_id:
        event_id = hashlib.sha256(raw).hexdigest()
    if dedupe.seen(event_id):
        return {"ok": True, "duplicate": True, "event_id": event_id}
    event = normalize_event(header, payload, event_id)
    message = format_event(event, settings.max_message_length)
    logger.info("received event=%s kind=%s project=%s event_id=%s", header, event.kind, event.project_label, event_id)
    try:
        result = await send_to_feishu(message, settings.feishu_webhook_url, settings.feishu_secret, settings.feishu_timeout, settings.feishu_retry_count, settings.feishu_retry_backoff)
    except FeishuDeliveryError as exc:
        # Let GitLab retry a failed delivery instead of suppressing it as a duplicate.
        dedupe.discard(event_id)
        logger.exception("delivery failed event_id=%s", event_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "event_id": event_id, "event": event.kind, "feishu": result}

