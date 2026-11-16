"""Outbound requests for integrations: public https addresses only, no redirects, capped reads,
and one deadline for the whole request.

The host is resolved when the connection opens, every address must pass `is_allowed_ip`, and the
socket goes to the checked address. A DNS answer that changes after the URL was saved cannot point
a request at a private network. TLS SNI and the Host header keep the original hostname, because
httpcore passes `server_hostname` to `start_tls` separately.
"""

import asyncio
import re
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, IPv6Network, ip_address
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

from megooflags_api.models.enums import IntegrationKind

SLACK_HOST = "hooks.slack.com"
MAX_READ_BYTES = 65536
MAX_BODY_CHARS = 2048

# NAT64 (RFC 6052): the last 32 bits are the IPv4 address the gateway connects to.
NAT64_PREFIX = IPv6Network("64:ff9b::/96")
# Control characters other than tab and newlines; Postgres text refuses NUL.
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

Resolver = Callable[[str, int], Awaitable[list[str]]]


class UnsafeUrl(ValueError):
    pass


def validate_url(url: str, kind: IntegrationKind, *, allow_private: bool) -> None:
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError for a bad port
        httpx.URL(url)  # stricter than urlsplit: refuses what httpx could not send
    except (ValueError, httpx.InvalidURL) as exc:
        raise UnsafeUrl("unsafe address: the URL is not valid") from exc
    schemes = ("https", "http") if allow_private else ("https",)
    if parts.scheme not in schemes:
        raise UnsafeUrl("unsafe address: the URL must use https")
    if "@" in parts.netloc:
        raise UnsafeUrl("unsafe address: the URL must not contain a user name or password")
    if not parts.hostname:
        raise UnsafeUrl("unsafe address: the URL has no host")
    if kind is IntegrationKind.SLACK and parts.hostname != SLACK_HOST:
        raise UnsafeUrl(f"unsafe address: a Slack URL must be on {SLACK_HOST}")


def is_allowed_ip(ip: IPv4Address | IPv6Address, *, allow_private: bool) -> bool:
    if isinstance(ip, IPv6Address):
        if ip in NAT64_PREFIX:
            ip = IPv4Address(int(ip) & 0xFFFFFFFF)
        else:
            ip = ip.ipv4_mapped or ip.sixtofour or ip
    if ip.is_unspecified or ip.is_multicast:
        return False
    return allow_private or ip.is_global


async def resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class CheckedBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        *,
        allow_private: bool,
        resolver: Resolver = resolve,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._allow_private = allow_private
        self._resolver = resolver
        self._backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            answers = await asyncio.wait_for(self._resolver(host, port), timeout)
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout(f"resolving {host} timed out") from exc
        except OSError as exc:
            raise httpcore.ConnectError(f"cannot resolve {host}: {exc}") from exc
        if not answers:
            raise httpcore.ConnectError(f"cannot resolve {host}")
        try:
            addresses = [ip_address(answer) for answer in answers]
        except ValueError as exc:
            raise UnsafeUrl(f"unsafe address: {host} resolved to an invalid address") from exc
        for address in addresses:
            if not is_allowed_ip(address, allow_private=self._allow_private):
                raise UnsafeUrl(f"unsafe address: {host} resolves to {address}")
        return await self._backend.connect_tcp(
            str(addresses[0]),
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's interface
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise UnsafeUrl("unsafe address: unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class SafeTransport(httpx.AsyncHTTPTransport):
    def __init__(
        self,
        *,
        allow_private: bool,
        resolver: Resolver = resolve,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            network_backend=CheckedBackend(
                allow_private=allow_private, resolver=resolver, backend=backend
            ),
        )


async def _already_read(response: httpx.Response) -> AsyncIterator[bytes]:
    yield response.content


@dataclass
class PostResult:
    status: int | None
    body: str
    duration_ms: int
    error: str | None


async def post_json(
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    *,
    allow_private: bool,
    timeout: float,  # noqa: ASYNC109 - the deadline for the whole request, not each phase
    transport: httpx.AsyncBaseTransport | None = None,
) -> PostResult:
    """POST `body` to `url` and read at most MAX_READ_BYTES of the answer, as sent: the body
    is never decompressed. Never raises: failures come back in `PostResult.error`."""
    started = time.monotonic()

    def result(status: int | None, text: str = "", error: str | None = None) -> PostResult:
        return PostResult(status, text, int((time.monotonic() - started) * 1000), error)

    owned = transport is None
    client = httpx.AsyncClient(
        transport=transport or SafeTransport(allow_private=allow_private),
        follow_redirects=False,
        timeout=timeout,
        trust_env=False,
    )
    try:
        validate_url(url, IntegrationKind.WEBHOOK, allow_private=allow_private)
        sent = {k: v for k, v in headers.items() if k.lower() != "accept-encoding"}
        sent["Accept-Encoding"] = "identity"
        async with (
            asyncio.timeout(timeout),
            client.stream("POST", url, content=body, headers=sent) as response,
        ):
            read = bytearray()
            # A transport that answers with the body already read (a test's MockTransport)
            # leaves nothing to stream; the network always streams.
            chunks = (
                _already_read(response) if response.is_stream_consumed else response.aiter_raw()
            )
            async for chunk in chunks:
                read += chunk
                if len(read) >= MAX_READ_BYTES:
                    break
            text = bytes(read[:MAX_READ_BYTES]).decode("utf-8", errors="replace")
            return result(response.status_code, CONTROL_CHARS.sub("", text)[:MAX_BODY_CHARS])
    except UnsafeUrl as exc:
        return result(None, error=str(exc))
    except httpx.InvalidURL:  # validate_url parses with httpx too; kept so this never raises
        return result(None, error="unsafe address: the URL is not valid")
    except (TimeoutError, httpx.TimeoutException):
        return result(None, error="timeout")
    except httpx.HTTPError as exc:
        return result(None, error=str(exc) or type(exc).__name__)
    finally:
        if owned:
            await client.aclose()
