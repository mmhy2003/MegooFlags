from typing import Any

import httpx
import pytest
from fastapi import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from megooflags_api.main import create_app
from megooflags_api.settings import Settings
from megooflags_api.urls import public_base_url

# Same value as FORWARDED_ALLOW_IPS in api/Dockerfile and deploy/docker-compose.yml.
PRIVATE_RANGES = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1"
HEADERS = {
    "Host": "flags.example.com",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-For": "198.51.100.7",
}


async def probe(settings: Settings, client_ip: str) -> dict[str, Any]:
    app = create_app(settings)

    async def endpoint(request: Request) -> dict[str, str]:
        client = request.client.host if request.client else ""
        return {"url": public_base_url(request), "client": client}

    app.add_api_route("/_probe", endpoint)
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=PRIVATE_RANGES)
    transport = httpx.ASGITransport(app=wrapped, client=(client_ip, 4321))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/_probe", headers=HEADERS)
            return dict(response.json())
    finally:
        await app.state.engine.dispose()


async def test_public_url_setting_wins() -> None:
    result = await probe(Settings(public_url="https://flags.example.org/"), "172.18.0.5")
    assert result["url"] == "https://flags.example.org"


async def test_forwarded_headers_trusted_from_private_proxy() -> None:
    result = await probe(Settings(), "172.18.0.5")
    assert result == {"url": "https://flags.example.com", "client": "198.51.100.7"}


async def test_forwarded_headers_ignored_from_untrusted_ip() -> None:
    result = await probe(Settings(), "203.0.113.9")
    assert result == {"url": "http://flags.example.com", "client": "203.0.113.9"}


def test_empty_public_url_env_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEGOOFLAGS_PUBLIC_URL", "")
    assert Settings().public_url is None
