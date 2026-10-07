from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from typing import Any
from urllib.parse import quote, urlsplit

from .events import NormalizedEvent


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _short(value: Any, length: int = 8) -> str:
    return _text(value)[:length]


def _zero_sha(value: Any) -> bool:
    value = _text(value)
    return bool(value) and set(value) == {"0"}


def _person(value: Any) -> str:
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("username"))
    return ""


def _author(event: NormalizedEvent, attrs: dict[str, Any]) -> str:
    author = _person(attrs.get("author")) or _person(event.payload.get("author"))
    author_id = attrs.get("author_id")
    user = event.payload.get("user") or {}
    if not author and author_id is not None:
        if isinstance(user, dict) and user.get("id") == author_id:
            author = _person(user)
        author = author or f"user #{author_id}"
    return author


def _commit_url(event: NormalizedEvent, sha: str) -> str:
    return f"{event.project_url}/-/commit/{sha}" if event.project_url and sha else ""


def _line(*parts: str) -> list[dict[str, str]]:
    return [{"tag": "text", "text": part} for part in parts if part] or [{"tag": "text", "text": "\n"}]


def _link(label: str, url: str) -> dict[str, str]:
    return {"tag": "a", "text": label, "href": url}


def _line_link(prefix: str, label: str, url: str, suffix: str = "") -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if prefix:
        result.append({"tag": "text", "text": prefix})
    if urlsplit(url).scheme in {"http", "https"}:
        result.append(_link(label, url))
    else:
        result.append({"tag": "text", "text": label})
    if suffix:
        result.append({"tag": "text", "text": suffix})
    return result


def _fields(lines: list[list[dict[str, str]]], attrs: dict[str, Any], *keys: str) -> None:
    for key in keys:
        value = attrs.get(key)
        if value is not None and value != "":
            if isinstance(value, bool):
                value = str(value).lower()
            elif isinstance(value, list):
                value = ", ".join(_text(item) for item in value)
            lines.append(_line(f"{key.replace('_', ' ').title()}: {value}"))


def _people(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    for key in ("assignees", "reviewers"):
        people = event.payload.get(key) or []
        names = []
        for person in people:
            name = _person(person)
            if name:
                state = _text(person.get("state"))
                names.append(name + (f" ({state})" if state else "") + (" [review requested again]" if person.get("re_requested") else ""))
        if names:
            lines.append(_line(f"{key.title()}: {', '.join(names)}"))


def _commit(event: NormalizedEvent, lines: list[list[dict[str, str]]], commit: dict[str, Any]) -> None:
    sha = _text(commit.get("sha") or commit.get("id"))
    title = _text(commit.get("title") or commit.get("message"))
    author = _person(commit.get("author")) or _text(commit.get("author_name"))
    if sha or title:
        lines.append(_line_link("Commit: ", _short(sha) or "commit", _text(commit.get("url")) or _commit_url(event, sha), f" {title}" + (f" - {author}" if author else "")))


def _related(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    for key, route, marker in (("merge_request", "merge_requests", "!"), ("issue", "issues", "#"), ("work_item", "work_items", "#"), ("snippet", "snippets", "#")):
        obj = event.payload.get(key)
        if isinstance(obj, dict):
            number = _text(obj.get("iid") or obj.get("id"))
            url = _text(obj.get("url") or obj.get("web_url"))
            if not url and number and event.project_url:
                url = f"{event.project_url}/-/{route}/{number}"
            lines.append(_line_link(f"{key.replace('_', ' ').title()}: ", f"{marker}{number} {_text(obj.get('title'))}", url))
    commit = event.payload.get("commit")
    if isinstance(commit, dict):
        _commit(event, lines, commit)
    source = event.payload.get("source_pipeline") or {}
    if isinstance(source, dict) and source.get("pipeline_id"):
        project = source.get("project") or {}
        base_url = _text(project.get("web_url"))
        url = f"{base_url}/-/pipelines/{source['pipeline_id']}" if base_url else ""
        lines.append(_line_link("Source pipeline: ", str(source["pipeline_id"]), url))


def _changes(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    changes = event.payload.get("changes")
    if not isinstance(changes, dict):
        return
    # Show user-facing changes, avoiding timestamps, emails and arbitrary payload fields.
    for key in ("title", "description", "state", "draft", "source_branch", "target_branch", "labels", "assignee_ids", "reviewers", "merge_status", "detailed_merge_status"):
        change = changes.get(key)
        if isinstance(change, list) and len(change) == 2:
            change = {"previous": change[0], "current": change[1]}
        if isinstance(change, dict):
            def render(value: Any) -> str:
                if isinstance(value, list):
                    return ", ".join((_person(item) or _text(item.get("title"))) if isinstance(item, dict) else _text(item) for item in value)
                return _text(value)
            lines.append(_line(f"Changed {key.replace('_', ' ')}: {render(change.get('previous'))} -> {render(change.get('current'))}"))


def _base(event: NormalizedEvent) -> list[list[dict[str, str]]]:
    branch = f":{event.ref}" if event.ref else ""
    return [[{"tag": "text", "text": f"[{event.project_label}{branch}]"}]]


def _push(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    payload = event.payload
    commits = payload.get("commits") or []
    before = _short(payload.get("before"))
    after = _short(payload.get("after"))
    if event.kind in {"push", "tag_push"}:
        ref_type = "tag" if event.kind == "tag_push" else "branch"
        if _zero_sha(payload.get("after")):
            lines.append(_line(f"Deleted {ref_type}: {event.ref}"))
        elif _zero_sha(payload.get("before")):
            lines.append(_line(f"Created {ref_type}: {event.ref}"))
        if payload.get("message"):
            lines.append(_line(_text(payload["message"])))
    if payload.get("total_commits_count") is not None:
        lines.append(_line(f"{event.action}: {payload.get('total_commits_count')} commit(s)"))
    if before or after:
        lines.append(_line(f"{before} -> {after}"))
    for commit in commits:
        sha = _text(commit.get("id") or commit.get("sha"))
        message_value = _text(commit.get("message"))
        title = _text(commit.get("title") or (message_value.splitlines()[0] if message_value else ""))
        author_data = commit.get("author")
        author = _text(commit.get("author_name") or (author_data.get("name") if isinstance(author_data, dict) else ""))
        lines.append(_line_link("", _short(sha) or "commit", _text(commit.get("url")) or _commit_url(event, sha), f" {title}" + (f" - {author}" if author else "")))
        message = message_value
        if message and "\n" in message:
            lines.append(_line("\n".join(message.splitlines()[1:]).strip()))
    total = payload.get("total_commits_count")
    if isinstance(total, int) and total > len(commits):
        lines.append(_line(f"Showing {len(commits)} of {total} commits supplied by GitLab"))
    if not commits and event.action in {"repository_update", "system"}:
        changes = event.payload.get("changes") or []
        refs = event.payload.get("refs") or []
        for change in changes:
            ref = _text(change.get("ref")) if isinstance(change, dict) else ""
            ref_type = "tag" if ref.startswith("refs/tags/") else "branch"
            before_sha = _text(change.get("before")) if isinstance(change, dict) else ""
            after_sha = _text(change.get("after")) if isinstance(change, dict) else ""
            if ref.startswith("refs/heads/"):
                ref = ref.removeprefix("refs/heads/")
            if ref.startswith("refs/tags/"):
                ref = ref.removeprefix("refs/tags/")
            if after_sha and set(after_sha) == {"0"}:
                lines.append(_line(f"Deleted {ref_type}: {ref}"))
            elif before_sha and set(before_sha) == {"0"}:
                lines.append(_line(f"Created {ref_type}: {ref}"))
            else:
                lines.append(_line(f"Updated ref: {ref} ({_short(before_sha)} -> {_short(after_sha)})"))
        if refs and not changes:
            lines.append(_line("Refs: " + ", ".join(_text(ref).removeprefix("refs/heads/") for ref in refs)))
        if not changes and not refs:
            lines.append(_line("Repository updated"))


def _merge_request(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    attrs = event.payload.get("object_attributes") or {}
    iid = _text(attrs.get("iid"))
    title = _text(attrs.get("title"))
    url = _text(attrs.get("url")) or (f"{event.project_url}/-/merge_requests/{iid}" if iid else event.project_url)
    lines.append(_line_link(f"[PullRequest-{event.project_label} !{iid}]: ", title or "Merge request", url, f" ({event.action})"))
    author = _author(event, attrs)
    if author:
        lines.append(_line(f"Pull Request by {author}"))
    description = _text(attrs.get("description"))
    if description:
        lines.extend([_line(""), _line(description)])
    source = _text(attrs.get("source_branch"))
    target = _text(attrs.get("target_branch"))
    if source or target:
        lines.append(_line(f"Source branch: {source}    Target branch: {target}"))
    labels = attrs.get("labels") or event.payload.get("labels") or []
    if labels:
        lines.append(_line("Labels: " + ", ".join(_text(x.get("title") if isinstance(x, dict) else x) for x in labels)))
    _fields(lines, attrs, "state", "draft", "detailed_merge_status", "merge_error", "system_action", "merge_commit_sha", "squash_commit_sha")
    _people(event, lines)
    if isinstance(attrs.get("last_commit"), dict):
        _commit(event, lines, attrs["last_commit"])
    _changes(event, lines)


def _pipeline(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    attrs = event.payload.get("object_attributes") or event.payload.get("pipeline") or {}
    status = _text(attrs.get("status") or event.payload.get("status"))
    ref = _text(attrs.get("ref") or event.ref)
    sha = _short(attrs.get("sha") or attrs.get("before_sha"))
    url = _text(attrs.get("url")) or (f"{event.project_url}/-/pipelines/{attrs.get('id')}" if attrs.get("id") else event.project_url)
    lines.append(_line_link(f"[Pipeline-{event.project_label}] ", status or event.action, url))
    lines.append(_line(f"Status: {status or 'unknown'}    Ref: {ref or '-'}    Commit: {sha or '-'}"))
    if attrs.get("duration") is not None:
        lines.append(_line(f"Duration: {attrs.get('duration')}s"))
    _fields(lines, attrs, "name", "source", "detailed_status", "queued_duration", "stages", "created_at", "finished_at")
    for build in event.payload.get("builds") or []:
        if isinstance(build, dict):
            job_url = f"{event.project_url}/-/jobs/{build['id']}" if event.project_url and build.get("id") else ""
            lines.append(_line_link("Job: ", f"{_text(build.get('stage'))} / {_text(build.get('name'))}: {_text(build.get('status'))}", job_url))
            _fields(lines, build, "failure_reason", "duration", "allow_failure")
    _related(event, lines)


def _job(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    payload = event.payload
    attrs = event.payload.get("build") or event.payload.get("object_attributes") or {}
    status = _text(payload.get("build_status") or attrs.get("status"))
    name = _text(payload.get("build_name") or attrs.get("name") or "job")
    job_id = payload.get("build_id") or attrs.get("id")
    url = _text(attrs.get("web_url") or attrs.get("url"))
    if not url and job_id and event.project_url:
        url = f"{event.project_url}/-/jobs/{job_id}"
    lines.append(_line_link(f"[Job-{event.project_label}] ", f"{name}: {status or event.action}", url))
    lines.append(_line(f"Ref: {_text(attrs.get('ref') or event.ref) or '-'}    Commit: {_short(payload.get('sha') or attrs.get('sha')) or '-'}"))
    for label, value in (
        ("Stage", payload.get("build_stage") or attrs.get("stage")),
        ("Duration", payload.get("build_duration") if payload.get("build_duration") is not None else attrs.get("duration")),
        ("Failure reason", payload.get("build_failure_reason") or attrs.get("failure_reason")),
    ):
        if value is not None and value != "":
            lines.append(_line(f"{label}: {value}"))
    _fields(lines, payload, "retries_count", "build_allow_failure", "build_queued_duration", "build_created_at", "build_started_at", "build_finished_at")
    if payload.get("pipeline_id") and event.project_url:
        lines.append(_line_link("Pipeline: ", str(payload["pipeline_id"]), f"{event.project_url}/-/pipelines/{payload['pipeline_id']}"))
    runner = payload.get("runner") or {}
    if isinstance(runner, dict):
        _fields(lines, runner, "description", "runner_type")
    environment = payload.get("environment") or {}
    if isinstance(environment, dict) and environment.get("name"):
        lines.append(_line(f"Environment: {_text(environment.get('name'))}"))
    _related(event, lines)


def _deployment(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    attrs = {**event.payload, **(event.payload.get("object_attributes") or {})}
    lines.append(_line_link(f"[Deployment-{event.project_label} #{attrs.get('deployment_id', '')}]: ", event.action, _text(attrs.get("deployable_url"))))
    _fields(lines, attrs, "environment", "environment_tier", "status", "status_changed_at")
    if attrs.get("environment_external_url"):
        lines.append(_line_link("Environment URL: ", _text(attrs["environment_external_url"]), _text(attrs["environment_external_url"])))
    if attrs.get("short_sha") or attrs.get("commit_title"):
        lines.append(_line_link("Commit: ", _text(attrs.get("short_sha")), _text(attrs.get("commit_url")), " " + _text(attrs.get("commit_title"))))
    approver = _person(event.payload.get("approver"))
    if approver:
        lines.append(_line(f"Approver: {approver}"))
    approval = event.payload.get("approval") or {}
    _fields(lines, approval, "status", "comment", "created_at")
    rule = approval.get("approval_rule") or {}
    _fields(lines, rule, "access_level_description", "required_approvals")


def _release(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    attrs = {**event.payload, **(event.payload.get("object_attributes") or {})}
    url = _text(attrs.get("url"))
    tag = _text(attrs.get("tag"))
    if not url and tag and event.project_url:
        url = f"{event.project_url}/-/releases/{quote(tag, safe='')}"
    lines.append(_line_link(f"[Release-{event.project_label}]: {event.action} ", _text(attrs.get("name") or tag), url))
    _fields(lines, attrs, "tag", "released_at", "created_at")
    if attrs.get("description"):
        lines.append(_line(_text(attrs["description"])))
    assets = attrs.get("assets") or {}
    for asset in (assets.get("links") or []) + (assets.get("sources") or []):
        if isinstance(asset, dict):
            lines.append(_line_link("Asset: ", _text(asset.get("name") or asset.get("format")), _text(asset.get("url"))))
    _related(event, lines)


def _administrative(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    attrs = event.payload
    lines.append(_line(f"[{event.kind.title()}-{event.project_label}]: {event.action}"))
    if event.kind == "member":
        lines.append(_line(f"Member: {_text(attrs.get('user_name') or attrs.get('user_username'))}"))
        _fields(lines, attrs, "user_id", "group_access", "expires_at", "group_id")
    elif event.kind == "project":
        _fields(lines, attrs, "project_id", "path_with_namespace", "project_visibility", "created_at", "updated_at")
        owners = [_person(owner) for owner in attrs.get("owners") or []]
        if owners:
            lines.append(_line("Owners: " + ", ".join(owners)))
    else:
        _fields(lines, attrs, "group_id", "full_path", "parent_group_id", "parent_full_path", "created_at")


def _duo(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    attrs = event.payload.get("workflow") or {}
    lines.append(_line_link(f"[Duo Flow-{event.project_label}]: ", event.action, _text(attrs.get("web_url"))))
    _fields(lines, attrs, "id", "status")
    _fields(lines, event.payload, "client_reference")
    result = event.payload.get("result") or {}
    error = event.payload.get("error") or {}
    _fields(lines, result, "message")
    _fields(lines, error, "reason")


def _object_event(event: NormalizedEvent, lines: list[list[dict[str, str]]]) -> None:
    attrs = event.payload.get("object_attributes") or {}
    kind_name = {"issue": "Work Item", "confidential_issue": "Confidential Work Item", "note": "Comment", "confidential_note": "Confidential Comment", "wiki": "Wiki", "feature_flag": "Feature Flag", "emoji": "Emoji", "system": "System"}.get(event.kind, event.kind.replace("_", " ").title())
    iid = _text(attrs.get("iid") or attrs.get("id"))
    title = _text(attrs.get("title") or attrs.get("name") or attrs.get("note") or attrs.get("action") or event.action)
    url = _text(attrs.get("url") or attrs.get("web_url") or attrs.get("awarded_on_url"))
    if not url and iid and event.project_url:
        route = {"issue": "issues", "confidential_issue": "issues", "milestone": "milestones", "vulnerability": "security/vulnerabilities"}.get(event.kind)
        if event.payload.get("object_kind") == "work_item":
            route = "work_items"
        if route:
            url = f"{event.project_url}/-/{route}/{iid}"
    identifier = f" #{iid}" if iid else ""
    lines.append(_line_link(f"[{kind_name}-{event.project_label}{identifier}]: {event.action} ", title, url))
    description = _text(attrs.get("description") or attrs.get("note") or attrs.get("content") or attrs.get("message"))
    if description and description != title:
        lines.extend([_line(""), _line(description)])
    _fields(lines, attrs, "state", "status", "type", "confidential", "internal", "severity", "health_status", "due_date", "start_date", "weight", "active", "slug", "format", "message", "version_id", "awardable_type", "awardable_id", "expires_at", "last_used_at", "user_id", "revoked", "deploy_token_type")
    if attrs.get("diff_url"):
        lines.append(_line_link("Diff: ", "View changes", _text(attrs["diff_url"])))
    labels = attrs.get("labels") or event.payload.get("labels") or []
    if labels:
        lines.append(_line("Labels: " + ", ".join(_text(label.get("title")) if isinstance(label, dict) else _text(label) for label in labels)))
    _people(event, lines)
    if event.kind in {"note", "confidential_note", "emoji"}:
        _fields(lines, attrs, "noteable_type", "commit_id")
        _related(event, lines)
        note = event.payload.get("note") or {}
        if isinstance(note, dict) and note.get("note"):
            lines.append(_line("Comment: " + _text(note["note"])))
    if event.kind == "vulnerability":
        _fields(lines, attrs, "report_type", "scanner_external_id", "confidence")
        location = attrs.get("location") or {}
        _fields(lines, location, "file")
        dependency = location.get("dependency") or {}
        package = dependency.get("package") or {}
        _fields(lines, package, "name")
        _fields(lines, dependency, "version")
        for identifier in attrs.get("identifiers") or []:
            lines.append(_line_link("Identifier: ", _text(identifier.get("name")), _text(identifier.get("url"))))
        for issue in attrs.get("issues") or []:
            lines.append(_line_link("Issue: ", _text(issue.get("title")), _text(issue.get("url"))))
    if event.kind in {"issue", "confidential_issue"}:
        author = _author(event, attrs)
        if author:
            lines.append(_line(f"Author: {author}"))
    if event.kind in {"resource_access_token", "resource_deploy_token"}:
        group = event.payload.get("group") or {}
        _fields(lines, group, "full_path", "group_id")
        _fields(lines, attrs, "created_at")
    _changes(event, lines)


SYSTEM_ACTIONS = {
    "project_create": "Project created", "project_destroy": "Project deleted",
    "project_rename": "Project renamed", "project_transfer": "Project transferred",
    "project_update": "Project updated",
    "group_create": "Group created", "group_destroy": "Group deleted", "group_rename": "Group renamed",
    "user_create": "User created", "user_destroy": "User deleted",
    "user_rename": "User renamed", "user_failed_login": "User login failed",
    "key_create": "SSH key added", "key_destroy": "SSH key deleted",
    "user_add_to_team": "Project member added", "user_remove_from_team": "Project member removed",
    "user_update_for_team": "Project member role updated",
    "user_add_to_group": "Group member added", "user_remove_from_group": "Group member removed",
    "user_update_for_group": "Group member role updated",
    "user_access_request_to_project": "Project access requested",
    "user_access_request_revoked_for_project": "Project access request canceled",
    "user_access_request_to_group": "Group access requested",
    "user_access_request_revoked_for_group": "Group access request canceled",
    "repository_update": "Repository updated", "push": "Push", "tag_push": "Tag push",
    "merge_request": "Merge request",
}


def _system_user(payload: dict[str, Any]) -> str:
    name = _text(payload.get("user_name") or payload.get("name"))
    username = _text(payload.get("user_username") or payload.get("username"))
    return (name + (f" (@{username})" if username else "")).strip() or (f"user #{payload['user_id']}" if payload.get("user_id") is not None else "User")


def _system_event(event: NormalizedEvent) -> tuple[str, list[list[dict[str, str]]]]:
    payload = event.payload
    name = _text(payload.get("event_name") or payload.get("object_kind"))
    action = SYSTEM_ACTIONS.get(name, "System event")
    title = f"GitLab System · {action}"
    lines: list[list[dict[str, str]]] = []
    if name in {"push", "tag_push", "repository_update", "merge_request"}:
        title = f"GitLab System · {event.project_label}"
        lines = _base(event)
        if name == "merge_request":
            _merge_request(event, lines)
            attrs = payload.get("object_attributes") or {}
            _fields(lines, attrs, "merge_status", "work_in_progress")
            assignee = _person(attrs.get("assignee"))
            if assignee and not payload.get("assignees"):
                lines.append(_line(f"Assignee: {assignee}"))
        else:
            effective = replace(event, kind=name) if name in {"push", "tag_push"} else event
            _push(effective, lines)
            if name in {"push", "tag_push"} and not payload.get("commits") and not _zero_sha(payload.get("after")):
                sha = _text(payload.get("after") or payload.get("checkout_sha"))
                if sha:
                    _commit(event, lines, {"id": sha})
            if name == "repository_update":
                for change in payload.get("changes") or []:
                    if isinstance(change, dict):
                        sha = _text(change.get("before") if _zero_sha(change.get("after")) else change.get("after"))
                        if sha and not _zero_sha(sha):
                            _commit(event, lines, {"id": sha})
        if event.actor:
            lines.append(_line(f"Operator: {event.actor}"))
    elif name.startswith("project_"):
        scope = _text(payload.get("path_with_namespace") or payload.get("name")) or event.project_label
        lines.append(_line(f"{action}: {scope}"))
        _fields(lines, payload, "name", "project_id", "project_namespace_id", "project_visibility", "path")
        old_path = _text(payload.get("old_path_with_namespace"))
        if old_path:
            lines.append(_line(f"Path: {old_path} -> {_text(payload.get('path_with_namespace'))}"))
        owners = [_person(owner) for owner in payload.get("owners") or []]
        owner_name = _text(payload.get("owner_name"))
        if owner_name and owner_name not in owners:
            owners.append(owner_name)
        if owners:
            lines.append(_line("Owners: " + ", ".join(filter(None, owners))))
    elif name.startswith("group_"):
        scope = _text(payload.get("full_path") or payload.get("path") or payload.get("name"))
        lines.append(_line(f"{action}: {scope or 'group #' + _text(payload.get('group_id'))}"))
        _fields(lines, payload, "name", "group_id", "path", "full_path", "old_path", "old_full_path")
    elif name in {"user_create", "user_destroy", "user_rename", "user_failed_login"}:
        lines.append(_line(f"{action}: {_system_user(payload)}"))
        _fields(lines, payload, "user_id", "state")
        if name == "user_rename":
            lines.append(_line(f"Username: {_text(payload.get('old_username'))} -> {_text(payload.get('username'))}"))
    elif name in {"key_create", "key_destroy"}:
        lines.append(_line(f"{action}: @{_text(payload.get('username')) or 'unknown-user'}"))
        _fields(lines, payload, "id")
        key_parts = _text(payload.get("key")).split(None, 2)
        if len(key_parts) >= 2:
            lines.append(_line(f"Key type: {key_parts[0]}"))
            try:
                key_bytes = base64.b64decode(key_parts[1], validate=True)
            except ValueError:
                lines.append(_line("Key fingerprint unavailable"))
            else:
                digest = base64.b64encode(hashlib.sha256(key_bytes).digest()).decode().rstrip("=")
                lines.append(_line(f"Fingerprint: SHA256:{digest}"))
            if len(key_parts) == 3:
                lines.append(_line(f"Key comment: {key_parts[2]}"))
    elif name.startswith("user_") and ("group" in name or "team" in name or "project" in name):
        is_group = "group" in name
        scope_type = "Group" if is_group else "Project"
        scope = (_text(payload.get("group_name") or payload.get("group_path")) if is_group else _text(payload.get("project_path_with_namespace") or payload.get("project_name")))
        scope_id = payload.get("group_id" if is_group else "project_id")
        scope = scope or f"{scope_type.lower()} #{scope_id}"
        lines.append(_line(f"{action}: {scope}"))
        lines.append(_line(f"Member: {_system_user(payload)}"))
        _fields(lines, payload, "user_id", "group_id", "group_path", "project_id", "project_path_with_namespace", "group_access", "access_level", "project_visibility", "expires_at")
    elif name in {"gitlab_subscription_member_approval", "gitlab_subscription_member_approvals"}:
        approval_action = _text(payload.get("action"))
        action = {"enqueue": "Member role promotion requested", "approve": "Member role promotion approved", "deny": "Member role promotion denied"}.get(approval_action, "Member role promotion")
        title = f"GitLab System · {action}"
        lines.append(_line(action))
        _fields(lines, payload, "user_id", "requested_by_user_id", "reviewed_by_user_id", "promotion_namespace_id")
        attrs = dict(payload.get("object_attributes") or {})
        roles = {0: "No access", 5: "Minimal access", 10: "Guest", 15: "Planner", 20: "Reporter", 30: "Developer", 40: "Maintainer", 50: "Owner"}
        for key in ("old_access_level", "new_access_level"):
            level = attrs.get(key)
            if level in roles:
                attrs[key] = f"{level} ({roles[level]})"
        _fields(lines, attrs, "existing_member_id", "old_access_level", "new_access_level", "status", "promotion_request_ids_that_failed_to_apply")
    else:
        lines.append(_line(f"System event: {name or event.action}"))
        _fields(lines, payload, "name", "username", "user_id", "project_id", "group_id", "path", "action", "state")
        _fields(lines, payload.get("object_attributes") or {}, "name", "title", "action", "status")
    # Only repository/MR payloads identify an operator. Other top-level users are subjects.
    if name not in {"push", "tag_push", "repository_update", "merge_request"}:
        operator = _person(payload.get("operator"))
        if operator:
            lines.append(_line(f"Operator: {operator}"))
    _fields(lines, payload, "created_at", "updated_at")
    return title, lines


def format_event(event: NormalizedEvent, max_length: int = 12000) -> dict[str, Any]:
    title = f"GitLab {event.kind.replace('_', ' ').title()} · {event.project_label}"
    lines = _base(event)
    if event.kind in {"push", "tag_push"}:
        _push(event, lines)
    elif event.kind == "system":
        title, lines = _system_event(event)
    elif event.kind == "merge_request":
        _merge_request(event, lines)
    elif event.kind == "pipeline":
        _pipeline(event, lines)
    elif event.kind == "job":
        _job(event, lines)
    elif event.kind == "deployment":
        _deployment(event, lines)
    elif event.kind == "release":
        _release(event, lines)
    elif event.kind in {"member", "project", "subgroup"}:
        _administrative(event, lines)
    elif event.kind == "duo_workflow":
        _duo(event, lines)
    else:
        _object_event(event, lines)
    if event.actor and event.kind not in {"member", "system"}:
        lines.append(_line(f"Operator: {event.actor}"))
    title = title[:200]
    original = next((item["href"] for line in lines for item in line if item["tag"] == "a"), event.project_url)
    footer = _line_link("", "View original", original) if original else _line("Message truncated")

    def clip(budget: int) -> tuple[list[list[dict[str, str]]], bool]:
        clipped: list[list[dict[str, str]]] = []
        used = 0
        for line in lines:
            line_size = sum(len(item["text"]) for item in line)
            if used + line_size > budget:
                remaining = budget - used
                if remaining > 1:
                    clipped.append(_line("".join(item["text"] for item in line)[:remaining - 1] + "…"))
                return clipped, True
            clipped.append(line)
            used += line_size
        return clipped, False

    # Bound both visible text and UTF-8 JSON size; leave room for Feishu signing fields.
    budget = max(1, max_length)
    while True:
        content, truncated = clip(budget)
        if truncated:
            footer_size = sum(len(item["text"]) for item in footer)
            content, _ = clip(max(0, budget - footer_size))
            content.append(footer)
        message = {"msg_type": "post", "content": {"post": {"zh_cn": {"title": title, "content": content}}}}
        if len(json.dumps(message, ensure_ascii=False).encode("utf-8")) <= 19000:
            return message
        if budget <= 1:
            # Oversized URLs themselves cannot be carried in a valid Feishu message.
            return {"msg_type": "post", "content": {"post": {"zh_cn": {"title": title, "content": [_line("Message truncated")]}}}}
        budget = max(1, budget // 2)

