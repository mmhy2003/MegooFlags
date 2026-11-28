#!/usr/bin/env python3
"""Cross-build the CLI or the Terraform provider into release archives plus SHA256SUMS."""

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-rc\.[1-9]\d*)?$",
)

TARGETS = [
    ("linux", "amd64"),
    ("linux", "arm64"),
    ("darwin", "amd64"),
    ("darwin", "arm64"),
    ("windows", "amd64"),
    ("windows", "arm64"),
]

Runner = Callable[..., object]


@dataclass(frozen=True)
class Kind:
    directory: str
    package: str
    name: str
    # Files from the module directory packed next to the binary.
    extras: tuple[str, ...]


KINDS = {
    "cli": Kind("cli", "./cmd/megooflags", "megooflags", ("README.md", "LICENSE")),
    "terraform": Kind("terraform", ".", "terraform-provider-megooflags", ()),
}


def _kind(kind: str) -> Kind:
    try:
        return KINDS[kind]
    except KeyError:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(KINDS)}") from None


def binary_name(kind: str, version: str, goos: str) -> str:
    k = _kind(kind)
    # Terraform finds providers by the terraform-provider-<name>_v<version> file name.
    base = f"{k.name}_v{version}" if kind == "terraform" else k.name
    return f"{base}.exe" if goos == "windows" else base


def archive_name(kind: str, version: str, goos: str, goarch: str) -> str:
    k = _kind(kind)
    ext = "tar.gz" if kind == "cli" and goos != "windows" else "zip"
    return f"{k.name}_{version}_{goos}_{goarch}.{ext}"


def sha256sums(paths: Iterable[Path]) -> str:
    lines = []
    for path in sorted(paths, key=lambda p: p.name):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}\n")
    return "".join(lines)


def _write_archive(archive: Path, binary: Path, extras: Sequence[Path]) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, mode in [(binary, 0o755), *((p, 0o644) for p in extras)]:
                zinfo = zipfile.ZipInfo.from_file(path, arcname=path.name)
                zinfo.external_attr = (0o100000 | mode) << 16
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zinfo, path.read_bytes())
    else:
        with tarfile.open(archive, "w:gz") as tar:
            for path, mode in [(binary, 0o755), *((p, 0o644) for p in extras)]:
                tinfo = tar.gettarinfo(path, arcname=path.name)
                tinfo.mode = mode
                tinfo.uid = tinfo.gid = 0
                tinfo.uname = tinfo.gname = ""
                with path.open("rb") as f:
                    tar.addfile(tinfo, f)


def build(
    kind: str,
    version: str,
    out: Path,
    run: Runner = subprocess.run,
    root: Path = DEFAULT_ROOT,
) -> list[Path]:
    k = _kind(kind)
    module = root / k.directory
    extras = [module / name for name in k.extras]
    for path in extras:
        if not path.is_file():
            raise FileNotFoundError(f"{path} is packed into the {kind} archives but is missing")

    out.mkdir(parents=True, exist_ok=True)
    archives = []
    for goos, goarch in TARGETS:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / binary_name(kind, version, goos)
            env = {**os.environ, "CGO_ENABLED": "0", "GOOS": goos, "GOARCH": goarch}
            cmd = ["go", "build", "-trimpath", "-ldflags", "-s -w", "-o", str(binary), k.package]
            print(f"building {kind} {version} for {goos}/{goarch}", file=sys.stderr)
            run(cmd, cwd=module, env=env, check=True)
            archive = out / archive_name(kind, version, goos, goarch)
            _write_archive(archive, binary, extras)
            archives.append(archive)

    (out / "SHA256SUMS").write_text(sha256sums(archives), encoding="utf-8")
    return archives


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=sorted(KINDS))
    parser.add_argument("--version", required=True, help="X.Y.Z or X.Y.Z-rc.N, without a leading v")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if not VERSION_RE.match(args.version):
        parser.error(f"--version must be X.Y.Z or X.Y.Z-rc.N, got {args.version!r}")
    for archive in build(args.kind, args.version, args.out):
        print(archive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
