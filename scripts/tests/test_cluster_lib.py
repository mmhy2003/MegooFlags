import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

SCRIPTS = Path(__file__).resolve().parents[1]
LIB = SCRIPTS / "cluster" / "lib.sh"
CLUSTER_TEST = SCRIPTS / "cluster_test.sh"


@pytest.mark.parametrize("script", [LIB, CLUSTER_TEST], ids=lambda p: p.name)
def test_syntax(script: Path) -> None:
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_cluster_test_uses_the_library() -> None:
    text = CLUSTER_TEST.read_text(encoding="utf-8")
    assert "scripts/cluster/lib.sh" in text
    assert "cp_compose()" not in text
    assert "bootstrap_admin " in text


def test_library_defines_the_helpers() -> None:
    subprocess.run(
        ["bash", "-c", f". {LIB}; type cp_compose worker_compose bootstrap_admin mem_mib"],
        check=True,
        capture_output=True,
    )
