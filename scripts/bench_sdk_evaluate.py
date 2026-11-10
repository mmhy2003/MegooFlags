#!/usr/bin/env python3
"""Manual benchmark of remote evaluation: POST /sdk/v1/evaluate at a fixed request rate.

Open loop: request n is due at start + n / rps, whatever the earlier requests are doing, and its
latency is measured from that due time, so a slow server cannot hide its queueing delay. At most
--concurrency requests are in flight; when the cap is reached, the next request waits for a free
slot and that wait counts towards its latency.

One Python process sends at most a few hundred requests per second, so higher rates need
--processes: each process sends rps / processes with concurrency / processes in flight, and the
latencies of all processes are merged.

    uv run --project api python scripts/bench_sdk_evaluate.py --url http://localhost:8080 \
        --key <client or server key> --rps 2000 --duration 30 --processes 8 [--flags a,b]

CI does not run it: shared runners are too noisy for latency targets.
"""

import argparse
import asyncio
import contextlib
import copy
import random
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor

import httpx
from latency import percentile


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url", required=True, help="Base URL of the API, e.g. http://localhost:8080"
    )
    parser.add_argument("--key", required=True, help="A server or client SDK key")
    parser.add_argument(
        "--rps", type=float, default=2000, help="Requests per second (default 2000)"
    )
    parser.add_argument("--duration", type=float, default=30, help="Seconds to run (default 30)")
    parser.add_argument(
        "--concurrency", type=int, default=512, help="Most requests in flight (default 512)"
    )
    parser.add_argument(
        "--flags",
        type=lambda value: [f for f in value.split(",") if f],
        default=None,
        help="Comma-separated flag keys to evaluate (default: every flag)",
    )
    parser.add_argument(
        "--processes", type=int, default=1, help="Client processes sharing the load (default 1)"
    )
    return parser.parse_args(argv)


def share(args: argparse.Namespace) -> argparse.Namespace:
    """The arguments of one of `args.processes` processes that share the load."""
    part = copy.copy(args)
    part.rps = args.rps / args.processes
    part.concurrency = max(1, args.concurrency // args.processes)
    return part


def run_sync(args: argparse.Namespace) -> tuple[list[float], int, float]:
    return asyncio.run(run(args))


async def run(args: argparse.Namespace) -> tuple[list[float], int, float]:
    """Send the requests; return the latencies of successful requests, the error count and the
    elapsed time in seconds."""
    total = int(args.rps * args.duration)
    latencies: list[float] = []
    errors = 0
    headers = {"Authorization": f"Bearer {args.key}"}
    # One single-connection client per in-flight slot: httpx's pool scans all of its connections
    # for every queued request, which caps one shared client at about 100 requests per second.
    one_connection = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    free: asyncio.Queue[httpx.AsyncClient] = asyncio.Queue()
    tls = httpx.create_ssl_context()  # shared: building one per client takes ~15 ms each

    async with contextlib.AsyncExitStack() as stack:
        for _ in range(args.concurrency):
            client = httpx.AsyncClient(
                base_url=args.url,
                headers=headers,
                limits=one_connection,
                timeout=10.0,
                verify=tls,
            )
            free.put_nowait(await stack.enter_async_context(client))

        async def one(client: httpx.AsyncClient, due: float) -> None:
            nonlocal errors
            try:
                body: dict[str, object] = {
                    "context": {"key": f"user-{random.randrange(1_000_000)}"}
                }
                if args.flags is not None:
                    body["flags"] = args.flags
                response = await client.post("/sdk/v1/evaluate", json=body)
                if response.status_code == 200:
                    latencies.append(time.perf_counter() - due)
                else:
                    errors += 1
            except Exception:  # any failure is an error; it must not abort the run
                errors += 1
            finally:
                free.put_nowait(client)

        tasks: set[asyncio.Task[None]] = set()
        start = time.perf_counter()
        for n in range(total):
            due = start + n / args.rps
            delay = due - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            client = await free.get()
            task = asyncio.create_task(one(client, due))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start
    return latencies, errors, elapsed


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.processes == 1:
        results = [run_sync(args)]
    else:
        with ProcessPoolExecutor(args.processes) as pool:
            results = list(pool.map(run_sync, [share(args)] * args.processes))
    latencies = [latency for result in results for latency in result[0]]
    errors = sum(result[1] for result in results)
    elapsed = max(result[2] for result in results)
    done = len(latencies) + errors
    print(f"requests {done} in {elapsed:.1f} s: {done / elapsed:.0f} RPS achieved")
    print(f"errors   {errors}")
    if latencies:
        p50, p95, p99 = (percentile(latencies, q) * 1000 for q in (0.5, 0.95, 0.99))
        print(f"latency  p50 {p50:.1f} ms  p95 {p95:.1f} ms  p99 {p99:.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
