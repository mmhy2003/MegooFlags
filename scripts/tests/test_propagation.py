import socket

import pytest
from latency import percentile
from propagation_test import parse_sse, replica_ips


def test_percentile():
    samples = [float(n) for n in range(20, 0, -1)]
    assert percentile(samples, 0.95) == 19
    assert percentile(samples, 0.5) == 10
    assert percentile(samples, 1.0) == 20
    assert percentile([0.25], 0.95) == 0.25
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_parse_sse():
    lines = [
        "retry: 3000",
        "",
        'id: "e1"',
        "event: put",
        'data: {"a":1}',
        "",
        ": ping",
        "",
        "event: revoked",
        "data: {}",
        "",
    ]
    assert list(parse_sse(lines)) == [("put", '{"a":1}', '"e1"'), ("revoked", "{}", None)]


def test_replica_ips_dedup(monkeypatch):
    def fake_getaddrinfo(host, port, family=0, type=0, *args):
        assert (host, port) == ("api", 8000)
        assert family == socket.AF_INET
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.5", 8000)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.3", 8000)),
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("172.18.0.5", 8000)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.4", 8000)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert replica_ips("api", 8000) == ["172.18.0.3", "172.18.0.4", "172.18.0.5"]
