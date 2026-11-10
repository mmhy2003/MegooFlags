"""CORS for the endpoints that browser SDKs call with client keys.

Any origin may call them, never with credentials: client keys are sent in a header (or `?key=` on
the stream), not in cookies. `/sdk/v1/ruleset` is for server keys and gets no CORS headers.
"""

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORS_PATHS = frozenset({"/sdk/v1/evaluate", "/sdk/v1/stream", "/sdk/v1/events"})
CORS_PREFIX = "/ofrep/"

PREFLIGHT_HEADERS = [
    (b"access-control-allow-origin", b"*"),
    (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
    (
        b"access-control-allow-headers",
        b"Authorization, Content-Type, X-API-Key, If-None-Match, Last-Event-ID",
    ),
    (b"access-control-max-age", b"600"),
]


def is_cors_path(path: str) -> bool:
    return path in CORS_PATHS or path.startswith(CORS_PREFIX)


class SdkCorsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_cors_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        if scope["method"] == "OPTIONS" and "access-control-request-method" in Headers(scope=scope):
            await send({"type": "http.response.start", "status": 204, "headers": PREFLIGHT_HEADERS})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Access-Control-Allow-Origin"] = "*"
                headers["Access-Control-Expose-Headers"] = "ETag, Retry-After"
            await send(message)

        await self.app(scope, receive, send_with_cors)
