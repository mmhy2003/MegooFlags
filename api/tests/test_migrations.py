import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

API_DIR = Path(__file__).resolve().parents[1]
REPO = API_DIR.parent

needs_db = pytest.mark.skipif(
    "MEGOOFLAGS_DATABASE_URL" not in os.environ, reason="MEGOOFLAGS_DATABASE_URL is not set"
)


def test_single_head() -> None:
    script = ScriptDirectory.from_config(Config(str(API_DIR / "alembic.ini")))

    assert script.get_heads() == ["0013"]


@needs_db
def test_upgrade_downgrade_roundtrip() -> None:
    subprocess.run(["bash", str(REPO / "scripts/check_migrations.sh")], check=True)


@needs_db
def test_models_match_migrations() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=API_DIR, check=True)
    subprocess.run([sys.executable, "-m", "alembic", "check"], cwd=API_DIR, check=True)
