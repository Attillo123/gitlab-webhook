import copy
import json
from pathlib import Path

import pytest

from app.events import normalize_event
from app.formatter import format_event


DOCUMENT = json.loads((Path(__file__).parent / "fixtures" / "official_system_events.json").read_text(encoding="utf-8"))
SAMPLES = DOCUMENT["samples"]


def render(payload, header="System Hook"):
    event = normalize_event(header, payload)
    message = format_event(event)
    post = message["content"]["post"]["zh_cn"]
    text = post["title"] + "\n" + "\n".join("".join(item["text"] for item in line) for line in post["content"])
    links = [item["href"] for line in post["content"] for item in line if item["tag"] == "a"]
    return text, links


def name_of(payload):
    return payload.get("event_name") or payload.get("object_kind")


def sample_for(event_name):
    return copy.deepcopy(next(s["payload"] for s in SAMPLES if name_of(s["payload"]) == event_name))


DETAILS = {
    "project_create": ["Project created", "jsmith/storecloud", "Project Id: 74", "private", "John", "John Smith"],
    "project_destroy": ["Project deleted", "jsmith/underscore", "Project Id: 73", "internal"],
    "project_rename": ["Project renamed", "jsmith/overscore -> jsmith/underscore"],
    "project_transfer": ["Project transferred", "jsmith/overscore -> scores/underscore"],
    "project_update": ["Project updated", "StoreCloud", "Project Namespace Id: 23"],
    "group_create": ["Group created", "StoreCloud", "Group Id: 78", "storecloud"],
    "group_destroy": ["Group deleted", "StoreCloud", "Group Id: 78"],
    "group_rename": ["Group renamed", "Better Name", "parent-group/better-name", "parent-group/old-name"],
    "user_create": ["User created: John Smith (@js)", "User Id: 41"],
    "user_destroy": ["User deleted: John Smith (@js)", "User Id: 41"],
    "user_rename": ["User renamed: new-name (@new-exciting-name)", "old-boring-name -> new-exciting-name", "User Id: 58"],
    "user_failed_login": ["User login failed: John Smith (@user4)", "blocked", "User Id: 26"],
    "key_create": ["SSH key added: @root", "Id: 4", "Key type: ssh-rsa", "Fingerprint: SHA256:", "john@localhost"],
    "key_destroy": ["SSH key deleted: @root", "Id: 4", "Fingerprint: SHA256:"],
    "user_add_to_team": ["Project member added: jsmith/storecloud", "Member: John Smith (@johnsmith)", "Maintainer", "Project Id: 74"],
    "user_remove_from_team": ["Project member removed: jsmith/storecloud", "John Smith (@johnsmith)", "Maintainer"],
    "user_update_for_team": ["Project member role updated: jsmith/storecloud", "John Smith (@johnsmith)", "Maintainer"],
    "user_add_to_group": ["Group member added: StoreCloud", "Member: John Smith (@johnsmith)", "Group Id: 78", "Maintainer"],
    "user_remove_from_group": ["Group member removed: StoreCloud", "John Smith (@johnsmith)", "Maintainer"],
    "user_update_for_group": ["Group member role updated: StoreCloud", "John Smith (@johnsmith)", "Maintainer"],
    "user_access_request_to_project": ["Project access requested: jsmith/storecloud", "Member: John Smith (@johnsmith)", "Maintainer"],
    "user_access_request_revoked_for_project": ["Project access request canceled: jsmith/storecloud", "Member: John Smith (@johnsmith)"],
    "user_access_request_to_group": ["Group access requested: StoreCloud", "Member: John Smith (@johnsmith)"],
    "user_access_request_revoked_for_group": ["Group access request canceled: StoreCloud", "Member: John Smith (@johnsmith)"],
    "repository_update": ["Jsmith/Example", "Updated ref: master", "8205ea8d -> 4045ea7a", "Operator: John Smith"],
    "push": ["Mike/Diaspora:master", "Example User", "Add simple search", "Operator: John Smith"],
    "tag_push": ["Jsmith/Example:v1.0.0", "Created tag: v1.0.0", "Operator: John Smith"],
    "merge_request": ["GitlabHQ/Gitlab Test !1", "MS-Viewport", "Pull Request by user #51", "Operator: Administrator", "Source branch: ms-viewport", "Target branch: master", "Assignee: User1", "GitLab dev user"],
    "gitlab_subscription_member_approval": ["Member role promotion requested", "User Id: 42", "Requested By User Id: 99", "Promotion Namespace Id: 789", "Old Access Level: 10", "New Access Level: 30", "Existing Member Id: 123"],
    "gitlab_subscription_member_approvals": ["User Id: 42", "Reviewed By User Id: 101", "Status: success"],
}


def test_fixtures_cover_every_documented_system_type():
    assert set(DOCUMENT["documented_events"]) == {name_of(sample["payload"]) for sample in SAMPLES}
    assert set(DETAILS) == set(DOCUMENT["documented_events"])


@pytest.mark.parametrize("sample", SAMPLES, ids=[f"{name_of(s['payload'])}-{s['payload'].get('action', '')}" for s in SAMPLES])
def test_official_system_payload_details(sample):
    payload = sample["payload"]
    text, links = render(payload)
    name = name_of(payload)
    for expected in DETAILS[name]:
        assert expected in text
    assert "unknown-project" not in text
    if name not in {"push", "tag_push", "repository_update", "merge_request"}:
        assert "Operator:" not in text
    if name == "gitlab_subscription_member_approvals":
        assert ("promotion approved" if payload["action"] == "approve" else "promotion denied") in text
    if name in {"key_create", "key_destroy"}:
        assert payload["key"] not in text
    if name in {"push", "tag_push", "repository_update", "merge_request"}:
        assert links
    assert payload.get("email", "email-not-present") not in text
    assert payload.get("user_email", "email-not-present") not in text


def test_user_creation_regression_in_chinese():
    text, _ = render({
        "event_name": "user_create", "name": "\u5f20\u4e00\u9f99", "username": "zyl", "user_id": 13,
        "created_at": "2026-10-05T08:07:27Z", "updated_at": "2026-10-05T08:07:27Z",
    })
    assert "User created: \u5f20\u4e00\u9f99 (@zyl)" in text
    assert "User Id: 13" in text
    assert "unknown-project" not in text
    assert "Operator:" not in text
    assert "user_create user_create" not in text


def test_ssh_fingerprint_uses_public_key_blob():
    payload = sample_for("key_create")
    payload["key"] = "ssh-ed25519 aGVsbG8= example-comment"
    text, _ = render(payload)
    assert "Fingerprint: SHA256:LPJNul+wow4m6DsqxbninhsWHlwfp0JecwQzYpOLmCQ" in text


def test_invalid_ssh_key_does_not_crash():
    payload = sample_for("key_create")
    payload["key"] = "ssh-ed25519 !!! invalid"
    text, _ = render(payload)
    assert "Key fingerprint unavailable" in text


def test_system_push_without_commit_details_keeps_ref_actor_and_link():
    payload = sample_for("push")
    payload["commits"] = []
    text, links = render(payload)
    assert "Mike/Diaspora:master" in text
    assert "Operator: John Smith" in text
    assert any(url.endswith("/-/commit/" + payload["after"]) for url in links)
    assert "Add simple search" not in text


def test_project_member_does_not_become_operator():
    payload = sample_for("user_add_to_team")
    payload["operator"] = {"name": "Admin"}
    text, _ = render(payload)
    assert "Member: John Smith (@johnsmith)" in text
    assert "Operator: Admin" in text
    assert "Operator: John Smith" not in text


def test_missing_subject_fields_use_ids():
    text, _ = render({"event_name": "user_create", "user_id": 13})
    assert "User created: user #13" in text
    text, _ = render({"event_name": "user_add_to_team", "user_id": 13, "project_id": 74})
    assert "Project member added: project #74" in text
    assert "Member: user #13" in text


def test_unknown_system_event_has_safe_generic_notification():
    text, _ = render({"event_name": "future_event", "name": "Resource", "action": "changed", "secret": "do-not-forward"})
    assert "System event: future_event" in text
    assert "Name: Resource" in text
    assert "unknown-project" not in text
    assert "do-not-forward" not in text


@pytest.mark.parametrize("event_name", ["user_create", "user_destroy", "user_rename", "user_failed_login", "key_create", "key_destroy", "group_create", "group_destroy", "group_rename", "gitlab_subscription_member_approval", "gitlab_subscription_member_approvals"])
def test_unique_system_events_without_header(event_name):
    payload = sample_for(event_name)
    assert normalize_event("", payload).kind == "system"
    assert render(payload, "") == render(payload)
