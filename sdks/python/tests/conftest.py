"""Shared test setup. The helpers in this directory (ruleset_gen) are importable from tests/ and
perf/ through `pythonpath` in pyproject.toml."""

import threading
import time
from collections.abc import Iterator

import pytest


@pytest.fixture
def no_thread_leaks() -> Iterator[None]:
    """Fail the test when it leaves a thread running. The SDK's own threads (named megooflags-*)
    must be gone at once; the fake server's handler threads get a moment to notice their closed
    connections."""
    start = threading.active_count()
    yield
    sdk = [t.name for t in threading.enumerate() if t.name.startswith("megooflags-")]
    assert sdk == []
    deadline = time.monotonic() + 5
    while threading.active_count() > start and time.monotonic() < deadline:
        time.sleep(0.01)
    assert threading.active_count() == start, [t.name for t in threading.enumerate()]
