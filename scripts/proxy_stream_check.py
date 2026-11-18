#!/usr/bin/env python3
"""Check a reverse proxy in front of the bundle: the web UI loads, the SDK stream stays open past
the heartbeat, and a flag change reaches the stream through the proxy within 5 s.

Management calls go straight to the bundle (--api); the UI and the stream go through the proxy
(--proxy). Stdlib only. Used by scripts/proxy_samples_test.sh for docs/examples/proxies/.
"""

import argparse
import json
import os
import queue
import ssl
import sys
import threading
import time
import urllib.request
import uuid
from collections.abc import Iterable, Iterator
from typing import Any

from propagation_test import ENV, FLAG, STREAM_READ_TIMEOUT, Update, call, parse_sse, wait_for

HOLD_SECONDS = 20.0  # longer than the 15 s SSE heartbeat
# The first heartbeat comes 15 s after the stream's last internal message, and the server's stream
# re-check (every 30 s) sends one, so it can take up to 45 s.
HEARTBEAT_DEADLINE = 60.0
PROPAGATION_LIMIT = 5.0


def heartbeat_seen(lines: Iterable[str]) -> bool:
    """True when the stream carried a comment line, which is what the server sends as heartbeat."""
    return any(line.startswith(":") for line in lines)


def open_url(
    url: str, context: ssl.SSLContext | None, headers: dict[str, str] | None = None
) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(request, timeout=STREAM_READ_TIMEOUT, context=context)


class Stream:
    """An SDK stream read on a thread: every line it carried, and the flag's state from every
    `put` event, with (time, None) when the stream ended."""

    def __init__(self, resp: Any) -> None:
        self._resp = resp
        self.lines: list[str] = []
        self.updates: queue.Queue[Update] = queue.Queue()
        self.ended = threading.Event()
        self._closing = False
        threading.Thread(target=self._read, daemon=True).start()

    def _lines(self) -> Iterator[str]:
        for raw in self._resp:
            line = raw.decode().rstrip("\r\n")
            self.lines.append(line)
            yield line

    def _read(self) -> None:
        try:
            for event, data, _ in parse_sse(self._lines()):
                if event == "put":
                    enabled = json.loads(data)["flags"][FLAG]["enabled"]
                    self.updates.put((time.monotonic(), enabled))
        except Exception as exc:  # reported through the queue as a stream end
            if not self._closing:
                print(f"stream: {exc}", file=sys.stderr)
        self.ended.set()
        self.updates.put((time.monotonic(), None))

    def close(self) -> None:
        self._closing = True
        self._resp.close()


def hold(stream: Stream) -> None:
    """Keep the stream open for HOLD_SECONDS, then until a heartbeat has come through."""
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if stream.ended.is_set():
            raise RuntimeError(f"the stream closed after {elapsed:.0f} s")
        if elapsed >= HOLD_SECONDS and heartbeat_seen(stream.lines):
            print(f"stream held {elapsed:.0f} s with a heartbeat OK")
            return
        if elapsed >= HEARTBEAT_DEADLINE:
            raise RuntimeError(f"no heartbeat comment within {HEARTBEAT_DEADLINE:.0f} s")
        time.sleep(0.5)


def run(args: argparse.Namespace, context: ssl.SSLContext | None, api: str, project: str) -> float:
    token = args.token
    flag_env = f"{api}/projects/{project}/flags/{FLAG}/environments/{ENV}"
    call(
        "POST",
        f"{api}/projects/{project}/flags",
        {"key": FLAG, "name": "Proxy check", "type": "boolean"},
        key=token,
    )
    call("POST", f"{flag_env}/toggle", {"enabled": True}, key=token)
    key = call(
        "POST",
        f"{api}/projects/{project}/environments/{ENV}/sdk-keys",
        {"name": "proxy-check", "kind": "server"},
        key=token,
    )["key"]

    with open_url(f"{args.proxy}/sign-in", context) as resp:
        if resp.status != 200 or b"MegooFlags" not in resp.read():
            raise RuntimeError(f"GET {args.proxy}/sign-in: status {resp.status} without MegooFlags")
    print("GET /sign-in through the proxy OK")

    stream = Stream(
        open_url(
            f"{args.proxy}/sdk/v1/stream",
            context,
            {"Accept": "text/event-stream", "Authorization": f"Bearer {key}"},
        )
    )
    try:
        wait_for(stream.updates, True, 10.0, "first put")
        hold(stream)
        t0 = time.monotonic()
        call("POST", f"{flag_env}/toggle", {"enabled": False}, key=token)
        return wait_for(stream.updates, False, PROPAGATION_LIMIT, "the stream") - t0
    finally:
        stream.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--proxy", default="https://localhost", help="the proxy's public URL")
    parser.add_argument(
        "--api", default="http://localhost:8080", help="the bundle, without the proxy"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("MEGOOFLAGS_API_TOKEN"),
        help="API token for the management API (default: $MEGOOFLAGS_API_TOKEN)",
    )
    parser.add_argument(
        "--insecure", action="store_true", help="do not verify the proxy's certificate"
    )
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("--token or MEGOOFLAGS_API_TOKEN is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    context = ssl._create_unverified_context() if args.insecure else None
    api = f"{args.api}/api"
    project = f"proxy-{uuid.uuid4().hex[:8]}"
    call(
        "POST",
        f"{api}/projects",
        {"key": project, "name": project, "environments": [{"key": ENV, "name": "Production"}]},
        key=args.token,
    )
    try:
        elapsed = run(args, context, api, project)
    except (OSError, TimeoutError, RuntimeError) as exc:
        print(f"proxy stream: {exc}", file=sys.stderr)
        return 1
    finally:
        call("DELETE", f"{api}/projects/{project}", key=args.token)
    print(f"proxy stream OK ({elapsed:.3f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
