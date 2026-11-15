import json
from collections.abc import Callable
from pathlib import Path

import pytest
from check_sdk_version import main

SDKS = ["node", "python", "java", "dotnet", "ruby", "php"]

MakeSdkRepo = Callable[..., Path]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def make_sdk_repo(tmp_path: Path) -> MakeSdkRepo:
    def factory(version: str = "0.1.0") -> Path:
        write(
            tmp_path / "sdks/node/package.json",
            json.dumps({"name": "@megooflags/node", "version": version}),
        )
        write(
            tmp_path / "sdks/python/pyproject.toml",
            f'[project]\nname = "megooflags"\nversion = "{version}"\n',
        )
        # The parent and dependency versions must not be mistaken for the project's own.
        write(
            tmp_path / "sdks/java/pom.xml",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
            "  <modelVersion>4.0.0</modelVersion>\n"
            "  <parent><groupId>x</groupId><artifactId>p</artifactId>"
            "<version>9.9.9</version></parent>\n"
            "  <groupId>io.github.mmhy2003</groupId>\n"
            "  <artifactId>megooflags</artifactId>\n"
            f"  <version>{version}</version>\n"
            "  <dependencies><dependency><groupId>d</groupId><artifactId>d</artifactId>"
            "<version>8.8.8</version></dependency></dependencies>\n"
            "</project>\n",
        )
        write(
            tmp_path / "sdks/dotnet/src/MegooFlags/MegooFlags.csproj",
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <PropertyGroup>\n"
            "    <LangVersion>12</LangVersion>\n"
            f"    <Version>{version}</Version>\n"
            "  </PropertyGroup>\n"
            "  <ItemGroup>\n"
            '    <PackageReference Include="OpenFeature" Version="2.14.1" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
        )
        write(
            tmp_path / "sdks/ruby/lib/megooflags/version.rb",
            f'# frozen_string_literal: true\n\nmodule MegooFlags\n  VERSION = "{version}"\nend\n',
        )
        write(
            tmp_path / "sdks/php/src/Version.php",
            "<?php\n\nnamespace MegooFlags;\n\nfinal class Version\n{\n"
            f"    public const VERSION = '{version}';\n}}\n",
        )
        return tmp_path

    return factory


def run(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


@pytest.mark.parametrize("sdk", SDKS)
def test_each_sdk_reads_its_version(make_sdk_repo, capsys, sdk):
    root = make_sdk_repo(version="1.2.3")
    assert run(root, "--sdk", sdk) == 0
    out = capsys.readouterr()
    assert out.out == f"sdk {sdk} version OK: 1.2.3\n"
    assert out.err == ""


@pytest.mark.parametrize("sdk", SDKS)
@pytest.mark.parametrize("suffix", ["", "-rc.1", "-rc.12"])
def test_matching_tag_passes(make_sdk_repo, capsys, sdk, suffix):
    tag = f"sdk-{sdk}/v0.1.0{suffix}"
    assert run(make_sdk_repo(), "--sdk", sdk, "--tag", tag) == 0
    out = capsys.readouterr()
    assert out.out == f"sdk {sdk} version OK: 0.1.0 (tag {tag})\n"
    assert out.err == ""


@pytest.mark.parametrize("tag", ["sdk-node/v0.2.0", "sdk-node/v0.2.0-rc.1", "sdk-node/v1.1.0"])
def test_tag_for_another_version_fails(make_sdk_repo, capsys, tag):
    assert run(make_sdk_repo(), "--sdk", "node", "--tag", tag) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == f"tag {tag} does not match sdks/node/package.json version 0.1.0\n"


@pytest.mark.parametrize(
    "tag",
    [
        "v0.1.0",
        "v0.1.0-rc.1",
        "sdk-python/v0.1.0",
        "sdks/go/v0.1.0",
        "sdks/node/v0.1.0",
        "sdk-node/0.1.0",
        "sdk-node/v0.1",
        "sdk-node/v0.1.0-rc",
        "sdk-node/v0.1.0-rc.0",
        "sdk-node/v0.1.0-beta.1",
        "sdk-node/v01.1.0",
        "sdk-node/v0.1.0+build",
    ],
)
def test_tag_with_wrong_form_fails(make_sdk_repo, capsys, tag):
    assert run(make_sdk_repo(), "--sdk", "node", "--tag", tag) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == (f"invalid tag {tag!r}: expected sdk-node/vX.Y.Z or sdk-node/vX.Y.Z-rc.N\n")


@pytest.mark.parametrize(
    ("sdk", "manifest"),
    [
        ("node", "sdks/node/package.json"),
        ("python", "sdks/python/pyproject.toml"),
        ("java", "sdks/java/pom.xml"),
        ("dotnet", "sdks/dotnet/src/MegooFlags/MegooFlags.csproj"),
        ("ruby", "sdks/ruby/lib/megooflags/version.rb"),
        ("php", "sdks/php/src/Version.php"),
    ],
)
def test_missing_manifest_fails(make_sdk_repo, capsys, sdk, manifest):
    root = make_sdk_repo()
    (root / manifest).unlink()
    assert run(root, "--sdk", sdk) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == f"{manifest} is missing\n"


@pytest.mark.parametrize(
    ("sdk", "manifest", "text"),
    [
        ("node", "sdks/node/package.json", '{"name": "x"}'),
        ("python", "sdks/python/pyproject.toml", '[project]\nname = "x"\n'),
        ("java", "sdks/java/pom.xml", '<project xmlns="http://maven.apache.org/POM/4.0.0"/>'),
        ("dotnet", "sdks/dotnet/src/MegooFlags/MegooFlags.csproj", "<Project/>"),
        ("ruby", "sdks/ruby/lib/megooflags/version.rb", "module MegooFlags\nend\n"),
        ("php", "sdks/php/src/Version.php", "<?php\n"),
    ],
)
def test_manifest_without_version_fails(make_sdk_repo, capsys, sdk, manifest, text):
    root = make_sdk_repo()
    (root / manifest).write_text(text, encoding="utf-8")
    assert run(root, "--sdk", sdk) == 1
    assert capsys.readouterr().err == f"{manifest} has no version\n"


@pytest.mark.parametrize(
    ("sdk", "manifest", "text"),
    [
        ("node", "sdks/node/package.json", "{"),
        ("python", "sdks/python/pyproject.toml", "[project\n"),
        ("java", "sdks/java/pom.xml", "<project>"),
        ("dotnet", "sdks/dotnet/src/MegooFlags/MegooFlags.csproj", "<Project>"),
    ],
)
def test_unreadable_manifest_fails(make_sdk_repo, capsys, sdk, manifest, text):
    root = make_sdk_repo()
    (root / manifest).write_text(text, encoding="utf-8")
    assert run(root, "--sdk", sdk) == 1
    assert capsys.readouterr().err.startswith(f"{manifest} cannot be read: ")


@pytest.mark.parametrize("sdk", SDKS)
@pytest.mark.parametrize("version", ["0.1", "0.1.0-rc.1", "v0.1.0", "01.0.0"])
def test_manifest_version_not_semver_fails(make_sdk_repo, capsys, sdk, version):
    assert run(make_sdk_repo(version=version), "--sdk", sdk, "--tag", f"sdk-{sdk}/v0.1.0") == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert f"has {version}, expected X.Y.Z\n" in out.err


@pytest.mark.parametrize("args", [["--sdk", "go"], ["--sdk", "rust"], []])
def test_unknown_or_missing_sdk_is_usage_error(make_sdk_repo, capsys, args):
    with pytest.raises(SystemExit) as exc:
        run(make_sdk_repo(), *args)
    assert exc.value.code == 2
    assert "--sdk" in capsys.readouterr().err
