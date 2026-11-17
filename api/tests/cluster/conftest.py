from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from cluster.cluster_harness import Cluster, start_cluster


@pytest.fixture
async def cluster(migrated: None, valkey_up: None, tmp_path: Path) -> AsyncIterator[Cluster]:
    """A served control plane with the demo data. Workers are added by the test."""
    async with start_cluster(tmp_path) as cluster:
        yield cluster
