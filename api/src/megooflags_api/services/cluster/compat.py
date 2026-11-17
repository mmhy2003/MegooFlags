"""Version rule for workers joining a control plane: which releases may run side by side."""

import re

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-rc\.\d+)?")


def minor_of(version: str) -> tuple[int, int]:
    """(X, Y) of "X.Y.Z" or "X.Y.Z-rc.N"; ValueError otherwise."""
    match = _VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"not a release version: {version!r}")
    return int(match.group(1)), int(match.group(2))


def _reason(worker: str, control_plane: str) -> str | None:
    try:
        w_major, w_minor = minor_of(worker)
    except ValueError:
        return "the worker's version is not a release version"
    try:
        cp_major, cp_minor = minor_of(control_plane)
    except ValueError:
        return "the control plane's version is not a release version"
    if w_major != cp_major:
        return "the major versions differ"
    if w_major == 0:
        return None if w_minor == cp_minor else "before 1.0 the minor versions must match"
    if w_minor > cp_minor:
        return "the worker is newer than the control plane"
    if w_minor < cp_minor - 1:
        return "the worker is more than one minor version behind"
    return None


def incompatibility(worker: str, control_plane: str) -> str | None:
    """None when the worker may connect, else why not (both versions in the text)."""
    reason = _reason(worker, control_plane)
    if reason is None:
        return None
    return (
        f"worker {worker} cannot join control plane {control_plane}: {reason}; "
        "upgrade the control plane first, then the workers"
    )
