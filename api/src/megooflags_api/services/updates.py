"""Update notices: which MegooFlags release is the latest, asked of the GitHub Releases API.

The request carries nothing about the instance but a `User-Agent` with the running version. Only a
tag of the form `vX.Y.Z` is accepted, and the release URL is built from it, never taken from the
response. `releases/latest` returns only full releases; a 404 means there is none yet.
"""

import json
import re
from datetime import timedelta
from typing import Any

import httpx

RELEASES_URL = "https://api.github.com/repos/mmhy2003/MegooFlags/releases/latest"
RELEASE_URL = "https://github.com/mmhy2003/MegooFlags/releases/tag/v{version}"
CHECK_EVERY = timedelta(hours=24)
RETRY_AFTER = timedelta(hours=1)
MAX_BODY_BYTES = 1_048_576
TIMEOUT_SECONDS = 10.0

_TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


class UpdateCheckError(Exception):
    pass


def parse_release_tag(tag: object) -> str | None:
    """`"v1.2.3"` -> `"1.2.3"`; anything else, pre-releases and build metadata included, -> None."""
    if not isinstance(tag, str) or _TAG.fullmatch(tag) is None:
        return None
    return tag[1:]


def version_key(version: str) -> tuple[Any, ...] | None:
    """A sort key with SemVer 2.0 precedence, ignoring build metadata; None if unparseable."""
    match = _VERSION.fullmatch(version)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.group(1, 2, 3))
    pre = match.group(4)
    if pre is None:
        # A release ranks above all of its pre-releases.
        return (major, minor, patch, 1, ())
    ids = pre.split(".")
    if "" in ids:
        return None
    # Numeric identifiers compare as numbers and rank below alphanumeric ones.
    return (major, minor, patch, 0, tuple((0, int(i)) if i.isdigit() else (1, i) for i in ids))


def is_newer(candidate: str, running: str) -> bool:
    candidate_key = version_key(candidate)
    running_key = version_key(running)
    if candidate_key is None or running_key is None:
        return False
    return bool(candidate_key > running_key)


def release_url(version: str) -> str:
    return RELEASE_URL.format(version=version)


async def fetch_latest(client: httpx.AsyncClient, *, running: str) -> str | None:
    """The latest full release's version, or None when there is none yet."""
    headers = {"User-Agent": f"MegooFlags/{running}", "Accept": "application/vnd.github+json"}
    try:
        async with client.stream("GET", RELEASES_URL, headers=headers) as response:
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise UpdateCheckError(f"GitHub answered {response.status_code}")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise UpdateCheckError(f"the response is over {MAX_BODY_BYTES} bytes")
        payload = json.loads(body.decode("utf-8"))
    except httpx.HTTPError as error:
        raise UpdateCheckError(f"the request failed: {type(error).__name__}") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UpdateCheckError("the response is not JSON") from error
    version = parse_release_tag(payload.get("tag_name")) if isinstance(payload, dict) else None
    if version is None:
        raise UpdateCheckError("the response has no vX.Y.Z tag_name")
    return version
