import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from helpers import create_flag, create_project
from jsonschema import Draft202012Validator
from sqlalchemy import select

from megooflags_api.evaluation import parse_ruleset
from megooflags_api.models import Environment, Project
from megooflags_api.services.ruleset import build_ruleset

SPEC = Path(__file__).resolve().parents[2] / "spec"
FLAGS = "/api/projects/demo/flags"


def clause(attribute: str, operator: str, values: list[Any]) -> dict[str, Any]:
    return {"attribute": attribute, "operator": operator, "values": values, "negate": False}


SEGMENT_RULE = {"clauses": [clause("plan", "in", ["pro"])]}
APP_ROLLOUT = {
    "bucket_by": "org",
    "weights": [{"variation": "red", "weight": 50000}, {"variation": "blue", "weight": 50000}],
}
BLUE_ROLLOUT = {"bucket_by": "key", "weights": [{"variation": "blue", "weight": 100000}]}

EXPECTED = {
    "schema_version": 1,
    "project": "demo",
    "environment": "production",
    "flags": {
        "color": {
            "key": "color",
            "type": "string",
            "enabled": True,
            "version": 2,
            "variations": [
                {"key": "red", "value": "red"},
                {"key": "blue", "value": "blue"},
                {"key": "green", "value": "green"},
            ],
            "off_variation": "red",
            "prerequisites": [{"flag": "gate", "variation": "true"}],
            "targets": [{"variation": "blue", "values": ["u0", "u3"]}],
            "rules": [
                {
                    "description": "beta users",
                    "clauses": [clause("key", "in_segment", ["beta"])],
                    "serve": {"variation": "green"},
                },
                {
                    "description": "new app",
                    "clauses": [clause("app", "semver_gt", ["1.0.0"])],
                    "serve": {"rollout": APP_ROLLOUT},
                },
            ],
            "fallthrough": {"rollout": BLUE_ROLLOUT},
        },
        "gate": {
            "key": "gate",
            "type": "boolean",
            "enabled": True,
            "version": 2,
            "variations": [{"key": "true", "value": True}, {"key": "false", "value": False}],
            "off_variation": "false",
            "prerequisites": [],
            "targets": [],
            "rules": [],
            "fallthrough": {"variation": "true"},
        },
    },
    "segments": {"beta": {"included": ["u1"], "excluded": ["u2"], "rules": [SEGMENT_RULE]}},
}


async def setup_demo(api: httpx.AsyncClient) -> None:
    await create_project(api)
    variations = [{"key": k, "value": k} for k in ("red", "blue", "green")]
    await create_flag(api, key="color", type="string", variations=variations, off_variation="red")
    await create_flag(api, key="gate")
    await create_flag(api, key="old")

    r = await api.post("/api/projects/demo/segments", json={"key": "beta", "name": "Beta"})
    assert r.status_code == 201, r.text
    r = await api.put(
        "/api/projects/demo/segments/beta",
        json={"included": ["u1"], "excluded": ["u2"], "rules": [SEGMENT_RULE]},
    )
    assert r.status_code == 200, r.text

    r = await api.post(f"{FLAGS}/gate/environments/production/toggle", json={"enabled": True})
    assert r.status_code == 200, r.text
    r = await api.put(
        f"{FLAGS}/color/environments/production",
        json={
            "version": 1,
            "enabled": True,
            "targets": [{"variation": "blue", "values": ["u3", "u0"]}],
            "rules": [
                {
                    "description": "beta users",
                    "clauses": [{"operator": "in_segment", "values": ["beta"]}],
                    "variation": "green",
                },
                {
                    "description": "new app",
                    "clauses": [{"attribute": "app", "operator": "semver_gt", "values": ["1.0.0"]}],
                    "rollout": APP_ROLLOUT,
                },
            ],
            "fallthrough": {"rollout": {"weights": [{"variation": "blue", "weight": 100000}]}},
            "prerequisites": [{"flag": "gate", "variation": "true"}],
        },
    )
    assert r.status_code == 200, r.text
    r = await api.post(f"{FLAGS}/old/archive")
    assert r.status_code == 200, r.text


async def build(app: FastAPI, env: str) -> dict[str, Any]:
    async with app.state.sessionmaker() as session:
        environment = await session.scalar(
            select(Environment)
            .join(Project, Project.id == Environment.project_id)
            .where(Project.key == "demo", Environment.key == env)
        )
        assert environment is not None
        return await build_ruleset(session, environment)


async def test_build_ruleset(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)

    ruleset = await build(app, "production")

    assert ruleset == EXPECTED
    schema = json.loads((SPEC / "ruleset.schema.json").read_text())
    Draft202012Validator(schema).validate(ruleset)
    assert json.loads(json.dumps(ruleset)) == ruleset
    assert set(parse_ruleset(ruleset).flags) == {"color", "gate"}


async def test_build_ruleset_per_environment(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)

    staging = await build(app, "staging")

    assert staging["environment"] == "staging"
    assert staging["flags"]["color"]["enabled"] is False
    assert staging["flags"]["color"]["fallthrough"] == {"variation": "red"}
    assert staging["flags"]["color"]["rules"] == []
    assert staging["segments"] == EXPECTED["segments"]
