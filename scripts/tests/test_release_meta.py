import pytest
from check_version import parse_tag
from release_meta import changelog_section, image_tags, is_prerelease, main, release_notes

CHANGELOG = """# Changelog

Intro text.

## [Unreleased]

### Added
- Something new.

## [1.4.2]

### Fixed
- A bug.

## [0.1.0]

### Added
- First release.
"""


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.1.0", ["0.1.0", "0.1", "latest"]),
        ("v0.1.0-rc.1", ["0.1.0-rc.1"]),
        ("v1.4.2", ["1.4.2", "1.4", "1", "latest"]),
        ("v1.4.2-rc.3", ["1.4.2-rc.3"]),
    ],
)
def test_image_tags(tag, expected):
    assert image_tags(parse_tag(tag)) == expected


def test_no_major_tag_before_1_0():
    assert "0" not in image_tags(parse_tag("v0.9.9"))


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.1.0", True),
        ("v0.1.0-rc.1", True),
        ("v1.4.2", False),
        ("v1.4.2-rc.1", True),
    ],
)
def test_is_prerelease(tag, expected):
    assert is_prerelease(parse_tag(tag)) is expected


def test_changelog_section_extracts_body():
    assert changelog_section(CHANGELOG, "1.4.2") == "### Fixed\n- A bug."
    assert changelog_section(CHANGELOG, "0.1.0") == "### Added\n- First release."


def test_changelog_section_missing_returns_none():
    assert changelog_section(CHANGELOG, "9.9.9") is None
    assert changelog_section("## [Unreleased]\n\n## [0.1.0]\n- x\n", "Unreleased") is None


def test_final_release_requires_section():
    with pytest.raises(ValueError, match="CHANGELOG.md has no section for 2.0.0"):
        release_notes(CHANGELOG, parse_tag("v2.0.0"))


def test_rc_uses_version_section_when_present():
    assert release_notes(CHANGELOG, parse_tag("v1.4.2-rc.1")) == "### Fixed\n- A bug."


def test_rc_falls_back_to_unreleased():
    assert release_notes(CHANGELOG, parse_tag("v2.0.0-rc.1")) == "### Added\n- Something new."


def test_rc_without_any_section_fails():
    with pytest.raises(ValueError):
        release_notes("# Changelog\n\n## [Unreleased]\n", parse_tag("v2.0.0-rc.1"))


def test_main_outputs(tmp_path, capsys):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    notes = tmp_path / "notes.md"

    code = main(["--tag", "v0.1.0-rc.1", "--changelog", str(changelog), "--notes-out", str(notes)])

    assert code == 0
    assert capsys.readouterr().out.splitlines() == [
        "version=0.1.0",
        "image_version=0.1.0-rc.1",
        "tags=0.1.0-rc.1",
        "prerelease=true",
    ]
    assert notes.read_text(encoding="utf-8") == "### Added\n- First release.\n"


def test_main_final_tag_joins_tags(tmp_path, capsys):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")

    assert main(["--tag", "v1.4.2", "--changelog", str(changelog)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[2:] == ["tags=1.4.2,1.4,1,latest", "prerelease=false"]


def test_main_missing_notes_fails(tmp_path, capsys):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")

    assert main(["--tag", "v2.0.0", "--changelog", str(changelog)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "CHANGELOG.md has no section for 2.0.0" in captured.err


def test_main_invalid_tag_fails(tmp_path, capsys):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")

    assert main(["--tag", "1.0.0", "--changelog", str(changelog)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid tag" in captured.err
