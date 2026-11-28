from fastapi import Request

from megooflags_api.settings import Settings


def public_base_url(request: Request) -> str:
    """External base URL without a trailing slash.

    MEGOOFLAGS_PUBLIC_URL wins. Otherwise the URL comes from the request, whose scheme and client
    Uvicorn has already corrected from X-Forwarded-* headers sent by a trusted proxy.
    """
    settings: Settings = request.app.state.settings
    if settings.public_url is not None:
        return str(settings.public_url).rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}"
