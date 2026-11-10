#!/usr/bin/env python3
"""Manual benchmark of remote evaluation: POST /sdk/v1/evaluate at a fixed request rate.

Open loop: request n is due at start + n / rps, whatever the earlier requests are doing, and its
latency is measured from that due time, so a slow server cannot hide its queueing delay. At most
--concurrency requests are in flight; when the cap is reached, the next request waits for a free
slot and that wait counts towards its latency.

    uv run --project api python scripts/bench_sdk_evaluate.py --url http://localhost:8080 \
        --key <client or server key> --rps 2000 --duration 30 [--flags a,b]

CI does not run it: shared runners are too noisy for latency targets.
"""

import argparse
import asyncio
import random
import sys
import time
from collections.abc import Sequence

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
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> tuple[list[float], int, float]:
    """Send the requests; return the latencies of successful requests, the error count and the
    elapsed time in seconds."""
    total = int(args.rps * args.duration)
    latencies: list[float] = []
    errors = 0
    slots = asyncio.Semaphore(args.concurrency)
    limits = httpx.Limits(
        max_connections=args.concurrency, max_keepalive_connections=args.concurrency
    )
    headers = {"Authorization": f"Bearer {args.key}"}

    async with httpx.AsyncClient(
        base_url=args.url, headers=headers, limits=limits, timeout=10.0
    ) as client:

        async def one(due: float) -> None:
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
            except httpx.HTTPError:
                errors += 1
            finally:
                slots.release()

        tasks: set[asyncio.Task[None]] = set()
        start = time.perf_counter()
        for n in range(total):
            due = start + n / args.rps
            delay = due - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            await slots.acquire()
            task = asyncio.create_task(one(due))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start
    return latencies, errors, elapsed


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    latencies, errors, elapsed = asyncio.run(run(args))
    done = len(latencies) + errors
    print(f"requests {done} in {elapsed:.1f} s: {done / elapsed:.0f} RPS achieved")
    print(f"errors   {errors}")
    if latencies:
        p50, p95, p99 = (percentile(latencies, q) * 1000 for q in (0.5, 0.95, 0.99))
        print(f"latency  p50 {p50:.1f} ms  p95 {p95:.1f} ms  p99 {p99:.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
