from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


EVENT_HEADER_MAP = {
    "Push Hook": "push",
    "Tag Push Hook": "tag_push",
    "Merge Request Hook": "merge_request",
    "Pipeline Hook": "pipeline",
    "Job Hook": "job",
    "Issue Hook": "issue",
    "Confidential Issue Hook": "confidential_issue",
    "Note Hook": "note",
    "Confidential Note Hook": "confidential_note",
    "Wiki Page Hook": "wiki",
    "Deployment Hook": "deployment",
    "Release Hook": "release",
    "Feature Flag Hook": "feature_flag",
    "Emoji Hook": "emoji",
    "Resource Access Token Hook": "resource_access_token",
    "Member Hook": "member",
    "Project Hook": "project",
    "System Hook": "system",
    "Milestone Hook": "milestone",
    "Subgroup Hook": "subgroup",
    "Resource Deploy Token Hook": "resource_deploy_token",
    "Vulnerability Hook": "vulnerability",
    "Duo Flow Callback": "duo_workflow",
}


def _project(payload: dict[str, Any]) -> dict[str, Any]:
    project = payload.get("project") or payload.get("repository")
    if isinstance(project, dict):
        return project
    group = payload.get("group")
    if isinstance(group, dict):
        return {"name": group.get("group_name") or group.get("name") or group.get("full_path"), "web_url": group.get("web_url")}
    if payload.get("group_name") or payload.get("full_path"):
        return {"name": payload.get("full_path") or payload.get("group_name")}
    if str(payload.get("event_name", "")).startswith("project_"):
        return payload
    return {}


def event_kind(header: str, payload: dict[str, Any]) -> str:
    if header in EVENT_HEADER_MAP:
        return EVENT_HEADER_MAP[header]
    value = str(payload.get("object_kind") or payload.get("event_name") or payload.get("event_type") or "").lower()
    if value in {"user_create", "user_destroy", "user_rename", "user_failed_login", "key_create", "key_destroy", "group_create", "group_destroy", "group_rename", "gitlab_subscription_member_approval", "gitlab_subscription_member_approvals"}:
        return "system"
    aliases = {
        "push": "push",
        "tag_push": "tag_push",
        "merge_request": "merge_request",
        "pipeline": "pipeline",
        "build": "job",
        "job": "job",
        "issue": "issue",
        "confidential_issue": "confidential_issue",
        "note": "note",
        "confidential_note": "confidential_note",
        "wiki_page": "wiki",
        "deployment": "deployment",
        "release": "release",
        "repository_update": "system",
        "work_item": "issue",
        "confidential_work_item": "confidential_issue",
        "feature_flag": "feature_flag",
        "emoji": "emoji",
        "milestone": "milestone",
        "access_token": "resource_access_token",
        "deploy_token": "resource_deploy_token",
        "expiring_access_token": "resource_access_token",
        "expiring_deploy_token": "resource_deploy_token",
        "vulnerability": "vulnerability",
        "duo_workflow": "duo_workflow",
    }
    if value.startswith("project_"):
        return "project"
    if value.startswith("subgroup_"):
        return "subgroup"
    if value.startswith("user_") and "group" in value:
        return "member"
    return aliases.get(value, value or "unknown")


@dataclass
class NormalizedEvent:
    kind: str
    action: str
    project_name: str
    namespace: str
    project_url: str
    ref: str = ""
    actor: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""

    @property
    def project_label(self) -> str:
        return f"{self.namespace}/{self.project_name}" if self.namespace else self.project_name


def normalize_event(header: str, payload: dict[str, Any], event_id: str = "") -> NormalizedEvent:
    project = _project(payload)
    attrs = payload.get("object_attributes") or {}
    project_id = payload.get("project_id") or attrs.get("project_id")
    path = str(project.get("path_with_namespace") or "")
    namespace = str(project.get("namespace") or (path.rsplit("/", 1)[0] if "/" in path else ""))
    name = str(project.get("name") or project.get("path") or payload.get("project_name") or (f"project #{project_id}" if project_id else "unknown-project"))
    url = str(project.get("web_url") or project.get("homepage") or "")
    user = payload.get("user") or payload.get("user_name") or payload.get("user_username") or {}
    if isinstance(user, dict):
        actor = str(user.get("name") or user.get("username") or user.get("user_name") or "")
    else:
        actor = str(user)
    action = str(attrs.get("action") or payload.get("action") or attrs.get("status") or payload.get("status") or payload.get("build_status") or payload.get("event_name") or payload.get("event") or payload.get("object_kind") or "updated")
    if event_kind(header, payload) == "feature_flag" and "active" in attrs:
        action = "enabled" if attrs["active"] else "disabled"
    ref = str((attrs or {}).get("ref") or payload.get("ref") or "")
    if ref.startswith("refs/heads/"):
        ref = ref.removeprefix("refs/heads/")
    if ref.startswith("refs/tags/"):
        ref = ref.removeprefix("refs/tags/")
    return NormalizedEvent(event_kind(header, payload), action, name, namespace, url, ref, actor, payload, event_id)

