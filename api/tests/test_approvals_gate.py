import httpx
from helpers import assert_error
from test_rollouts import BODY as ROLLOUT_BODY
from test_rollouts import ROLLOUT, color_flag, config
from test_schedules import SCHEDULES, in_

ENVIRONMENTS = "/api/projects/demo/environments"
FLAG = "/api/projects/demo/flags/color"
PRODUCTION = f"{FLAG}/environments/production"
STAGING = f"{FLAG}/environments/staging"


async def protect(api: httpx.AsyncClient, env: str = "production", approvals: int = 1) -> None:
    r = await api.patch(f"{ENVIRONMENTS}/{env}", json={"approvals_required": approvals})
    assert r.status_code == 200, r.text


def assert_approval_required(r: httpx.Response, env: str = "production") -> None:
    body = assert_error(r, 409, "approval_required")
    assert body["details"] == {"environment": env}
    assert body["message"] == f"environment '{env}' requires approval; open a change request"


async def put_config(api: httpx.AsyncClient, url: str = PRODUCTION) -> httpx.Response:
    r = await api.get(url)
    assert r.status_code == 200, r.text
    body = {"version": r.json()["version"], "enabled": True, "fallthrough": {"variation": "c"}}
    return await api.put(url, json=body)


async def gated_writes(api: httpx.AsyncClient, base: str) -> dict[str, httpx.Response]:
    return {
        "config": await put_config(api, base),
        "toggle": await api.post(f"{base}/toggle", json={"enabled": True}),
        "schedule": await api.post(
            f"{base}/schedules", json={"action": "enable", "run_at": in_(hours=1)}
        ),
        "rollout": await api.post(f"{base}/rollout", json=ROLLOUT_BODY),
    }


async def test_direct_writes_to_a_protected_environment_are_409(api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await protect(api)
    before = await config(api)

    for name, r in (await gated_writes(api, PRODUCTION)).items():
        assert r.status_code == 409, (name, r.text)
        assert_approval_required(r)

    assert await config(api) == before
    assert (await api.get(SCHEDULES)).json() == []
    assert (await api.get(ROLLOUT)).json()["current"] is None


async def test_copy_into_a_protected_environment_is_409(api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await protect(api)

    r = await api.post(f"{FLAG}/copy", json={"from": "staging", "to": ["production"]})
    assert_approval_required(r)
    r = await api.post(
        f"{FLAG}/copy", json={"from": "staging", "to": ["development", "production"]}
    )
    assert_approval_required(r)

    r = await api.post(f"{FLAG}/copy", json={"from": "production", "to": ["staging"]})
    assert r.status_code == 200, r.text


async def test_the_kill_switch_stays_direct(api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await protect(api)

    r = await api.post(f"{PRODUCTION}/kill")

    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False


async def test_a_rollout_started_before_protection_can_still_be_driven(
    api: httpx.AsyncClient,
) -> None:
    await color_flag(api)
    r = await api.post(ROLLOUT, json=ROLLOUT_BODY)
    assert r.status_code == 201, r.text
    await protect(api)

    for action, status in (("pause", "paused"), ("resume", "active"), ("abort", "aborted")):
        r = await api.post(f"{ROLLOUT}/{action}")
        assert r.status_code == 200, (action, r.text)
        assert r.json()["status"] == status


async def test_a_schedule_created_before_protection_can_still_be_cancelled(
    api: httpx.AsyncClient,
) -> None:
    await color_flag(api)
    r = await api.post(SCHEDULES, json={"action": "enable", "run_at": in_(hours=1)})
    assert r.status_code == 201, r.text
    await protect(api)

    r = await api.delete(f"{SCHEDULES}/{r.json()['id']}")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"


async def test_other_environments_stay_writable(api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await protect(api)

    expected = {"config": 200, "toggle": 200, "schedule": 201, "rollout": 201}
    statuses = {name: r.status_code for name, r in (await gated_writes(api, STAGING)).items()}
    assert statuses == expected


async def test_setting_approvals_back_to_0_lifts_the_gate(api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await protect(api, approvals=2)
    assert_approval_required(await api.post(f"{PRODUCTION}/toggle", json={"enabled": True}))

    await protect(api, approvals=0)

    expected = {"config": 200, "toggle": 200, "schedule": 201, "rollout": 201}
    statuses = {name: r.status_code for name, r in (await gated_writes(api, PRODUCTION)).items()}
    assert statuses == expected
