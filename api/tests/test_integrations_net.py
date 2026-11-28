import asyncio
import gzip
import time
from collections.abc import AsyncIterator, Iterable
from ipaddress import ip_address
from typing import Any

import httpcore
import httpx
import pytest

from megooflags_api.models.enums import IntegrationKind
from megooflags_api.services.integrations.net import (
    CheckedBackend,
    SafeTransport,
    UnsafeUrl,
    is_allowed_ip,
    post_json,
    validate_url,
)
from megooflags_api.settings import Settings


def test_webhook_settings_defaults() -> None:
    settings = Settings()

    assert settings.webhook_allow_private is False
    assert settings.webhook_timeout_seconds == 10
    assert settings.webhook_retention_days == 7


# --- validate_url ---


def test_https_url_is_accepted() -> None:
    validate_url("https://example.com/x", IntegrationKind.WEBHOOK, allow_private=False)


def test_slack_url_on_hooks_slack_com_is_accepted() -> None:
    validate_url(
        "https://hooks.slack.com/services/T/B/x", IntegrationKind.SLACK, allow_private=False
    )


def test_http_is_accepted_only_with_allow_private() -> None:
    validate_url("http://receiver:8080/hook", IntegrationKind.WEBHOOK, allow_private=True)

    with pytest.raises(UnsafeUrl):
        validate_url("http://example.com/x", IntegrationKind.WEBHOOK, allow_private=False)


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://user:pass@example.com/x", IntegrationKind.WEBHOOK),
        ("https://user@example.com/x", IntegrationKind.WEBHOOK),
        ("https:///x", IntegrationKind.WEBHOOK),
        ("ftp://example.com/x", IntegrationKind.WEBHOOK),
        ("not a url", IntegrationKind.WEBHOOK),
        ("https://example.com/services/T/B/x", IntegrationKind.SLACK),
        ("https://hooks.slack.com.evil.example/x", IntegrationKind.SLACK),
        # urlsplit accepts these, httpx refuses them when it builds the request.
        ("https://999.1.1.1/x", IntegrationKind.WEBHOOK),
        ("https://-\u00e9.example/x", IntegrationKind.WEBHOOK),  # invalid IDNA
        ("https://example.com/\x00", IntegrationKind.WEBHOOK),
    ],
)
def test_bad_urls_are_refused(url: str, kind: IntegrationKind) -> None:
    with pytest.raises(UnsafeUrl):
        validate_url(url, kind, allow_private=True)


# --- is_allowed_ip ---


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "::1",
        "169.254.169.254",
        "fe80::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "2002:7f00:1::1",  # 6to4 wrapping 127.0.0.1
        "0.0.0.0",
        "100.64.0.1",
        "224.0.0.1",
        "64:ff9b::a00:1",  # NAT64 wrapping 10.0.0.1
        "64:ff9b::7f00:1",  # NAT64 wrapping 127.0.0.1
    ],
)
def test_non_public_addresses_are_refused(ip: str) -> None:
    assert not is_allowed_ip(ip_address(ip), allow_private=False)


@pytest.mark.parametrize("ip", ["93.184.216.34", "2606:2800:220:1::1", "64:ff9b::5db8:d822"])
def test_public_addresses_are_allowed(ip: str) -> None:
    assert is_allowed_ip(ip_address(ip), allow_private=False)


def test_allow_private_allows_loopback_but_not_unspecified_or_multicast() -> None:
    assert is_allowed_ip(ip_address("127.0.0.1"), allow_private=True)
    assert is_allowed_ip(ip_address("10.0.0.1"), allow_private=True)
    assert not is_allowed_ip(ip_address("0.0.0.0"), allow_private=True)
    assert not is_allowed_ip(ip_address("::"), allow_private=True)
    assert not is_allowed_ip(ip_address("224.0.0.1"), allow_private=True)
    assert not is_allowed_ip(ip_address("ff02::1"), allow_private=True)


# --- CheckedBackend ---


class RecordingBackend(httpcore.AsyncNetworkBackend):
    """Stands in for the real network: records where it was asked to connect."""

    def __init__(self) -> None:
        self.connected: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connected.append((host, port))
        raise httpcore.ConnectError("recorded")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's interface
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise NotImplementedError

    async def sleep(self, seconds: float) -> None:
        pass


def fixed_resolver(*answers: list[str]) -> Any:
    calls = iter(answers)

    async def resolve(host: str, port: int) -> list[str]:
        return next(calls)

    return resolve


async def test_every_resolved_address_must_be_allowed() -> None:
    inner = RecordingBackend()
    backend = CheckedBackend(
        allow_private=False,
        resolver=fixed_resolver(["93.184.216.34", "10.0.0.5"]),
        backend=inner,
    )

    with pytest.raises(UnsafeUrl):
        await backend.connect_tcp("example.com", 443)
    assert inner.connected == []


async def test_connects_to_the_checked_address_not_the_hostname() -> None:
    inner = RecordingBackend()
    backend = CheckedBackend(
        allow_private=False, resolver=fixed_resolver(["93.184.216.34"]), backend=inner
    )

    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("example.com", 443)
    assert inner.connected == [("93.184.216.34", 443)]


async def test_dns_change_between_check_and_connect_is_caught() -> None:
    # The first answer is for the check made when the integration is saved, the second for the
    # lookup made when the request connects.
    resolver = fixed_resolver(["93.184.216.34"], ["127.0.0.1"])
    validate_url("https://example.com/x", IntegrationKind.WEBHOOK, allow_private=False)
    save_check = CheckedBackend(allow_private=False, resolver=resolver, backend=RecordingBackend())
    with pytest.raises(httpcore.ConnectError):  # passed the check, then reached the fake network
        await save_check.connect_tcp("example.com", 443)

    inner = RecordingBackend()
    transport = SafeTransport(allow_private=False, resolver=resolver, backend=inner)
    result = await post_json(
        "https://example.com/x",
        b"{}",
        {"content-type": "application/json"},
        allow_private=False,
        timeout=5,
        transport=transport,
    )

    assert result.status is None
    assert result.error is not None
    assert result.error.startswith("unsafe address")
    assert inner.connected == []


# --- post_json ---


async def test_redirect_is_not_followed() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    result = await post_json(
        "https://example.com/x",
        b"{}",
        {},
        allow_private=False,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )

    assert result.status == 302
    assert result.error is None
    assert len(requests) == 1


async def test_request_carries_body_and_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    result = await post_json(
        "https://example.com/x",
        b'{"a":1}',
        {"x-megooflags-signature": "sig"},
        allow_private=False,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )

    assert result.status == 204
    assert seen[0].method == "POST"
    assert seen[0].content == b'{"a":1}'
    assert seen[0].headers["x-megooflags-signature"] == "sig"
    assert result.duration_ms >= 0


class CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: int, size: int) -> None:
        self.chunks = chunks
        self.size = size
        self.sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.chunks):
            self.sent += self.size
            yield b"a" * self.size


async def test_large_body_is_cut() -> None:
    stream = CountingStream(chunks=200, size=1024)  # 200 KB

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=stream)

    result = await post_json(
        "https://example.com/x",
        b"{}",
        {},
        allow_private=False,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )

    assert result.status == 500
    assert result.body == "a" * 2048
    assert stream.sent <= 65536


async def test_timeout_becomes_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = await post_json(
        "https://example.com/x",
        b"{}",
        {},
        allow_private=False,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )

    assert result.status is None
    assert result.error == "timeout"


async def test_network_error_becomes_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await post_json(
        "https://example.com/x",
        b"{}",
        {},
        allow_private=False,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )

    assert result.status is None
    assert result.error is not None
    assert "refused" in result.error


async def test_unsafe_url_becomes_error_without_a_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    result = await post_json(
        "http://example.com/x",
        b"{}",
        {},
        allow_private=False,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )

    assert result.status is None
    assert result.error is not None
    assert result.error.startswith("unsafe address")
    assert requests == []


async def test_url_httpx_cannot_parse_becomes_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    result = await post_json(
        "https://999.1.1.1/x",
        b"{}",
        {},
        allow_private=False,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )

    assert result.status is None
    assert result.error == "unsafe address: the URL is not valid"


class TricklingStream(httpx.AsyncByteStream):
    """A receiver that sends one byte at a time, each well within a per-read timeout."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(100):
            await asyncio.sleep(0.05)
            yield b"a"


async def test_deadline_covers_the_whole_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=TricklingStream())

    started = time.monotonic()
    result = await post_json(
        "https://example.com/x",
        b"{}",
        {},
        allow_private=False,
        timeout=0.3,
        transport=httpx.MockTransport(handler),
    )

    assert result.status is None
    assert result.error == "timeout"
    assert time.monotonic() - started < 2


async def test_control_characters_are_removed_from_the_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"a\x00b\x01c\x7f\td\r\ne")

    result = await post_json(
        "https://example.com/x",
        b"{}",
        {},
        allow_private=False,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )

    assert result.body == "abc\td\r\ne"


async def test_body_is_not_decompressed() -> None:
    seen: list[httpx.Request] = []
    inflated = b"a" * 1_000_000

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            500,
            stream=httpx.ByteStream(gzip.compress(inflated)),
            headers={"content-encoding": "gzip"},
        )

    result = await post_json(
        "https://example.com/x",
        b"{}",
        {"accept-encoding": "gzip"},
        allow_private=False,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )

    assert seen[0].headers["accept-encoding"] == "identity"
    assert result.status == 500
    assert result.body != "a" * 2048  # the raw bytes were counted and kept, not inflated
