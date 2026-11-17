#!/usr/bin/env python3
"""Multi-node failure scenarios (PRD 8.5): drive a control plane, three workers and a load balancer
through each row of the failure table. Print `PASS <row>` per row; exit 1 with the reason otherwise.

Stdlib only. Containers are stopped and started through `--ctl` (scripts/cluster_test.sh ctl).
"""

import argparse
import json
import os
import queue
import shlex
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from latency import percentile
from propagation_test import ENV, FLAG, P95_LIMIT, Update, call, wait_for, watch

PROJECT = "cluster"
COUNTER = "outage-counter"
NAMES = ["worker-1", "worker-2", "worker-3"]
CONTEXT = {"key": "cluster-user"}
OUTAGE_COUNT = 5
HEARTBEAT_STALE_WAIT = 35.0
STREAM_PUT_LIMIT = 5.0
DB_FLIP_LIMIT = 10.0
MEMORY_LIMIT_MIB = 256.0

Response = tuple[int, dict[str, str], bytes]


class Failure(Exception):
    """A failure-table row did not hold."""


def all_status(nodes: Iterable[dict[str, Any]], names: Sequence[str], status: str) -> bool:
    """True when every named node has `status`; healthy also means on the current snapshot."""
    by_name = {node["name"]: node for node in nodes}
    for name in names:
        node = by_name.get(name)
        if node is None or node["status"] != status:
            return False
        if status == "healthy" and node["syncing"]:
            return False
    return True


def eval_total(series: dict[str, Any]) -> int:
    """The evaluations counted over every bucket of an evaluations series."""
    return sum(sum(point["counts"].values()) for point in series["points"])


def p95_ok(samples: Sequence[float], limit: float = P95_LIMIT) -> bool:
    return percentile(samples, 0.95) < limit


def request(
    method: str, url: str, body: Any = None, key: str | None = None, timeout: float = 10.0
) -> Response:
    """Send a request and return (status, lower-cased headers, body), errors included."""
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


def status_of(url: str) -> int | None:
    """The status of GET `url`, or None when it cannot be reached."""
    try:
        return request("GET", url, timeout=5)[0]
    except (OSError, urllib.error.URLError):
        return None


def wait_until(check: Callable[[], bool], timeout: float, what: str, interval: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise Failure(f"{what} not within {timeout:.0f} s")
        time.sleep(interval)


class Cluster:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.api = f"{args.cp}/api"
        self.flag_env = f"{self.api}/projects/{PROJECT}/flags/{FLAG}/environments/{ENV}"
        self.server_key = ""
        self.client_key = ""

    def ctl(self, action: str, *params: str) -> str:
        command = shlex.split(self.args.ctl) + [action, *params]
        result = subprocess.run(command, stdout=subprocess.PIPE, text=True, check=False)
        if result.returncode != 0:
            raise Failure(f"ctl {action} {' '.join(params)} exited {result.returncode}")
        return result.stdout

    def manage(self, method: str, path: str, body: Any = None) -> Any:
        return call(method, f"{self.api}{path}", body, key=self.args.token)

    def nodes(self) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = self.manage("GET", "/nodes")["nodes"]
        return nodes

    def wait_nodes(self, names: Sequence[str], status: str, timeout: float) -> None:
        wait_until(lambda: all_status(self.nodes(), names, status), timeout, f"{names} {status}")

    def wait_ready(self, base: str, timeout: float) -> None:
        wait_until(lambda: status_of(f"{base}/readyz") == 200, timeout, f"{base} ready")

    def flip(self, enabled: bool) -> None:
        """Turn the kill-me flag on, or flip its kill switch."""
        if enabled:
            call("POST", f"{self.flag_env}/toggle", {"enabled": True}, key=self.args.token)
        else:
            call("POST", f"{self.flag_env}/kill", key=self.args.token)

    def ruleset(self, base: str) -> Response:
        return request("GET", f"{base}/sdk/v1/ruleset", key=self.server_key)

    def stream(self, base: str) -> "queue.Queue[Update]":
        updates: queue.Queue[Update] = queue.Queue()
        url = f"{base}/sdk/v1/stream"
        threading.Thread(target=watch, args=(url, self.server_key, updates), daemon=True).start()
        return updates

    def expect(self, base: str, response: Response, status: int, what: str) -> Response:
        if response[0] != status:
            raise Failure(f"{base} {what}: {response[0]} (expected {status}) {response[2][:200]!r}")
        return response

    def serves(self, base: str, etag: str | None = None, key: str | None = None) -> None:
        """Ruleset (with `etag` when given), evaluate and OFREP answer 200 on `base`."""
        ruleset = self.expect(base, self.ruleset(base), 200, "ruleset")
        if etag is not None and ruleset[1].get("etag") != etag:
            raise Failure(f"{base} ruleset ETag {ruleset[1].get('etag')} != {etag}")
        key = key or self.server_key
        body = {"context": CONTEXT, "flags": [FLAG]}
        self.expect(base, request("POST", f"{base}/sdk/v1/evaluate", body, key), 200, "evaluate")
        ofrep = {"context": {"targetingKey": CONTEXT["key"]}}
        url = f"{base}/ofrep/v1/evaluate/flags/{FLAG}"
        self.expect(base, request("POST", url, ofrep, key), 200, "OFREP")

    def streams_put(self, base: str, timeout: float = STREAM_PUT_LIMIT) -> None:
        updates = self.stream(base)
        try:
            _, enabled = updates.get(timeout=timeout)
        except queue.Empty:
            raise Failure(f"{base} stream: no put within {timeout:.0f} s") from None
        if enabled is None:
            raise Failure(f"{base} stream ended before put")

    def eval_total(self) -> int:
        path = f"/projects/{PROJECT}/flags/{COUNTER}/environments/{ENV}/evaluations"
        return eval_total(self.manage("GET", path))

    def flips(self, bases: Sequence[str], iterations: int, timeout: float) -> list[float]:
        """Kill-switch latencies, one per stream per flip."""
        streams = {base: self.stream(base) for base in bases}
        samples: list[float] = []
        try:
            for _ in range(iterations):
                self.flip(True)
                for base, updates in streams.items():
                    wait_for(updates, True, timeout, base)
                t0 = time.monotonic()
                self.flip(False)
                for base, updates in streams.items():
                    samples.append(wait_for(updates, False, timeout, base) - t0)
        except (TimeoutError, RuntimeError) as exc:
            raise Failure(str(exc)) from exc
        return samples

    # Phases, in order.

    def setup(self) -> None:
        self.manage(
            "POST",
            "/projects",
            {
                "key": PROJECT,
                "name": "Cluster",
                "environments": [{"key": ENV, "name": "Production"}],
            },
        )
        for key, name in ((FLAG, "Kill me"), (COUNTER, "Outage counter")):
            self.manage(
                "POST", f"/projects/{PROJECT}/flags", {"key": key, "name": name, "type": "boolean"}
            )
        self.flip(True)
        keys = f"/projects/{PROJECT}/environments/{ENV}/sdk-keys"
        self.server_key = self.manage("POST", keys, {"name": "cluster", "kind": "server"})["key"]
        self.client_key = self.manage("POST", keys, {"name": "browser", "kind": "client"})["key"]
        self.wait_nodes(NAMES, "healthy", 60)
        print("PASS setup: three workers healthy and current")

    def one_worker_down(self) -> None:
        lb, worker2 = self.args.lb, self.args.worker[1]
        on_worker2 = self.stream(worker2)
        try:
            wait_for(on_worker2, True, STREAM_PUT_LIMIT, worker2)
        except (TimeoutError, RuntimeError) as exc:
            raise Failure(str(exc)) from exc
        self.ctl("stop-worker", "2")
        try:
            wait_for_end(on_worker2, 30)
        except TimeoutError as exc:
            raise Failure(str(exc)) from exc
        self.streams_put(lb)
        for _ in range(50):
            self.serves(lb)
        time.sleep(HEARTBEAT_STALE_WAIT)
        if not all_status(self.nodes(), ["worker-2"], "stale"):
            raise Failure("worker-2 is not shown stale 35 s after it stopped")
        self.ctl("start-worker", "2")
        self.wait_nodes(NAMES, "healthy", 60)
        print("PASS one worker down: the LB serves, streams reconnect, worker-2 shown stale")

    def control_plane_lost(self) -> None:
        etag = self.expect(self.args.cp, self.ruleset(self.args.cp), 200, "ruleset")[1]["etag"]
        self.ctl("stop-control-plane")
        wait_until(lambda: status_of(f"{self.args.cp}/readyz") != 200, 30, "control plane down")
        for base in [self.args.lb, *self.args.worker]:
            self.serves(base, etag)
            self.serves(base, etag, key=self.client_key)
            self.streams_put(base)
        worker1 = self.args.worker[0]
        self.ctl("restart-worker", "1")
        self.wait_ready(worker1, 30)
        self.serves(worker1, etag)
        self.fresh_worker_not_ready()
        hour = time.strftime("%Y-%m-%dT%H:00:00+00:00", time.gmtime())
        events = {"counts": [{"flag_key": COUNTER, "hour": hour, "count": OUTAGE_COUNT}]}
        for base in self.args.worker:
            url = f"{base}/sdk/v1/events"
            self.expect(base, request("POST", url, events, self.server_key), 202, "events")
        print("PASS control plane lost: workers serve from the snapshot, restart from disk")

    def fresh_worker_not_ready(self) -> None:
        fresh = self.args.fresh
        self.ctl("start-fresh-worker")
        try:
            # Caddy answers 502 until the api container listens.
            wait_until(lambda: status_of(f"{fresh}/readyz") not in (None, 502), 60, "fresh worker")
            ready = status_of(f"{fresh}/readyz")
            if ready != 503:
                raise Failure(f"fresh worker /readyz {ready} (expected 503)")
            self.expect(fresh, self.ruleset(fresh), 503, "ruleset on a fresh worker")
        finally:
            self.ctl("stop-fresh-worker")

    def control_plane_restored(self) -> None:
        self.ctl("start-control-plane")
        self.wait_ready(self.args.cp, 120)
        self.wait_nodes(NAMES, "healthy", 60)
        samples = self.flips(self.args.worker, 20, 10.0)
        p95 = percentile(samples, 0.95)
        if not p95_ok(samples):
            raise Failure(f"kill switch p95 {p95:.3f} s is {P95_LIMIT:.0f} s or more")
        expected = OUTAGE_COUNT * len(self.args.worker)
        wait_until(lambda: self.eval_total() >= expected, 60, f"{COUNTER} total {expected}")
        time.sleep(15)
        total = self.eval_total()
        if total != expected:
            raise Failure(f"{COUNTER} total {total} (expected exactly {expected})")
        print(f"PASS control plane restored: healthy, p95={p95:.3f}s, events counted once")

    def database_down(self) -> None:
        cp = self.args.cp
        self.ctl("stop-db")
        try:
            for _ in range(10):
                self.serves(self.args.lb)
            wait_until(lambda: status_of(f"{cp}/readyz") == 503, 30, "control plane not ready")
        finally:
            self.ctl("start-db")
        self.wait_ready(cp, 120)
        streams = {base: self.stream(base) for base in self.args.worker}
        try:
            for base, updates in streams.items():
                wait_for(updates, False, STREAM_PUT_LIMIT, base)
            self.flip(True)
            for base, updates in streams.items():
                wait_for(updates, True, DB_FLIP_LIMIT, base)
        except (TimeoutError, RuntimeError) as exc:
            raise Failure(str(exc)) from exc
        print("PASS database down: workers serve, control plane not ready, changes resume")

    def footprint(self) -> None:
        for n in range(1, len(self.args.worker) + 1):
            mib = float(self.ctl("mem-worker", str(n)).split()[-1])
            if mib >= MEMORY_LIMIT_MIB:
                raise Failure(f"worker-{n} uses {mib:.0f} MiB ({MEMORY_LIMIT_MIB:.0f} or more)")
        print(f"PASS footprint: every worker under {MEMORY_LIMIT_MIB:.0f} MiB")


def wait_for_end(updates: "queue.Queue[Update]", timeout: float) -> None:
    """Wait until the stream behind `updates` has ended."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"the stream did not end within {timeout:.0f} s")
        try:
            _, enabled = updates.get(timeout=remaining)
        except queue.Empty:
            continue
        if enabled is None:
            return


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--lb", required=True, help="The load balancer in front of the workers")
    parser.add_argument("--cp", required=True, help="The control plane")
    parser.add_argument("--worker", action="append", required=True, help="Once per worker, 1..3")
    parser.add_argument("--fresh", required=True, help="A worker that has never synced")
    parser.add_argument("--ca", help="CA bundle for the HTTPS URLs")
    parser.add_argument(
        "--token",
        default=os.environ.get("MEGOOFLAGS_API_TOKEN"),
        help="API token for the management API (default: $MEGOOFLAGS_API_TOKEN)",
    )
    parser.add_argument("--ctl", default="bash scripts/cluster_test.sh ctl")
    args = parser.parse_args(argv)
    if len(args.worker) != len(NAMES):
        parser.error(f"--worker is needed {len(NAMES)} times, got {len(args.worker)}")
    if not args.token:
        parser.error("--token or MEGOOFLAGS_API_TOKEN is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.ca:
        context = ssl.create_default_context(cafile=args.ca)
        urllib.request.install_opener(
            urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
        )
    cluster = Cluster(args)
    phases = [
        cluster.setup,
        cluster.one_worker_down,
        cluster.control_plane_lost,
        cluster.control_plane_restored,
        cluster.database_down,
        cluster.footprint,
    ]
    for phase in phases:
        try:
            phase()
        except (Failure, OSError, urllib.error.URLError, KeyError, ValueError) as exc:
            print(f"FAIL {phase.__name__.replace('_', ' ')}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
