"""Which audit actions an integration hears about, and the event payload sent for each."""

import uuid
from datetime import UTC, datetime
from typing import Any

from megooflags_api.models import AuditEntry
from megooflags_api.models.enums import EventGroup

# Exact actions, or prefixes ending in "." that match every action under them.
GROUP_ACTIONS: dict[EventGroup, tuple[str, ...]] = {
    EventGroup.FLAGS: (
        "flag.create",
        "flag.update",
        "flag.archive",
        "flag.restore",
        "flag.delete",
        "flag.variation.",
        "flag.lifecycle.update",
    ),
    EventGroup.TARGETING: (
        "flag.config.update",
        "flag.toggle",
        "flag.kill",
        "flag.copy",
        "flag.schedule.",
        "flag.rollout.",
    ),
    EventGroup.SEGMENTS: (
        "segment.create",
        "segment.update",
        "segment.config.update",
        "segment.delete",
    ),
    # Not change_request.comment.
    EventGroup.CHANGE_REQUESTS: (
        "change_request.create",
        "change_request.approve",
        "change_request.reject",
        "change_request.apply",
        "change_request.fail",
        "change_request.cancel",
    ),
}


def group_of(action: str) -> EventGroup | None:
    """The group `action` belongs to, or None when integrations don't hear about it."""
    for group, actions in GROUP_ACTIONS.items():
        for pattern in actions:
            if action == pattern or (pattern.endswith(".") and action.startswith(pattern)):
                return group
    return None


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _url(
    entry: AuditEntry, project_key: str, environment_key: str | None, public_url: str | None
) -> str | None:
    if public_url is None:
        return None
    base = f"{public_url.rstrip('/')}/projects/{project_key}"
    if entry.resource_type == "flag":
        if entry.action == "flag.delete":
            return f"{base}/flags"
        url = f"{base}/flags/{entry.resource_key}"
        return url if environment_key is None else f"{url}?env={environment_key}"
    if entry.resource_type == "segment":
        return f"{base}/segments"
    if entry.resource_type == "change_request":
        return f"{base}/change-requests"
    return None


def build_event(
    entry: AuditEntry, project_key: str, environment_key: str | None, public_url: str | None
) -> dict[str, Any]:
    """The event for `entry`; its snapshots are already redacted. `url` is None without
    `public_url` (MEGOOFLAGS_PUBLIC_URL)."""
    return {
        "id": f"evt_{entry.id}",
        "type": entry.action,
        "occurred_at": _iso(entry.occurred_at),
        "project": project_key,
        "environment": environment_key,
        "resource": {"type": entry.resource_type, "key": entry.resource_key},
        "actor": {"kind": entry.actor_kind, "label": entry.actor_label},
        "before": entry.before,
        "after": entry.after,
        "url": _url(entry, project_key, environment_key, public_url),
    }


def ping_event(project_key: str, integration_name: str, public_url: str | None) -> dict[str, Any]:
    """A test event for an integration; it links to the project's Integrations page."""
    return {
        "id": f"evt_ping_{uuid.uuid4().hex}",
        "type": "ping",
        "occurred_at": _iso(datetime.now(UTC)),
        "project": project_key,
        "environment": None,
        "resource": {"type": "integration", "key": integration_name},
        "actor": {"kind": "system", "label": "MegooFlags"},
        "before": None,
        "after": None,
        "url": (
            None
            if public_url is None
            else f"{public_url.rstrip('/')}/projects/{project_key}/integrations"
        ),
    }
