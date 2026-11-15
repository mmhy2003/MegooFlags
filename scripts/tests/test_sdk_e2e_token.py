import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None,
    reason="needs bash and curl",
)

SCRIPT = Path(__file__).resolve().parents[1] / "sdk_e2e_token.sh"


class FakeApi:
    def __init__(self, setup_status: int = 201, tokens_status: int = 201) -> None:
        self.setup_status = setup_status
        self.tokens_status = tokens_status
        self.requests: list[dict[str, Any]] = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"null")
                api.requests.append(
                    {
                        "path": self.path,
                        "cookie": self.headers.get("Cookie"),
                        "origin": self.headers.get("Origin"),
                        "body": body,
                    }
                )
                if self.path == "/api/setup":
                    self.reply(api.setup_status, {}, cookie=api.setup_status == 201)
                elif self.path == "/api/auth/sign-in":
                    self.reply(200, {}, cookie=True)
                elif self.path == "/api/tokens":
                    self.reply(api.tokens_status, {"secret": "mf_api_test"})
                else:
                    self.reply(404, {})

            def reply(self, status: int, payload: object, cookie: bool = False) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if cookie:
                    self.send_header("Set-Cookie", "mf_session=s3ss; Path=/; HttpOnly")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def path(self, path: str) -> dict[str, Any]:
        return next(r for r in self.requests if r["path"] == path)


@pytest.fixture
def start() -> Iterator[Any]:
    servers: list[FakeApi] = []

    def factory(**kwargs: int) -> FakeApi:
        api = FakeApi(**kwargs)
        threading.Thread(target=api.server.serve_forever, daemon=True).start()
        servers.append(api)
        return api

    yield factory
    for api in servers:
        api.server.shutdown()
        api.server.server_close()


def run(url: str, **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("MEGOOFLAGS_")}
    return subprocess.run(
        ["bash", str(SCRIPT), url],
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_fresh_stack_prints_token(start):
    api = start()
    result = run(api.url)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "mf_api_test\n"
    tokens = api.path("/api/tokens")
    assert tokens["cookie"] == "mf_session=s3ss"
    assert tokens["origin"] == api.url
    assert tokens["body"] == {"name": "sdk-e2e", "bindings": [{"role": "admin", "scope": "org"}]}


def test_existing_stack_signs_in(start):
    api = start(setup_status=409)
    result = run(api.url, MEGOOFLAGS_E2E_PASSWORD="s3cret-pass")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "mf_api_test\n"
    sign_in = api.path("/api/auth/sign-in")
    assert sign_in["body"] == {"email": "admin@example.com", "password": "s3cret-pass"}
    assert api.path("/api/tokens")["cookie"] == "mf_session=s3ss"


def test_existing_stack_without_password_fails(start):
    api = start(setup_status=409)
    result = run(api.url)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "MEGOOFLAGS_E2E_PASSWORD" in result.stderr


def test_token_error_fails(start):
    api = start(tokens_status=500)
    result = run(api.url)
    assert result.returncode != 0
    assert result.stdout == ""
