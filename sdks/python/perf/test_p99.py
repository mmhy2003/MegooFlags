"""p99 guard: evaluation stays under 1 ms on 10 000 flags. Runs alone: `uv run pytest perf -s`."""

import json
import random
import time

from ruleset_gen import bench_contexts, flag_keys, gen_ruleset

from megooflags._engine import evaluate, parse_ruleset

FLAGS = 10_000
WARM_UP = 10_000
SAMPLES = 100_000


def test_p99_under_1ms() -> None:
    rs = parse_ruleset(json.dumps(gen_ruleset(FLAGS)))
    keys = flag_keys(FLAGS)
    contexts = bench_contexts()
    rng = random.Random(12345)

    def pick() -> tuple[str, dict[str, object]]:
        return keys[rng.randrange(FLAGS)], contexts[rng.randrange(len(contexts))]

    for _ in range(WARM_UP):
        key, ctx = pick()
        evaluate(rs, key, ctx, None)
    samples = []
    clock = time.perf_counter_ns
    for _ in range(SAMPLES):
        key, ctx = pick()
        start = clock()
        evaluate(rs, key, ctx, None)
        samples.append((clock() - start) / 1e6)
    samples.sort()
    p50, p99, peak = samples[SAMPLES // 2], samples[SAMPLES * 99 // 100], samples[-1]
    print(f"p50 {p50:.4f} ms, p99 {p99:.4f} ms, max {peak:.4f} ms")
    assert p99 < 1.0
