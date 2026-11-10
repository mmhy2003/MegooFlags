import asyncio

import httpx
from bench_sdk_evaluate import parse_args, run, share


def test_bench_args():
    args = parse_args(["--url", "u", "--key", "k"])
    assert args.url == "u"
    assert args.key == "k"
    assert args.rps == 2000
    assert args.duration == 30
    assert args.concurrency == 512
    assert args.flags is None
    assert args.processes == 1


def test_bench_args_flags():
    args = parse_args(["--url", "u", "--key", "k", "--rps", "50", "--flags", "a,b"])
    assert args.rps == 50
    assert args.flags == ["a", "b"]


def test_unexpected_errors_are_counted(monkeypatch):
    async def broken_post(self, *args, **kwargs):
        raise RuntimeError("not an httpx error")

    monkeypatch.setattr(httpx.AsyncClient, "post", broken_post)
    args = parse_args(["--url", "http://127.0.0.1:9", "--key", "k", "--rps", "100"])
    args.duration = 0.05

    latencies, errors, _ = asyncio.run(run(args))

    assert latencies == []
    assert errors == 5


def test_share_splits_rate_and_concurrency():
    args = parse_args(["--url", "u", "--key", "k", "--processes", "8"])

    part = share(args)

    assert part.rps == 250
    assert part.concurrency == 64
    assert (part.url, part.duration) == ("u", 30)
    assert args.rps == 2000  # the original is left alone
