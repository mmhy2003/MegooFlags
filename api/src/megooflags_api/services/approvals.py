"""Protected environments: how many approvals a change request needs, and the direct-write gate.

Only routes call `ensure_direct_write`, so applying an approved change request and the
scheduler's jobs write to a protected environment without it.
"""

from megooflags_api.errors import approval_required
from megooflags_api.models import Environment


def required_approvals(env: Environment) -> int:
    """Approvals a change request in `env` needs; at least one, even when env is unprotected."""
    return max(1, env.approvals_required)


def ensure_direct_write(env: Environment) -> None:
    """Refuse a direct write (409 approval_required) when `env` requires approval."""
    if env.approvals_required > 0:
        raise approval_required(env.key)
