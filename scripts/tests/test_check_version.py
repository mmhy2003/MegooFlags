import pytest
from check_version import Tag, check, main, parse_tag, read_versions


def test_matching_versions_pass(make_repo):
    root = make_repo()
    assert read_versions(root) == {
        "VERSION": "0.1.0",
        "api/pyproject.toml": "0.1.0",
        "web/package.json": "0.1.0",
    }
    assert check(root) == []


def test_version_file_trailing_whitespace_ignored(make_repo):
    root = make_repo()
    (root / "VERSION").write_text("0.1.0  \n\n", encoding="utf-8")
    assert check(root) == []


def test_pyproject_mismatch_fails(make_repo):
    root = make_repo(api="0.2.0")
    assert check(root) == ["api/pyproject.toml has 0.2.0, VERSION has 0.1.0"]


def test_package_json_mismatch_fails(make_repo):
    root = make_repo(web="0.2.0")
    assert check(root) == ["web/package.json has 0.2.0, VERSION has 0.1.0"]


def test_missing_package_json_fails(make_repo):
    root = make_repo()
    (root / "web" / "package.json").unlink()
    assert read_versions(root)["web/package.json"] == "<missing>"
    assert check(root) == ["web/package.json has <missing>, VERSION has 0.1.0"]


def test_invalid_version_file_fails(make_repo):
    root = make_repo(version="0.1", api="0.1", web="0.1")
    errors = check(root)
    assert len(errors) == 1
    assert "VERSION" in errors[0]
    assert "0.1" in errors[0]


def test_parse_final_tag():
    tag = parse_tag("v0.1.0")
    assert tag == Tag(major=0, minor=1, patch=0, rc=None)
    assert tag.version == "0.1.0"
    assert tag.image_version == "0.1.0"


def test_parse_rc_tag():
    tag = parse_tag("v0.1.0-rc.1")
    assert tag.rc == 1
    assert tag.version == "0.1.0"
    assert tag.image_version == "0.1.0-rc.1"


@pytest.mark.parametrize(
    "tag",
    [
        "0.1.0",
        "v0.1",
        "v0.1.0-beta.1",
        "v01.0.0",
        "v0.1.0-rc",
        "v0.1.0-rc.0",
        "v0.1.0+build",
    ],
)
def test_invalid_tags_rejected(make_repo, tag):
    with pytest.raises(ValueError, match="invalid tag"):
        parse_tag(tag)
    errors = check(make_repo(), tag)
    assert len(errors) == 1
    assert errors[0].startswith("invalid tag")


@pytest.mark.parametrize("tag", ["v0.1.0", "v0.1.0-rc.1"])
def test_tag_matching_version_passes(make_repo, tag):
    assert check(make_repo(), tag) == []


@pytest.mark.parametrize("tag", ["v0.2.0", "v0.2.0-rc.1"])
def test_tag_mismatch_fails(make_repo, tag):
    assert check(make_repo(), tag) == [f"tag {tag} does not match VERSION 0.1.0"]


def test_main_exit_codes(make_repo, capsys):
    root = make_repo()
    assert main(["--root", str(root)]) == 0
    assert "version OK: 0.1.0" in capsys.readouterr().out

    assert main(["--root", str(root), "--tag", "v0.1.0-rc.1"]) == 0
    assert "version OK: 0.1.0 (tag v0.1.0-rc.1)" in capsys.readouterr().out

    root = make_repo(api="0.2.0")
    assert main(["--root", str(root)]) == 1
    assert "api/pyproject.toml has 0.2.0, VERSION has 0.1.0" in capsys.readouterr().err
