from typing import Any

import pytest

from megooflags_api.models.enums import IntegrationKind
from megooflags_api.services.integrations.format import (
    render,
    slack_body,
    summarise,
    teams_body,
)


def _event(
    action: str,
    resource_type: str = "flag",
    key: str = "new-checkout",
    *,
    environment: str | None = "production",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    actor: str = "mona@example.com",
    url: str | None = None,
) -> dict[str, Any]:
    return {
        "id": "evt_1",
        "type": action,
        "occurred_at": "2026-11-15T10:00:00Z",
        "project": "shop",
        "environment": environment,
        "resource": {"type": resource_type, "key": key},
        "actor": {"kind": "user", "label": actor},
        "before": before,
        "after": after,
        "url": url,
    }


CR = {"flag": "new-checkout"}
IN_PROD = " flag new-checkout in production (shop)"

CASES: list[tuple[dict[str, Any], str]] = [
    (_event("flag.create", environment=None), "mona@example.com created flag new-checkout (shop)"),
    (_event("flag.update", environment=None), "mona@example.com updated flag new-checkout (shop)"),
    (
        _event("flag.archive", environment=None),
        "mona@example.com archived flag new-checkout (shop)",
    ),
    (
        _event("flag.restore", environment=None),
        "mona@example.com restored flag new-checkout (shop)",
    ),
    (_event("flag.delete", environment=None), "mona@example.com deleted flag new-checkout (shop)"),
    (
        _event("flag.variation.create", environment=None),
        "mona@example.com added a variation of flag new-checkout (shop)",
    ),
    (
        _event("flag.variation.update", environment=None),
        "mona@example.com changed a variation of flag new-checkout (shop)",
    ),
    (
        _event("flag.variation.delete", environment=None),
        "mona@example.com removed a variation of flag new-checkout (shop)",
    ),
    (
        _event("flag.lifecycle.update", environment=None),
        "mona@example.com changed the lifecycle of flag new-checkout (shop)",
    ),
    (_event("flag.config.update"), "mona@example.com changed the targeting of" + IN_PROD),
    (_event("flag.toggle", after={"enabled": True}), "mona@example.com turned on" + IN_PROD),
    (_event("flag.toggle", after={"enabled": False}), "mona@example.com turned off" + IN_PROD),
    (_event("flag.kill"), "mona@example.com killed" + IN_PROD),
    (_event("flag.copy"), "mona@example.com copied the targeting of" + IN_PROD),
    (_event("flag.schedule.create"), "mona@example.com scheduled a change to" + IN_PROD),
    (_event("flag.schedule.run"), "mona@example.com ran a scheduled change to" + IN_PROD),
    (_event("flag.schedule.cancel"), "mona@example.com cancelled a scheduled change to" + IN_PROD),
    (_event("flag.schedule.fail"), "mona@example.com failed a scheduled change to" + IN_PROD),
    (_event("flag.rollout.start"), "mona@example.com started the rollout of" + IN_PROD),
    (_event("flag.rollout.step"), "mona@example.com advanced the rollout of" + IN_PROD),
    (_event("flag.rollout.pause"), "mona@example.com paused the rollout of" + IN_PROD),
    (_event("flag.rollout.resume"), "mona@example.com resumed the rollout of" + IN_PROD),
    (_event("flag.rollout.complete"), "mona@example.com completed the rollout of" + IN_PROD),
    (_event("flag.rollout.abort"), "mona@example.com aborted the rollout of" + IN_PROD),
    (_event("flag.rollout.fail"), "mona@example.com failed the rollout of" + IN_PROD),
    (_event("flag.rollout.cancel"), "mona@example.com cancelled the rollout of" + IN_PROD),
    (
        _event("segment.create", "segment", "beta", environment=None),
        "mona@example.com created segment beta (shop)",
    ),
    (
        _event("segment.update", "segment", "beta", environment=None),
        "mona@example.com updated segment beta (shop)",
    ),
    (
        _event("segment.config.update", "segment", "beta"),
        "mona@example.com changed the rules of segment beta in production (shop)",
    ),
    (
        _event("segment.delete", "segment", "beta", environment=None),
        "mona@example.com deleted segment beta (shop)",
    ),
    (
        _event("change_request.create", "change_request", "c1", after=CR),
        "mona@example.com opened a change request for" + IN_PROD,
    ),
    (
        _event("change_request.approve", "change_request", "c1", after={"decision": "approve"}),
        "mona@example.com approved a change request for change request c1 in production (shop)",
    ),
    (
        _event("change_request.reject", "change_request", "c1", before=CR),
        "mona@example.com rejected a change request for" + IN_PROD,
    ),
    (
        _event("change_request.apply", "change_request", "c1", before=CR, after=CR),
        "mona@example.com applied a change request for" + IN_PROD,
    ),
    (
        _event("change_request.fail", "change_request", "c1", before=CR, after=CR),
        "mona@example.com could not apply a change request for" + IN_PROD,
    ),
    (
        _event("change_request.cancel", "change_request", "c1", before=CR, after=CR),
        "mona@example.com cancelled a change request for" + IN_PROD,
    ),
    (
        _event("ping", "integration", "Ops hook", environment=None, actor="MegooFlags"),
        "This is a test notification from MegooFlags for project shop.",
    ),
    (
        _event("flag.something.new"),
        "mona@example.com did flag.something.new on flag new-checkout in production (shop)",
    ),
]


@pytest.mark.parametrize(("event", "expected"), CASES, ids=[e["type"] for e, _ in CASES])
def test_summarise(event: dict[str, Any], expected: str) -> None:
    assert summarise(event) == expected


def test_slack_body_without_url() -> None:
    text = "mona@example.com killed" + IN_PROD
    assert slack_body(_event("flag.kill")) == {
        "text": text,
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    }


def test_slack_body_with_url() -> None:
    url = "https://flags.example.com/projects/shop/flags/new-checkout?env=production"
    text = "mona@example.com killed" + IN_PROD
    assert slack_body(_event("flag.kill", url=url)) == {
        "text": text,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"<{url}|Open in MegooFlags>"}],
            },
        ],
    }


def test_slack_escapes_user_text() -> None:
    body = slack_body(_event("flag.kill", actor="<!channel> & <@U1>"))
    expected = "&lt;!channel&gt; &amp; &lt;@U1&gt; killed" + IN_PROD
    assert body["text"] == expected
    assert body["blocks"][0]["text"]["text"] == expected


def test_teams_body_without_url() -> None:
    text = "mona@example.com killed" + IN_PROD
    assert teams_body(_event("flag.kill")) == {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [{"type": "TextBlock", "text": text, "wrap": True}],
                    "actions": [],
                },
            }
        ],
    }


def test_teams_body_with_url_keeps_text_unescaped() -> None:
    url = "https://flags.example.com/projects/shop/flags/new-checkout?env=production"
    body = teams_body(_event("flag.kill", actor="a & b", url=url))
    content = body["attachments"][0]["content"]
    assert content["body"][0]["text"] == "a & b killed" + IN_PROD
    assert content["actions"] == [
        {"type": "Action.OpenUrl", "title": "Open in MegooFlags", "url": url}
    ]


def test_render_by_kind() -> None:
    event = _event("flag.kill")
    assert render(IntegrationKind.WEBHOOK, event) == event
    assert render(IntegrationKind.SLACK, event) == slack_body(event)
    assert render(IntegrationKind.TEAMS, event) == teams_body(event)
