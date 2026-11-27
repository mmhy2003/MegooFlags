import json
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from helpers import create_flag, create_project
from jsonschema import Draft202012Validator
from sqlalchemy import select

from megooflags_api.evaluation import parse_ruleset
from megooflags_api.models import Environment, Flag, FlagEnvironment, Project
from megooflags_api.services.ruleset import (
    build_flags,
    build_parts,
    build_ruleset,
    flag_versions,
)

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


async def demo_environment(session: Any, env: str) -> Environment:
    environment = await session.scalar(
        select(Environment)
        .join(Project, Project.id == Environment.project_id)
        .where(Project.key == "demo", Environment.key == env)
    )
    assert environment is not None
    return environment


async def build(app: FastAPI, env: str) -> dict[str, Any]:
    async with app.state.sessionmaker() as session:
        return await build_ruleset(session, await demo_environment(session, env))


async def demo_versions(session: Any, environment: Environment) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(Flag.id, FlagEnvironment.version)
        .join(FlagEnvironment, FlagEnvironment.flag_id == Flag.id)
        .where(FlagEnvironment.environment_id == environment.id)
    )
    return {flag_id: version for flag_id, version in rows}


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


async def test_build_parts_matches_build_ruleset(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)

    async with app.state.sessionmaker() as session:
        environment = await demo_environment(session, "production")
        parts = await build_parts(session, environment)
        ruleset = await build_ruleset(session, environment)

    assert parts.to_json() == ruleset == EXPECTED
    assert list(parts.to_json()) == list(ruleset)


async def test_build_flags_by_id_equals_the_subset(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    await create_flag(api, key="extra")

    async with app.state.sessionmaker() as session:
        environment = await demo_environment(session, "production")
        full = await build_flags(session, environment)
        ids = {id_ for id_, key in full.ids.items() if key in ("color", "gate")}
        subset = await build_flags(session, environment, ids)

    assert set(full.flags) == {"color", "extra", "gate"}
    assert subset.flags == {key: full.flags[key] for key in ("color", "gate")}
    assert list(subset.flags) == ["color", "gate"]
    assert subset.ids == {id_: full.ids[id_] for id_ in ids}
    assert subset.versions == {id_: full.versions[id_] for id_ in ids}
    assert subset.versions == {id_: 2 for id_ in ids}


async def test_flag_versions(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)

    async with app.state.sessionmaker() as session:
        environment = await demo_environment(session, "production")
        versions = await flag_versions(session, environment)
        everything = await demo_versions(session, environment)
        live = await build_flags(session, environment)
        archived = await session.scalar(select(Flag.id).where(Flag.key == "old"))

    assert versions == live.versions
    assert set(versions.values()) == {2}
    assert archived in everything
    assert archived not in versions
