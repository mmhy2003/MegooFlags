"""An in-process cluster: one control plane and any number of workers, in one event loop.

httpx's ASGITransport buffers whole responses, so every app is served with `helpers.running()`
(uvicorn on a free port). Each worker reaches the control plane through its own
`CuttableTransport`, the link a test cuts to simulate an outage.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from conftest import close_app
from fastapi import FastAPI
from helpers import bearer, reset_database, running, seed_service_token
from pydantic import SecretStr
from test_sdk import setup_demo

from megooflags_api.main import create_app
from megooflags_api.settings import Settings


class _CuttableStream(httpx.AsyncByteStream):
    """A response body that fails with ReadError once its link is cut."""

    def __init__(self, stream: httpx.AsyncByteStream, cut: asyncio.Event) -> None:
        self._stream = stream
        self._cut = cut

    async def __aiter__(self) -> AsyncIterator[bytes]:
        chunks = aiter(self._stream)
        cut = asyncio.ensure_future(self._cut.wait())
        try:
            while True:
                if self._cut.is_set():
                    raise httpx.ReadError("the link was cut")
                read = asyncio.ensure_future(anext(chunks))
                await asyncio.wait({read, cut}, return_when=asyncio.FIRST_COMPLETED)
                if not read.done():
                    read.cancel()
                    await asyncio.gather(read, return_exceptions=True)
                    raise httpx.ReadError("the link was cut")
                try:
                    yield read.result()
                except StopAsyncIteration:
                    return
        finally:
            cut.cancel()

    async def aclose(self) -> None:
        await self._stream.aclose()


class CuttableTransport(httpx.AsyncBaseTransport):
    """Wraps AsyncHTTPTransport. cut(): open responses raise httpx.ReadError on the next read and
    new requests raise httpx.ConnectError. restore() lets requests through again."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self._cut = asyncio.Event()

    def cut(self) -> None:
        self._cut.set()

    def restore(self) -> None:
        # Responses opened before the cut keep the set event, so they stay broken.
        if self._cut.is_set():
            self._cut = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._cut.is_set():
            raise httpx.ConnectError("the link is cut", request=request)
        response = await self._inner.handle_async_request(request)
        assert isinstance(response.stream, httpx.AsyncByteStream)
        response.stream = _CuttableStream(response.stream, self._cut)
        return response

    async def aclose(self) -> None:
        # A worker's client closes its transport on shutdown; a restarted worker reuses the link.
        inner, self._inner = self._inner, httpx.AsyncHTTPTransport()
        await inner.aclose()


async def eventually[T](check: Callable[[], Awaitable[T | None]], within: float, what: str) -> T:
    """Poll `check` until it returns something other than None or False."""
    deadline = time.monotonic() + within
    while True:
        result = await check()
        if result is not None and result is not False:
            return result
        if time.monotonic() >= deadline:
            raise AssertionError(f"not {what} within {within} s")
        await asyncio.sleep(0.05)


def worker_settings(cp_url: str, token: str, data_dir: Path) -> Settings:
    return Settings(
        role="worker",
        control_plane_url=cp_url,
        node_token=SecretStr(token),
        control_plane_insecure=True,
        data_dir=data_dir,
        worker_drain_grace_seconds=0.5,
    )


@dataclass
class Worker:
    name: str
    node_id: str
    token: str
    data_dir: Path
    link: CuttableTransport
    settings: Settings
    version: str | None = None
    app: FastAPI = field(init=False)
    url: str = field(init=False)
    sdk: httpx.AsyncClient = field(init=False)
    _stack: AsyncExitStack | None = field(default=None, init=False)

    async def start(self) -> None:
        self.app = create_app(self.settings, cluster_transport=self.link)
        client = self.app.state.worker_client
        client.heartbeat_seconds = 0.2
        client.backoff_max = 0.5
        client.incompatible_retry_seconds = 0.5
        if self.version is not None:
            client.version = self.version
        buffer = self.app.state.event_buffer
        buffer.seal_seconds = buffer.forward_seconds = 0.2
        stack = AsyncExitStack()
        self.url = await stack.enter_async_context(running(self.app))
        self.sdk = await stack.enter_async_context(httpx.AsyncClient(base_url=self.url, timeout=10))
        self._stack = stack

    async def stop(self) -> None:
        stack, self._stack = self._stack, None
        if stack is not None:
            await stack.aclose()

    async def restart(self) -> None:
        """Stop, create the app again on the same data_dir and link, start."""
        await self.stop()
        await self.start()


def control_plane_settings(**overrides: Any) -> Settings:
    return Settings(
        role="control-plane", sse_heartbeat_seconds=1, stream_recheck_seconds=1
    ).model_copy(update=overrides)


class Cluster:
    def __init__(self, cp_app: FastAPI, tmp: Path) -> None:
        self.cp_app = cp_app
        self.cp_url = ""
        self.admin: httpx.AsyncClient  # org admin service token, base_url=cp_url
        self._tmp = tmp
        self._cp_stack = AsyncExitStack()
        self._cp_open = True  # cp_app's resources are not closed yet
        self.workers: list[Worker] = []

    async def serve_control_plane(self, port: int = 0) -> None:
        self.cp_url = await self._cp_stack.enter_async_context(running(self.cp_app, port))

    async def stop_control_plane(self) -> None:
        """Stop serving cp_app and close it; a second call does nothing."""
        if not self._cp_open:
            return
        self._cp_open = False
        stack, self._cp_stack = self._cp_stack, AsyncExitStack()
        await stack.aclose()
        await self.cp_app.state.ruleset_cache.aclose()
        await close_app(self.cp_app)

    async def restart_control_plane(self, settings: Settings) -> None:
        """Serve a new control-plane app with `settings` on the same port and database."""
        port = httpx.URL(self.cp_url).port
        assert port is not None
        await self.stop_control_plane()
        self.cp_app = create_app(settings)
        self._cp_open = True
        await self.serve_control_plane(port)

    async def add_worker(
        self, name: str, *, token: str | None = None, version: str | None = None
    ) -> Worker:
        """Start a worker. Without `token` the node is created on the control plane first."""
        node_id = ""
        if token is None:
            r = await self.admin.post("/api/nodes", json={"name": name})
            assert r.status_code == 201, r.text
            node_id, token = r.json()["id"], r.json()["token"]
        worker = Worker(
            name,
            node_id,
            token,
            self._tmp / name,
            CuttableTransport(),
            worker_settings(self.cp_url, token, self._tmp / name),
            version,
        )
        await worker.start()
        self.workers.append(worker)
        return worker

    async def wait_ready(self, worker: Worker, within: float = 5.0) -> None:
        async def ready() -> bool:
            return (await worker.sdk.get("/readyz")).status_code == 200

        await eventually(ready, within, f"{worker.name} ready")

    async def node(self, node_id: str) -> dict[str, Any]:
        r = await self.admin.get("/api/nodes")
        assert r.status_code == 200, r.text
        [node] = [n for n in r.json()["nodes"] if n["id"] == node_id]
        return node  # type: ignore[no-any-return]

    async def wait_status(self, node_id: str, status: str, within: float = 10.0) -> dict[str, Any]:
        async def matches() -> dict[str, Any] | None:
            node = await self.node(node_id)
            return node if node["status"] == status else None

        return await eventually(matches, within, f"node {node_id} {status}")

    async def aclose(self) -> None:
        for worker in self.workers:
            await worker.stop()


@asynccontextmanager
async def start_cluster(tmp: Path) -> AsyncIterator[Cluster]:
    """A served control plane with the demo data; workers are added by the test."""
    cluster = Cluster(create_app(control_plane_settings()), tmp)
    await reset_database(cluster.cp_app)
    try:
        headers = bearer(await seed_service_token(cluster.cp_app))
        await cluster.serve_control_plane()
        async with httpx.AsyncClient(base_url=cluster.cp_url, timeout=10, headers=headers) as admin:
            cluster.admin = admin
            await setup_demo(admin)
            try:
                yield cluster
            finally:
                await cluster.aclose()
    finally:
        await cluster.stop_control_plane()
