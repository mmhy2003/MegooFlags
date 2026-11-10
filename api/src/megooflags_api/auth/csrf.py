"""CSRF protection for cookie-authenticated requests: the Origin check.

Bearer-token requests are exempt; the caller applies this to cookie requests only.
"""

from urllib.parse import urlsplit

from fastapi import Request

from megooflags_api.errors import csrf_failed
from megooflags_api.settings import Settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _allowed_origins(request: Request) -> set[str]:
    allowed = {f"{request.url.scheme}://{request.url.netloc}"}
    settings: Settings = request.app.state.settings
    if settings.public_url is not None:
        url = urlsplit(str(settings.public_url))
        allowed.add(f"{url.scheme}://{url.netloc}")
    return allowed


def check_origin(request: Request) -> None:
    """Unsafe methods need an Origin of MEGOOFLAGS_PUBLIC_URL or the request host, or, without an
    Origin, `Sec-Fetch-Site: same-origin`. Raises 403 csrf_failed otherwise."""
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        if request.headers.get("sec-fetch-site") == "same-origin":
            return
    elif origin in _allowed_origins(request):
        return
    raise csrf_failed()
