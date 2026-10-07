import copy
import json
from pathlib import Path

import pytest

from app.events import normalize_event
from app.formatter import format_event


SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "official_events.json").read_text(encoding="utf-8"))["samples"]


def render(header, payload):
    event = normalize_event(header, payload)
    message = format_event(event)
    post = message["content"]["post"]["zh_cn"]
    text = "\n".join("".join(item["text"] for item in line) for line in post["content"])
    links = [item["href"] for line in post["content"] for item in line if item["tag"] == "a"]
    return event, text, links


EXPECTED = {
    "push-events": ("push", ["master", "John Smith", "Jordi Mallach", "Update Catalan translation", "Showing 2 of 4"], "commit/"),
    "tag-events": ("tag_push", ["Created tag: v1.0.0", "Tag message", "John Smith"], None),
    "work-item-events": ("issue", ["#23", "New API", "Administrator", "User1", "API", "Severity: high"], "/issues/23"),
    "comment-on-a-commit": ("note", ["This is a commit comment", "Administrator", "Commit", "Add submodule"], "#note_1243"),
    "comment-on-a-merge-request": ("note", ["This MR needs work.", "Merge Request: !1", "Tempora et eos"], "#note_1244"),
    "comment-on-an-issue": ("note", ["Hello world", "Issue: #17 test"], "#note_1241"),
    "comment-on-a-code-snippet": ("note", ["Is this snippet", "Snippet: #53 test"], "/snippets/53"),
    "complete-payload-example": ("merge_request", ["!16", "Alex Garcia", "Sidney Jones", "enhancement", "Target branch: main", "Add email format validation"], "/merge_requests/16"),
    "wiki-page-events": ("wiki", ["Awesome", "awesome content goes here", "Slug: awesome", "adding an awesome page"], "/wikis/awesome"),
    "pipeline-events": ("pipeline", ["success", "Administrator", "test-image", "script_failure", "Queued Duration: 10"], "/pipelines/31"),
    "job-events": ("job", ["test: created", "User", "Stage: test", "unknown_failure", "Retries Count: 2"], "/jobs/1977"),
    "deployment-events": ("deployment", ["#15", "success", "staging", "Administrator", "Add new file"], "/jobs/796"),
    "deployment-approval-and-rejection-events": ("deployment", ["approved", "Ops Lead", "LGTM", "Required Approvals: 2"], "/jobs/796"),
    "add-member-to-group": ("member", ["user_add_to_group", "Test User", "Group Access: Guest"], None),
    "update-member-access-level-or-expiration-date": ("member", ["user_update_for_group", "Developer", "2020-12-20"], None),
    "remove-member-from-group": ("member", ["user_remove_from_group", "Test User"], None),
    "a-user-requests-access": ("member", ["user_access_request_to_group", "Test User"], None),
    "an-access-request-is-denied": ("member", ["user_access_request_denied_for_group", "Test User"], None),
    "create-a-project-in-a-group": ("project", ["project_create", "group1/project1", "John", "private"], None),
    "delete-a-project-in-a-group": ("project", ["project_destroy", "group1/project1"], None),
    "create-a-subgroup-in-a-group": ("subgroup", ["subgroup_create", "group1/subgroup1", "Parent Group Id: 7"], None),
    "remove-a-subgroup-from-a-group": ("subgroup", ["subgroup_destroy", "group1/subgroup1"], None),
    "feature-flag-events": ("feature_flag", ["enabled", "test-feature-flag", "Active: true", "Administrator"], None),
    "release-events": ("release", ["create", "v1.1 has been released", "Changelog", "tar.gz", "Example User"], "/releases/v1.1"),
    "milestone-events": ("milestone", ["create", "v1.0", "First stable release", "2025-06-30"], "/milestones/10"),
    "emoji-events": ("emoji", ["award", "thumbsup", "Blake Bergstrom", "New issue!", "Testing 123"], "#note_363"),
    "project-and-group-access-token-events": ("resource_access_token", ["expiring_access_token", "acd", "2024-01-26", "Last Used At: 2024-01-20"], None),
    "project-and-group-deploy-token-events": ("resource_deploy_token", ["expiring_deploy_token", "seven-days-6days", "2025-08-03", "Revoked: false"], None),
    "vulnerability-events": ("vulnerability", ["REXML DoS vulnerability", "confirmed", "high", "Gemfile.lock", "CVE-2024-41123"], "/security/vulnerabilities/1"),
    "gitlab-duo-flow-events": ("duo_workflow", ["run-abc123"], "/agent-sessions/1"),
}


@pytest.mark.parametrize("sample", SAMPLES, ids=[f"{s['section']}-{i}" for i, s in enumerate(SAMPLES)])
def test_official_payload_details(sample):
    kind, fields, link = EXPECTED[sample["section"]]
    event, text, links = render(sample["header"], sample["payload"])
    assert event.kind == kind
    for value in fields:
        assert value in text
    if link:
        assert any(link in url for url in links)
    if sample["section"] == "gitlab-duo-flow-events":
        assert sample["payload"]["event"] in text
        if "result" in sample["payload"]:
            assert sample["payload"]["result"]["message"] in text
        else:
            assert "flow_failed" in text
    if sample["payload"].get("group"):
        assert "Twitter" in text
        assert "unknown-project" not in text


def sample_for(section):
    return copy.deepcopy(next(s for s in SAMPLES if s["section"] == section))


@pytest.mark.parametrize("header,kind", [("Confidential Issue Hook", "confidential_issue"), ("Confidential Note Hook", "confidential_note")])
def test_confidential_headers(header, kind):
    sample = sample_for("work-item-events" if kind == "confidential_issue" else "comment-on-an-issue")
    event, text, _ = render(header, sample["payload"])
    assert event.kind == kind
    assert "Confidential" in text


def test_task_uses_work_item_route():
    sample = sample_for("work-item-events")
    payload = sample["payload"]
    payload["object_kind"] = "work_item"
    payload["object_attributes"]["type"] = "Task"
    del payload["object_attributes"]["url"]
    _, text, links = render(sample["header"], payload)
    assert "Type: Task" in text
    assert any("/-/work_items/23" in url for url in links)


def test_merge_request_author_is_not_operator():
    sample = sample_for("complete-payload-example")
    sample["payload"]["user"] = {"id": 99, "name": "Reviewer"}
    _, text, _ = render(sample["header"], sample["payload"])
    assert "Operator: Reviewer" in text
    assert "Pull Request by user #1" in text
    assert "Pull Request by Reviewer" not in text


@pytest.mark.parametrize("section", ["push-events", "tag-events"])
def test_ref_deletion(section):
    sample = sample_for(section)
    sample["payload"]["after"] = "0" * 40
    sample["payload"]["commits"] = []
    _, text, _ = render(sample["header"], sample["payload"])
    assert "Deleted tag:" in text if section == "tag-events" else "Deleted branch:" in text


@pytest.mark.parametrize("section", ["release-events", "milestone-events", "emoji-events"])
def test_top_level_and_nested_actions(section):
    sample = sample_for(section)
    if section == "emoji-events":
        sample["payload"]["object_attributes"]["action"] = "revoke"
        action = "revoke"
    else:
        sample["payload"]["action"] = "delete"
        action = "delete"
    event, text, _ = render(sample["header"], sample["payload"])
    assert event.action == action
    assert action in text


@pytest.mark.parametrize("sample", SAMPLES, ids=[f"fallback-{i}" for i in range(len(SAMPLES))])
def test_missing_header_uses_payload(sample):
    event = normalize_event("", sample["payload"])
    assert event.kind == EXPECTED[sample["section"]][0]


def test_disabled_feature_flag():
    sample = sample_for("feature-flag-events")
    sample["payload"]["object_attributes"]["active"] = False
    _, text, _ = render(sample["header"], sample["payload"])
    assert "disabled" in text
    assert "Active: false" in text


def test_system_update_shows_actor_and_ref_kind():
    _, text, _ = render("System Hook", {
        "event_name": "repository_update", "user_name": "Operator", "project_id": 17,
        "changes": [{"ref": "refs/tags/v2", "before": "abc123", "after": "0" * 40}],
    })
    assert "Operator: Operator" in text
    assert "Deleted tag: v2" in text
    assert "project #17" in text


def test_long_description_keeps_original_link_and_fits_feishu():
    sample = sample_for("release-events")
    sample["payload"]["description"] = "\u957f\u63cf\u8ff0" * 12000
    event = normalize_event(sample["header"], sample["payload"])
    message = format_event(event)
    content = message["content"]["post"]["zh_cn"]["content"]
    assert len(json.dumps(message, ensure_ascii=False).encode("utf-8")) < 20000
    assert content[-1][0]["tag"] == "a"
    assert content[-1][0]["href"] == sample["payload"]["url"]
    assert "View original" == content[-1][0]["text"]
