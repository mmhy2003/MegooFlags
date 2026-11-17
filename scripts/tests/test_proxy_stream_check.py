import pytest
from proxy_stream_check import heartbeat_seen, parse_args


def test_heartbeat_seen_on_a_comment_line():
    assert heartbeat_seen(["retry: 3000", "", "id: e1", "event: put", "data: {}", "", ": ping"])
    assert heartbeat_seen([":"])


def test_heartbeat_not_seen_without_a_comment_line():
    assert not heartbeat_seen([])
    assert not heartbeat_seen(["retry: 3000", "", "event: put", "data: :ping", ""])


def test_parse_args_defaults(monkeypatch):
    monkeypatch.delenv("MEGOOFLAGS_API_TOKEN", raising=False)
    args = parse_args(["--token", "mf_api_x"])
    assert args.proxy == "https://localhost"
    assert args.api == "http://localhost:8080"
    assert args.token == "mf_api_x"
    assert args.insecure is False


def test_parse_args_token_from_the_environment(monkeypatch):
    monkeypatch.setenv("MEGOOFLAGS_API_TOKEN", "mf_api_env")
    args = parse_args(["--insecure", "--proxy", "https://flags.example.com"])
    assert args.token == "mf_api_env"
    assert args.insecure is True
    assert args.proxy == "https://flags.example.com"


def test_parse_args_requires_a_token(monkeypatch):
    monkeypatch.delenv("MEGOOFLAGS_API_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        parse_args([])
