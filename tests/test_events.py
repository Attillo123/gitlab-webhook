from app.events import normalize_event


def test_push_event_normalization():
    event = normalize_event(
        "Push Hook",
        {"object_kind": "push", "ref": "refs/heads/main", "project": {"name": "demo", "namespace": "team", "web_url": "https://gitlab.example/team/demo"}},
    )
    assert event.kind == "push"
    assert event.ref == "main"
    assert event.project_label == "team/demo"


def test_system_repository_update_is_supported():
    event = normalize_event("System Hook", {"event_name": "repository_update", "project": {"name": "demo", "path_with_namespace": "team/demo"}})
    assert event.kind == "system"

