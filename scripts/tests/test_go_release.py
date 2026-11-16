import hashlib
import subprocess
import tarfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from go_release import TARGETS, archive_name, binary_name, build, main, sha256sums

CLI_NAMES = [
    ("linux", "amd64", "megooflags", "megooflags_0.1.0_linux_amd64.tar.gz"),
    ("linux", "arm64", "megooflags", "megooflags_0.1.0_linux_arm64.tar.gz"),
    ("darwin", "amd64", "megooflags", "megooflags_0.1.0_darwin_amd64.tar.gz"),
    ("darwin", "arm64", "megooflags", "megooflags_0.1.0_darwin_arm64.tar.gz"),
    ("windows", "amd64", "megooflags.exe", "megooflags_0.1.0_windows_amd64.zip"),
    ("windows", "arm64", "megooflags.exe", "megooflags_0.1.0_windows_arm64.zip"),
]

TF = "terraform-provider-megooflags"
TERRAFORM_NAMES = [
    ("linux", "amd64", f"{TF}_v0.1.0", f"{TF}_0.1.0_linux_amd64.zip"),
    ("linux", "arm64", f"{TF}_v0.1.0", f"{TF}_0.1.0_linux_arm64.zip"),
    ("darwin", "amd64", f"{TF}_v0.1.0", f"{TF}_0.1.0_darwin_amd64.zip"),
    ("darwin", "arm64", f"{TF}_v0.1.0", f"{TF}_0.1.0_darwin_arm64.zip"),
    ("windows", "amd64", f"{TF}_v0.1.0.exe", f"{TF}_0.1.0_windows_amd64.zip"),
    ("windows", "arm64", f"{TF}_v0.1.0.exe", f"{TF}_0.1.0_windows_arm64.zip"),
]


class FakeGo:
    """Stands in for subprocess.run: records each go build and writes a dummy binary to -o."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def __call__(
        self, cmd: Sequence[str], *, cwd: Path, env: Mapping[str, str], check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        args = list(cmd)
        self.calls.append((args, Path(cwd), dict(env)))
        out = Path(args[args.index("-o") + 1])
        out.write_bytes(f"binary {env['GOOS']}/{env['GOARCH']}".encode())
        return subprocess.CompletedProcess(args, 0)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "cli").mkdir(parents=True)
    (root / "cli" / "README.md").write_text("# CLI\n", encoding="utf-8")
    (root / "cli" / "LICENSE").write_text("license\n", encoding="utf-8")
    (root / "terraform").mkdir()
    return root


def test_targets_cover_three_systems_on_two_architectures() -> None:
    assert TARGETS == [
        ("linux", "amd64"),
        ("linux", "arm64"),
        ("darwin", "amd64"),
        ("darwin", "arm64"),
        ("windows", "amd64"),
        ("windows", "arm64"),
    ]


@pytest.mark.parametrize(("goos", "goarch", "binary", "archive"), CLI_NAMES)
def test_cli_names(goos: str, goarch: str, binary: str, archive: str) -> None:
    assert binary_name("cli", "0.1.0", goos) == binary
    assert archive_name("cli", "0.1.0", goos, goarch) == archive


@pytest.mark.parametrize(("goos", "goarch", "binary", "archive"), TERRAFORM_NAMES)
def test_terraform_names(goos: str, goarch: str, binary: str, archive: str) -> None:
    assert binary_name("terraform", "0.1.0", goos) == binary
    assert archive_name("terraform", "0.1.0", goos, goarch) == archive


def test_names_keep_the_release_candidate_suffix() -> None:
    assert archive_name("cli", "1.2.3-rc.4", "linux", "amd64") == (
        "megooflags_1.2.3-rc.4_linux_amd64.tar.gz"
    )
    assert binary_name("terraform", "1.2.3-rc.4", "linux") == f"{TF}_v1.2.3-rc.4"


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="kind"):
        archive_name("sdk", "0.1.0", "linux", "amd64")


def test_sha256sums_is_sorted_by_name_in_sha256sum_format(tmp_path: Path) -> None:
    b = tmp_path / "b.zip"
    a = tmp_path / "a.tar.gz"
    b.write_bytes(b"bee")
    a.write_bytes(b"ay")
    assert sha256sums([b, a]) == (
        f"{hashlib.sha256(b'ay').hexdigest()}  a.tar.gz\n"
        f"{hashlib.sha256(b'bee').hexdigest()}  b.zip\n"
    )


def test_build_cli_runs_go_build_per_target(repo: Path, tmp_path: Path) -> None:
    go = FakeGo()
    build("cli", "0.1.0", tmp_path / "dist", run=go, root=repo)

    assert len(go.calls) == 6
    for (cmd, cwd, env), (goos, goarch) in zip(go.calls, TARGETS, strict=True):
        assert cwd == repo / "cli"
        assert (env["CGO_ENABLED"], env["GOOS"], env["GOARCH"]) == ("0", goos, goarch)
        out = cmd[cmd.index("-o") + 1]
        assert cmd == [
            "go",
            "build",
            "-trimpath",
            "-ldflags",
            "-s -w",
            "-o",
            out,
            "./cmd/megooflags",
        ]
        assert Path(out).name == binary_name("cli", "0.1.0", goos)


def test_build_cli_archives_hold_the_binary_readme_and_license(repo: Path, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    build("cli", "0.1.0", dist, run=FakeGo(), root=repo)

    with tarfile.open(dist / "megooflags_0.1.0_linux_arm64.tar.gz") as tar:
        assert sorted(tar.getnames()) == ["LICENSE", "README.md", "megooflags"]
        member = tar.getmember("megooflags")
        assert member.mode & 0o111
        binary = tar.extractfile(member)
        assert binary is not None
        assert binary.read() == b"binary linux/arm64"
    with zipfile.ZipFile(dist / "megooflags_0.1.0_windows_amd64.zip") as zf:
        assert sorted(zf.namelist()) == ["LICENSE", "README.md", "megooflags.exe"]
        assert zf.read("megooflags.exe") == b"binary windows/amd64"
        assert zf.read("README.md") == b"# CLI\n"


def test_build_terraform_zips_only_the_provider_binary(repo: Path, tmp_path: Path) -> None:
    go = FakeGo()
    dist = tmp_path / "dist"
    build("terraform", "0.2.0-rc.1", dist, run=go, root=repo)

    assert {cwd for _, cwd, _ in go.calls} == {repo / "terraform"}
    assert {cmd[-1] for cmd, _, _ in go.calls} == {"."}
    with zipfile.ZipFile(dist / f"{TF}_0.2.0-rc.1_darwin_arm64.zip") as zf:
        assert zf.namelist() == [f"{TF}_v0.2.0-rc.1"]
        info = zf.getinfo(f"{TF}_v0.2.0-rc.1")
        assert (info.external_attr >> 16) & 0o111
        assert zf.read(info) == b"binary darwin/arm64"


def test_build_writes_sha256sums_matching_the_archives(repo: Path, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    build("cli", "0.1.0", dist, run=FakeGo(), root=repo)

    archives = sorted(p.name for p in dist.iterdir() if p.name != "SHA256SUMS")
    assert archives == sorted(archive_name("cli", "0.1.0", o, a) for o, a in TARGETS)
    expected = "".join(
        f"{hashlib.sha256((dist / name).read_bytes()).hexdigest()}  {name}\n" for name in archives
    )
    assert (dist / "SHA256SUMS").read_text(encoding="utf-8") == expected


def test_build_fails_when_a_packaged_file_is_missing(repo: Path, tmp_path: Path) -> None:
    (repo / "cli" / "README.md").unlink()
    with pytest.raises(FileNotFoundError, match="README.md"):
        build("cli", "0.1.0", tmp_path / "dist", run=FakeGo(), root=repo)


@pytest.mark.parametrize("version", ["v0.1.0", "0.1", "0.1.0-beta.1", "01.0.0", "0.1.0-rc.0"])
def test_main_rejects_bad_versions(version: str, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--kind", "cli", "--version", version, "--out", str(tmp_path / "dist")])
    assert exc.value.code != 0
    assert not (tmp_path / "dist").exists()
