from starlette.types import Message, Scope, Send


def route_label(scope: Scope) -> str:
    """Route template ("/flags/{key}") for metrics and logs, or "other" when no route matched."""
    path = getattr(scope.get("route"), "path", None)
    return path if isinstance(path, str) else "other"


class ResponseStatus:
    """Wraps an ASGI `send` and remembers the response status (500 until a response starts)."""

    def __init__(self, send: Send) -> None:
        self.code = 500
        self._send = send

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.code = message["status"]
        await self._send(message)
