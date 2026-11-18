import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
QUICK_START = ROOT / "site" / "src" / "content" / "docs" / "start" / "quick-start.mdx"
SCRIPT = ROOT / "scripts" / "quickstart_test.sh"

# What the script may add to a documented command: the CI override and the port that avoids 80.
ADDITIONS = ("-p mf-quickstart -f docker-compose.yml -f compose.ci.yml ", ":8080")

FENCE = re.compile(r"^[ \t]*```bash[ \t]*\n(.*?)^[ \t]*```", re.MULTILINE | re.DOTALL)


def doc_commands(text: str) -> list[str]:
    """The quick start's cp, sed, docker compose up and OFREP curl lines, in page order."""
    commands = []
    for block in FENCE.findall(text):
        for line in block.replace("\\\n", " ").splitlines():
            line = " ".join(line.split())
            if (
                line.startswith(("cp ", "sed ", "docker compose up"))
                or line.startswith("curl ")
                and "/ofrep/" in line
            ):
                commands.append(line)
    return commands


def normalise(text: str) -> str:
    for addition in ADDITIONS:
        text = text.replace(addition, "")
    return text


def test_doc_commands_picks_the_documented_lines() -> None:
    text = (
        "1. Start:\n\n   ```bash\n   cp .env.example .env\n   docker compose up -d --wait\n"
        "   echo skipped\n   ```\n\n```json\n{}\n```\n\n"
        "```bash\ncurl -s -X POST http://localhost/ofrep/v1/evaluate/flags/x \\\n  -d '{}'\n"
        "curl -fsS http://localhost/api/projects\n```\n"
    )
    assert doc_commands(text) == [
        "cp .env.example .env",
        "docker compose up -d --wait",
        "curl -s -X POST http://localhost/ofrep/v1/evaluate/flags/x -d '{}'",
    ]


def test_quick_start_has_the_checked_commands() -> None:
    commands = doc_commands(QUICK_START.read_text(encoding="utf-8"))
    assert [c.split()[0] for c in commands] == ["cp", "sed", "docker", "curl"]


@pytest.mark.parametrize("command", doc_commands(QUICK_START.read_text(encoding="utf-8")))
def test_script_runs_the_documented_command(command: str) -> None:
    assert SCRIPT.exists(), "scripts/quickstart_test.sh is missing"
    assert command in normalise(SCRIPT.read_text(encoding="utf-8"))
