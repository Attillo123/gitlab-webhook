import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app import main
from app.feishu import FeishuDeliveryError


SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "official_events.json").read_text(encoding="utf-8"))["samples"]


def post(payload, headers=None):
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.post("/webhook/gitlab", json=payload, headers=headers or {})
    return asyncio.run(request())


@pytest.fixture(autouse=True)
def isolated_service(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, gitlab_secret_token="test-secret", feishu_webhook_url="https://example.com/hook"))
    monkeypatch.setattr(main, "dedupe", main.DedupeCache(60))


@pytest.mark.parametrize("sample", SAMPLES, ids=[f"delivery-{i}" for i in range(len(SAMPLES))])
def test_official_event_reaches_delivery(monkeypatch, sample):
    sent = []
    async def send(message, *args):
        sent.append(message)
        return {"code": 0}
    monkeypatch.setattr(main, "send_to_feishu", send)
    response = post(sample["payload"], {"X-Gitlab-Event": sample["header"], "X-Gitlab-Token": "test-secret"})
    assert response.status_code == 200
    assert len(sent) == 1
    assert sent[0]["msg_type"] == "post"
    assert sent[0]["content"]["post"]["zh_cn"]["content"]


def test_token_must_be_valid_before_delivery(monkeypatch):
    async def never_send(*args):
        pytest.fail("Unauthorized request was forwarded")
    monkeypatch.setattr(main, "send_to_feishu", never_send)
    assert post({}, {"X-Gitlab-Token": "wrong"}).status_code == 401


def test_failure_can_be_retried_and_success_is_deduplicated(monkeypatch):
    attempts = []
    async def send(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise FeishuDeliveryError("temporary failure")
        return {"code": 0}
    monkeypatch.setattr(main, "send_to_feishu", send)
    headers = {"X-Gitlab-Token": "test-secret", "Idempotency-Key": "retry-test"}
    assert post({"object_kind": "push"}, headers).status_code == 502
    assert post({"object_kind": "push"}, headers).status_code == 200
    assert post({"object_kind": "push"}, headers).json()["duplicate"] is True
    assert len(attempts) == 2


def test_duo_callback_retry_uses_payload_event_id(monkeypatch):
    sent = []
    async def send(*args):
        sent.append(True)
        return {"code": 0}
    monkeypatch.setattr(main, "send_to_feishu", send)
    sample = next(s for s in SAMPLES if s["section"] == "gitlab-duo-flow-events")
    for request_id in ("first-delivery", "second-delivery"):
        response = post(sample["payload"], {
            "X-Gitlab-Token": "test-secret", "X-Gitlab-Event": sample["header"],
            "Idempotency-Key": request_id,
        })
        assert response.status_code == 200
    assert response.json()["duplicate"] is True
    assert len(sent) == 1


@pytest.mark.parametrize("event_name", ["user_create", "user_destroy", "project_create", "user_add_to_group", ""])
def test_system_events_are_forwarded_without_filtering(monkeypatch, event_name):
    sent = []
    async def send(message, *args):
        sent.append(message)
        return {"code": 0}
    monkeypatch.setattr(main, "send_to_feishu", send)
    response = post(
        {"event_name": event_name, "name": "New User", "username": "new-user", "user_id": 13},
        {"X-Gitlab-Event": "System Hook", "X-Gitlab-Token": "test-secret"},
    )
    assert response.status_code == 200
    assert "ignored" not in response.json()
    assert len(sent) == 1


def test_selected_system_repository_update_is_delivered(monkeypatch):
    sent = []
    async def send(message, *args):
        sent.append(message)
        return {"code": 0}
    monkeypatch.setattr(main, "send_to_feishu", send)
    response = post({
        "event_name": "repository_update", "user_name": "Operator", "project_id": 17,
        "changes": [{"ref": "refs/heads/feature", "before": "abc", "after": "0" * 40}],
    }, {"X-Gitlab-Event": "System Hook", "X-Gitlab-Token": "test-secret"})
    assert response.status_code == 200
    assert "ignored" not in response.json()
    text = json.dumps(sent[0])
    assert "Deleted branch: feature" in text
    assert "Operator: Operator" in text


@pytest.mark.parametrize("sample", json.loads((Path(__file__).parent / "fixtures" / "official_system_events.json").read_text(encoding="utf-8"))["samples"])
def test_official_system_events_are_delivered(monkeypatch, sample):
    sent = []
    async def send(message, *args):
        sent.append(message)
        return {"code": 0}
    monkeypatch.setattr(main, "send_to_feishu", send)
    response = post(sample["payload"], {"X-Gitlab-Event": "System Hook", "X-Gitlab-Token": "test-secret"})
    assert response.status_code == 200
    assert "ignored" not in response.json()
    assert len(sent) == 1
    assert "unknown-project" not in json.dumps(sent[0])


def test_system_event_requires_authentication(monkeypatch):
    async def never_send(*args):
        pytest.fail("Unauthorized System Hook reached Feishu")
    monkeypatch.setattr(main, "send_to_feishu", never_send)
    response = post({"event_name": "user_create"}, {"X-Gitlab-Event": "System Hook", "X-Gitlab-Token": "wrong"})
    assert response.status_code == 401
