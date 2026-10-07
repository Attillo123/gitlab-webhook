from app.events import normalize_event
from app.formatter import format_event


def test_merge_request_contains_gitea_style_fields():
    event = normalize_event(
        "Merge Request Hook",
        {
            "user": {"name": "Alice"},
            "object_attributes": {"iid": 19, "title": "Improve dashboard", "action": "open", "url": "https://gitlab.example/mr/19", "description": "Details", "source_branch": "feature", "target_branch": "main"},
            "project": {"name": "demo", "namespace": "team", "web_url": "https://gitlab.example/team/demo"},
        },
    )
    message = format_event(event)
    content = message["content"]["post"]["zh_cn"]["content"]
    flattened = "\n".join(item.get("text", "") for line in content for item in line)
    assert "PullRequest-team/demo !19" in flattened
    assert "Target branch: main" in flattened


def test_push_includes_commit_link():
    event = normalize_event(
        "Push Hook",
        {"ref": "refs/heads/main", "commits": [{"id": "abcdef123456", "message": "feat: add webhook", "author_name": "Bob"}], "project": {"name": "demo", "namespace": "team", "web_url": "https://gitlab.example/team/demo"}},
    )
    message = format_event(event)
    links = [item for line in message["content"]["post"]["zh_cn"]["content"] for item in line if item.get("tag") == "a"]
    assert links[0]["href"].endswith("/-/commit/abcdef123456")


def test_repository_update_renders_deleted_branch():
    event = normalize_event(
        "System Hook",
        {
            "event_name": "repository_update",
            "changes": [{"before": "abcdef", "after": "0" * 40, "ref": "refs/heads/feature"}],
            "project": {"name": "demo", "namespace": "team", "web_url": "https://gitlab.example/team/demo"},
        },
    )
    message = format_event(event)
    flattened = "\n".join(item.get("text", "") for line in message["content"]["post"]["zh_cn"]["content"] for item in line)
    assert "Deleted branch: feature" in flattened

