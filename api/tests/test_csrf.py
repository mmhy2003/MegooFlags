from types import SimpleNamespace

import pytest
from fastapi import Request

from megooflags_api.auth.csrf import check_origin
from megooflags_api.errors import ApiError
from megooflags_api.settings import Settings


def make_request(
    method: str, headers: dict[str, str] | None = None, settings: Settings | None = None
) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(settings=settings or Settings()))
    raw = {"host": "test", **(headers or {})}
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "http",
            "path": "/api/projects",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in raw.items()],
            "server": ("test", 80),
            "client": ("192.0.2.10", 5555),
            "app": app,
        }
    )


def assert_rejected(request: Request) -> None:
    with pytest.raises(ApiError) as excinfo:
        check_origin(request)
    assert excinfo.value.status == 403
    assert excinfo.value.code == "csrf_failed"


def test_safe_method_passes_without_origin() -> None:
    check_origin(make_request("GET"))
    check_origin(make_request("HEAD", {"Origin": "https://evil.example"}))


def test_same_host_origin_passes() -> None:
    check_origin(make_request("POST", {"Origin": "http://test"}))


def test_foreign_origin_is_rejected() -> None:
    assert_rejected(make_request("POST", {"Origin": "https://evil.example"}))
    assert_rejected(make_request("DELETE", {"Origin": "https://test"}))


def test_missing_origin_needs_same_origin_fetch_metadata() -> None:
    check_origin(make_request("PUT", {"Sec-Fetch-Site": "same-origin"}))
    assert_rejected(make_request("PUT", {"Sec-Fetch-Site": "cross-site"}))
    assert_rejected(make_request("PUT"))


def test_public_url_origin_passes() -> None:
    settings = Settings(public_url="https://flags.example.org")
    check_origin(make_request("PATCH", {"Origin": "https://flags.example.org"}, settings))
    assert_rejected(make_request("PATCH", {"Origin": "https://other.example.org"}, settings))
