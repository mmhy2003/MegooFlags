import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

COMMUNITY_FILES = [
    "README.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "ROADMAP.md",
    "rfcs/README.md",
    "rfcs/0000-template.md",
]

ADVISORIES_URL = "https://github.com/mmhy2003/MegooFlags/security/advisories/new"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}")

BADGE_URLS = [
    "https://img.shields.io/github/actions/workflow/status/mmhy2003/MegooFlags/ci.yml"
    "?branch=main&label=CI&labelColor=1a1033",
    "https://img.shields.io/github/v/release/mmhy2003/MegooFlags"
    "?include_prereleases&labelColor=1a1033&color=c8006a",
    "https://img.shields.io/badge/license-Apache--2.0-007a87?labelColor=1a1033",
    "https://img.shields.io/badge/docs-mmhy2003.github.io%2FMegooFlags-7a1fd6?labelColor=1a1033",
    "https://img.shields.io/badge/OpenFeature-providers-c8006a?labelColor=1a1033",
]
SITE_URL = "https://mmhy2003.github.io/MegooFlags/"
QUICK_START_URL = "https://mmhy2003.github.io/MegooFlags/start/quick-start/"


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", COMMUNITY_FILES)
def test_community_file_exists(name: str) -> None:
    assert (ROOT / name).is_file()


def test_code_of_conduct_is_contributor_covenant_2_1() -> None:
    text = _read("CODE_OF_CONDUCT.md")
    assert "Contributor Covenant" in text
    assert "version 2.1" in text


def test_code_of_conduct_reports_through_github_only() -> None:
    text = _read("CODE_OF_CONDUCT.md")
    assert ADVISORIES_URL in text
    assert EMAIL.search(text) is None


@pytest.mark.parametrize("url", BADGE_URLS)
def test_readme_has_badge(url: str) -> None:
    assert url in _read("README.md")


def test_readme_links_site() -> None:
    text = _read("README.md")
    assert SITE_URL in text
    assert QUICK_START_URL in text
