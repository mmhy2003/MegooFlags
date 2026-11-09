#!/usr/bin/env python3
"""Check that VERSION, api/pyproject.toml and web/package.json agree, and optionally a tag."""

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc\.([1-9]\d*))?$")
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

MISSING = "<missing>"
DEFAULT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Tag:
    major: int
    minor: int
    patch: int
    rc: int | None

    @property
    def version(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def image_version(self) -> str:
        return self.version if self.rc is None else f"{self.version}-rc.{self.rc}"


def parse_tag(tag: str) -> Tag:
    match = TAG_RE.match(tag)
    if match is None:
        raise ValueError(f"invalid tag {tag!r}: expected vX.Y.Z or vX.Y.Z-rc.N")
    major, minor, patch, rc = match.groups()
    return Tag(int(major), int(minor), int(patch), None if rc is None else int(rc))


def read_versions(root: Path) -> dict[str, str]:
    versions: dict[str, str] = {}

    version_file = root / "VERSION"
    versions["VERSION"] = (
        version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else MISSING
    )

    pyproject = root / "api" / "pyproject.toml"
    if pyproject.is_file():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        versions["api/pyproject.toml"] = str(data.get("project", {}).get("version", MISSING))
    else:
        versions["api/pyproject.toml"] = MISSING

    package_json = root / "web" / "package.json"
    if package_json.is_file():
        data = json.loads(package_json.read_text(encoding="utf-8"))
        versions["web/package.json"] = str(data.get("version", MISSING))
    else:
        versions["web/package.json"] = MISSING

    return versions


def check(root: Path, tag: str | None = None) -> list[str]:
    versions = read_versions(root)
    version = versions.pop("VERSION")
    errors: list[str] = []

    if not VERSION_RE.match(version):
        errors.append(f"VERSION has {version}, expected X.Y.Z")

    for path, found in versions.items():
        if found != version:
            errors.append(f"{path} has {found}, VERSION has {version}")

    if tag is not None:
        try:
            parsed = parse_tag(tag)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if parsed.version != version:
                errors.append(f"tag {tag} does not match VERSION {version}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="repository root")
    parser.add_argument("--tag", help="release tag to check against VERSION, e.g. v0.1.0")
    args = parser.parse_args(argv)

    errors = check(args.root, args.tag)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    version = read_versions(args.root)["VERSION"]
    suffix = f" (tag {args.tag})" if args.tag else ""
    print(f"version OK: {version}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
