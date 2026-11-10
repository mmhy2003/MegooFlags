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


async def serve_ok(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """A keep-alive HTTP/1.1 server that answers every request with an empty JSON object."""
    try:
        while True:
            head = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in head.decode().split("\r\n"):
                name, _, value = line.partition(":")
                if name.lower() == "content-length":
                    length = int(value)
            await reader.readexactly(length)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n")
            writer.write(b"Content-Length: 2\r\n\r\n{}")
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        writer.close()


def test_run_measures_every_request():
    async def scenario():
        server = await asyncio.start_server(serve_ok, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        args = parse_args(["--url", f"http://127.0.0.1:{port}", "--key", "k", "--rps", "200"])
        args.duration = 0.1
        args.concurrency = 4
        async with server:
            return await run(args)

    latencies, errors, elapsed = asyncio.run(scenario())

    assert errors == 0
    assert len(latencies) == 20
    assert all(latency >= 0 for latency in latencies)
    assert elapsed >= 0.09
