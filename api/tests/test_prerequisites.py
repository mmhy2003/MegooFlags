import uuid
from typing import Any

import httpx
from helpers import assert_error, create_flag, create_project

from megooflags_api.services.prerequisites import has_cycle

FLAGS = "/api/projects/demo/flags"
A, B, C, D = (uuid.uuid4() for _ in range(4))


def test_has_cycle_chain_is_not_a_cycle() -> None:
    assert has_cycle({A: {B}, B: {C}}, A) is False


def test_has_cycle_two_flags() -> None:
    assert has_cycle({A: {B}, B: {A}}, A) is True


def test_has_cycle_three_flags() -> None:
    assert has_cycle({A: {B}, B: {C}, C: {A}}, A) is True


def test_has_cycle_diamond_is_not_a_cycle() -> None:
    assert has_cycle({A: {B, C}, B: {D}, C: {D}}, A) is False


async def put_prerequisites(
    api: httpx.AsyncClient, flag: str, prerequisites: list[dict[str, str]], env: str = "production"
) -> httpx.Response:
    url = f"{FLAGS}/{flag}/environments/{env}"
    current = (await api.get(url)).json()
    body: dict[str, Any] = {
        "version": current["version"],
        "enabled": current["enabled"],
        "fallthrough": current["fallthrough"],
        "prerequisites": prerequisites,
    }
    return await api.put(url, json=body)


async def setup_ab(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api, key="a")
    await create_flag(api, key="b")


async def test_put_prerequisite_ok(api: httpx.AsyncClient) -> None:
    await setup_ab(api)

    r = await put_prerequisites(api, "b", [{"flag": "a", "variation": "true"}])

    assert r.status_code == 200, r.text
    assert r.json()["prerequisites"] == [{"flag": "a", "variation": "true"}]
    got = (await api.get(f"{FLAGS}/b/environments/production")).json()
    assert got["prerequisites"] == [{"flag": "a", "variation": "true"}]


async def test_prerequisites_keep_their_order(api: httpx.AsyncClient) -> None:
    await setup_ab(api)
    await create_flag(api, key="c")
    prerequisites = [{"flag": "c", "variation": "false"}, {"flag": "a", "variation": "true"}]

    assert (await put_prerequisites(api, "b", prerequisites)).status_code == 200

    got = (await api.get(f"{FLAGS}/b/environments/production")).json()
    assert got["prerequisites"] == prerequisites


async def test_self_reference_422(api: httpx.AsyncClient) -> None:
    await setup_ab(api)

    r = await put_prerequisites(api, "b", [{"flag": "b", "variation": "true"}])

    body = assert_error(r, 422, "prerequisite_cycle")
    assert body["details"] == {"path": "prerequisites[0].flag"}


async def test_cycle_422(api: httpx.AsyncClient) -> None:
    await setup_ab(api)
    assert (
        await put_prerequisites(api, "a", [{"flag": "b", "variation": "true"}])
    ).status_code == 200

    r = await put_prerequisites(api, "b", [{"flag": "a", "variation": "true"}])

    body = assert_error(r, 422, "prerequisite_cycle")
    assert body["details"] == {"path": "prerequisites"}
    r = await put_prerequisites(api, "b", [{"flag": "a", "variation": "true"}], env="development")
    assert r.status_code == 200, r.text


async def test_longer_cycle_422(api: httpx.AsyncClient) -> None:
    await setup_ab(api)
    await create_flag(api, key="c")
    assert (
        await put_prerequisites(api, "a", [{"flag": "b", "variation": "true"}])
    ).status_code == 200
    assert (
        await put_prerequisites(api, "b", [{"flag": "c", "variation": "true"}])
    ).status_code == 200

    r = await put_prerequisites(api, "c", [{"flag": "a", "variation": "true"}])

    assert_error(r, 422, "prerequisite_cycle")


async def test_unknown_flag_422(api: httpx.AsyncClient) -> None:
    await setup_ab(api)

    r = await put_prerequisites(api, "b", [{"flag": "nope", "variation": "true"}])

    body = assert_error(r, 422, "invalid_reference")
    assert body["details"] == {"path": "prerequisites[0].flag"}


async def test_other_project_flag_422(api: httpx.AsyncClient) -> None:
    await setup_ab(api)
    await create_project(api, key="other")
    await create_flag(api, project="other", key="x")

    r = await put_prerequisites(api, "b", [{"flag": "x", "variation": "true"}])

    body = assert_error(r, 422, "invalid_reference")
    assert body["details"] == {"path": "prerequisites[0].flag"}


async def test_unknown_variation_of_prereq_422(api: httpx.AsyncClient) -> None:
    await setup_ab(api)

    r = await put_prerequisites(api, "b", [{"flag": "a", "variation": "maybe"}])

    body = assert_error(r, 422, "invalid_reference")
    assert body["details"] == {"path": "prerequisites[0].variation"}


async def test_archived_prereq_422(api: httpx.AsyncClient) -> None:
    await setup_ab(api)
    assert (await api.post(f"{FLAGS}/a/archive")).status_code == 200

    r = await put_prerequisites(api, "b", [{"flag": "a", "variation": "true"}])

    body = assert_error(r, 422, "invalid_reference")
    assert body["message"] == "prerequisite flag 'a' is archived"
    assert body["details"] == {"path": "prerequisites[0].flag"}


async def test_duplicate_prereq_422(api: httpx.AsyncClient) -> None:
    await setup_ab(api)
    prerequisites = [{"flag": "a", "variation": "true"}, {"flag": "a", "variation": "false"}]

    r = await put_prerequisites(api, "b", prerequisites)

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "prerequisites[1].flag"}


async def test_refused_put_keeps_the_old_prerequisites(api: httpx.AsyncClient) -> None:
    await setup_ab(api)
    assert (
        await put_prerequisites(api, "b", [{"flag": "a", "variation": "true"}])
    ).status_code == 200

    assert_error(
        await put_prerequisites(api, "b", [{"flag": "nope", "variation": "true"}]),
        422,
        "invalid_reference",
    )

    got = (await api.get(f"{FLAGS}/b/environments/production")).json()
    assert got["prerequisites"] == [{"flag": "a", "variation": "true"}]


async def test_flag_in_use_as_prerequisite(api: httpx.AsyncClient) -> None:
    await setup_ab(api)
    assert (
        await put_prerequisites(api, "b", [{"flag": "a", "variation": "true"}])
    ).status_code == 200

    body = assert_error(await api.post(f"{FLAGS}/a/archive"), 409, "in_use")
    assert body["details"] == {"used_by": [{"flag": "b", "environment": "production"}]}
    assert_error(await api.delete(f"{FLAGS}/a"), 409, "not_archived")

    assert (await put_prerequisites(api, "b", [])).status_code == 200
    assert (await api.post(f"{FLAGS}/a/archive")).status_code == 200
    assert (await api.delete(f"{FLAGS}/a")).status_code == 204


async def test_variation_in_use_as_prerequisite(api: httpx.AsyncClient) -> None:
    # Boolean variations can never be deleted, so the prerequisite is a string flag.
    await setup_ab(api)
    await create_flag(
        api,
        key="color",
        type="string",
        variations=[{"key": key, "value": key} for key in ("red", "green")],
        off_variation="red",
    )
    r = await put_prerequisites(api, "b", [{"flag": "color", "variation": "green"}])
    assert r.status_code == 200, r.text

    body = assert_error(await api.delete(f"{FLAGS}/color/variations/green"), 409, "in_use")
    assert body["details"] == {
        "used_by": [{"usage": "prerequisites", "flag": "b", "environment": "production"}]
    }

    assert (await put_prerequisites(api, "b", [])).status_code == 200
    assert (await api.delete(f"{FLAGS}/color/variations/green")).status_code == 204
