"""Polling for a condition that another thread makes true."""

import time
from collections.abc import Callable


def wait_for(condition: Callable[[], object], timeout: float = 5.0) -> None:
    """Return as soon as condition() is truthy; fail after timeout seconds."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.005)
