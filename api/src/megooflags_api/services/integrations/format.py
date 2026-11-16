"""The one-line summary of an event, and the Slack and Microsoft Teams bodies built from it."""

from typing import Any

from megooflags_api.models.enums import IntegrationKind

VERBS: dict[str, str] = {
    "flag.create": "created",
    "flag.update": "updated",
    "flag.archive": "archived",
    "flag.restore": "restored",
    "flag.delete": "deleted",
    "flag.variation.create": "added a variation of",
    "flag.variation.update": "changed a variation of",
    "flag.variation.delete": "removed a variation of",
    "flag.lifecycle.update": "changed the lifecycle of",
    "flag.config.update": "changed the targeting of",
    "flag.kill": "killed",
    "flag.copy": "copied the targeting of",
    "flag.schedule.create": "scheduled a change to",
    "flag.schedule.run": "ran a scheduled change to",
    "flag.schedule.cancel": "cancelled a scheduled change to",
    "flag.schedule.fail": "failed a scheduled change to",
    "flag.rollout.start": "started the rollout of",
    "flag.rollout.step": "advanced the rollout of",
    "flag.rollout.pause": "paused the rollout of",
    "flag.rollout.resume": "resumed the rollout of",
    "flag.rollout.complete": "completed the rollout of",
    "flag.rollout.abort": "aborted the rollout of",
    "flag.rollout.fail": "failed the rollout of",
    "flag.rollout.cancel": "cancelled the rollout of",
    "segment.create": "created",
    "segment.update": "updated",
    "segment.config.update": "changed the rules of",
    "segment.delete": "deleted",
    "change_request.create": "opened a change request for",
    "change_request.approve": "approved a change request for",
    "change_request.reject": "rejected a change request for",
    "change_request.apply": "applied a change request for",
    "change_request.fail": "could not apply a change request for",
    "change_request.cancel": "cancelled a change request for",
}


def _resource(event: dict[str, Any]) -> str:
    """ "flag new-checkout"; a change request names its flag when its snapshot has one."""
    kind, key = event["resource"]["type"], event["resource"]["key"]
    if kind == "change_request":
        for snapshot in (event.get("after"), event.get("before")):
            if isinstance(snapshot, dict) and isinstance(snapshot.get("flag"), str):
                return f"flag {snapshot['flag']}"
    return f"{kind.replace('_', ' ')} {key}"


def summarise(event: dict[str, Any]) -> str:
    """One sentence, e.g. "mona@example.com turned on flag new-checkout in production (shop)"."""
    action, project = event["type"], event["project"]
    if action == "ping":
        return f"This is a test notification from MegooFlags for project {project}."
    actor = event["actor"]["label"]
    if action == "flag.toggle":
        after = event.get("after") or {}
        verb = "turned on" if after.get("enabled") else "turned off"
    else:
        verb = VERBS.get(action, "")
    if verb:
        text = f"{actor} {verb} {_resource(event)}"
    else:
        text = f"{actor} did {action} on {_resource(event)}"
    if event.get("environment"):
        text += f" in {event['environment']}"
    return f"{text} ({project})"


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_body(event: dict[str, Any]) -> dict[str, Any]:
    """A Slack incoming-webhook message, with a link when the event has a URL."""
    text = _slack_escape(summarise(event))
    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    if url := event.get("url"):
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"<{url}|Open in MegooFlags>"}],
            }
        )
    return {"text": text, "blocks": blocks}


def teams_body(event: dict[str, Any]) -> dict[str, Any]:
    """A Teams workflow message carrying an Adaptive Card 1.4."""
    url = event.get("url")
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [{"type": "TextBlock", "text": summarise(event), "wrap": True}],
                    "actions": (
                        [{"type": "Action.OpenUrl", "title": "Open in MegooFlags", "url": url}]
                        if url
                        else []
                    ),
                },
            }
        ],
    }


def render(kind: IntegrationKind, event: dict[str, Any]) -> dict[str, Any]:
    """The request body for an integration of `kind`: webhooks get the event itself."""
    if kind == IntegrationKind.SLACK:
        return slack_body(event)
    if kind == IntegrationKind.TEAMS:
        return teams_body(event)
    return event
