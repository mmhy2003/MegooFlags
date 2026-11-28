#!/usr/bin/env python3
"""Kill-switch propagation test (F-6): flip a flag off and time how long every API replica's SSE
stream takes to deliver the change. Exit 1 if p95 is 5 s or more, or if any replica times out.

Stdlib only, so it runs inside the API image on the compose network.
"""

import argparse
import json
import os
import queue
import socket
import sys
import threading
import time
import urllib.request
import uuid
from collections.abc import Iterable, Iterator
from typing import Any

from latency import percentile

FLAG = "kill-me"
ENV = "production"
P95_LIMIT = 5.0
STREAM_READ_TIMEOUT = 60.0  # longer than the SSE heartbeat

# Per replica: (monotonic time received, flag enabled), or (time, None) when the stream ended.
Update = tuple[float, bool | None]


def replica_ips(host: str, port: int) -> list[str]:
    """The unique IPv4 addresses `host` resolves to: one per replica on the compose network."""
    infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, str, str | None]]:
    """Yield (event, data, id) per dispatched event; comments and `retry` yield nothing."""
    event, data, event_id = "message", [], None
    for line in lines:
        if not line:
            if data:
                yield event, "\n".join(data), event_id
            event, data, event_id = "message", [], None
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
        elif name == "id":
            event_id = value


def call(method: str, url: str, body: Any = None, key: str | None = None) -> Any:
    """Send a JSON request and return the decoded JSON response (None when empty)."""
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def watch(url: str, key: str, updates: "queue.Queue[Update]") -> None:
    """Read one replica's stream and push the flag's state from every `put` event."""
    request = urllib.request.Request(
        url, headers={"Accept": "text/event-stream", "Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=STREAM_READ_TIMEOUT) as resp:
            lines = (raw.decode().rstrip("\r\n") for raw in resp)
            for event, data, _ in parse_sse(lines):
                if event == "put":
                    enabled = json.loads(data)["flags"][FLAG]["enabled"]
                    updates.put((time.monotonic(), enabled))
                elif event == "revoked":
                    break
    except Exception as exc:  # reported through the queue as a stream end
        print(f"stream {url}: {exc}", file=sys.stderr)
    updates.put((time.monotonic(), None))


def wait_for(updates: "queue.Queue[Update]", expected: bool, timeout: float, name: str) -> float:
    """Return the time at which the replica reported `expected`."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"{name} did not report enabled={expected} within {timeout} s")
        try:
            received, enabled = updates.get(timeout=remaining)
        except queue.Empty:
            continue
        if enabled is None:
            raise RuntimeError(f"{name}: stream ended")
        if enabled == expected:
            return received


def run(args: argparse.Namespace, ips: list[str], api: str, project: str) -> list[float]:
    token = args.token
    flag_env = f"{api}/projects/{project}/flags/{FLAG}/environments/{ENV}"
    call(
        "POST",
        f"{api}/projects/{project}/flags",
        {"key": FLAG, "name": "Kill me", "type": "boolean"},
        key=token,
    )
    call("POST", f"{flag_env}/toggle", {"enabled": True}, key=token)
    created = call(
        "POST",
        f"{api}/projects/{project}/environments/{ENV}/sdk-keys",
        {"name": "propagation", "kind": "server"},
        key=token,
    )
    key = created["key"]

    queues: dict[str, queue.Queue[Update]] = {}
    for ip in ips:
        queues[ip] = queue.Queue()
        url = f"http://{ip}:{args.port}/sdk/v1/stream"
        threading.Thread(target=watch, args=(url, key, queues[ip]), daemon=True).start()

    samples: list[float] = []
    for _ in range(args.iterations):
        call("POST", f"{flag_env}/toggle", {"enabled": True}, key=token)
        for ip, updates in queues.items():
            wait_for(updates, True, args.timeout, ip)
        t0 = time.monotonic()
        call("POST", f"{flag_env}/kill", key=token)
        for ip, updates in queues.items():
            samples.append(wait_for(updates, False, args.timeout, ip) - t0)
    return samples


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="api")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--replicas", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--token",
        default=os.environ.get("MEGOOFLAGS_API_TOKEN"),
        help="API token for the management API (default: $MEGOOFLAGS_API_TOKEN)",
    )
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("--token or MEGOOFLAGS_API_TOKEN is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    ips = replica_ips(args.host, args.port)
    if len(ips) != args.replicas:
        print(f"expected {args.replicas} replicas of {args.host}, found {ips}", file=sys.stderr)
        return 1
    api = f"http://{ips[0]}:{args.port}/api"
    project = f"prop-{uuid.uuid4().hex[:8]}"
    call(
        "POST",
        f"{api}/projects",
        {"key": project, "name": project, "environments": [{"key": ENV, "name": "Production"}]},
        key=args.token,
    )
    try:
        samples = run(args, ips, api, project)
    except (TimeoutError, RuntimeError) as exc:
        print(f"propagation: {exc}", file=sys.stderr)
        return 1
    finally:
        call("DELETE", f"{api}/projects/{project}", key=args.token)

    p50, p95 = percentile(samples, 0.5), percentile(samples, 0.95)
    print(f"propagation: n={len(samples)} p50={p50:.3f}s p95={p95:.3f}s max={max(samples):.3f}s")
    return 1 if p95 >= P95_LIMIT else 0


if __name__ == "__main__":
    sys.exit(main())
