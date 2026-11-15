#!/usr/bin/env python3
"""Check an SDK's manifest version, and optionally that a sdk-<sdk>/vX.Y.Z tag matches it."""

import argparse
import json
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
TAG_VERSION = r"v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))(?:-rc\.[1-9]\d*)?"
POM_NS = "{http://maven.apache.org/POM/4.0.0}"
RUBY_RE = re.compile(r"""^\s*VERSION\s*=\s*"([^"]*)"\s*$""", re.MULTILINE)
PHP_RE = re.compile(r"""\bconst\s+VERSION\s*=\s*'([^']*)'\s*;""")

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


class ManifestError(Exception):
    pass


def from_package_json(text: str) -> str | None:
    value = json.loads(text).get("version")
    return value if isinstance(value, str) else None


def from_pyproject(text: str) -> str | None:
    value = tomllib.loads(text).get("project", {}).get("version")
    return value if isinstance(value, str) else None


def from_pom(text: str) -> str | None:
    # The project's own <version>, a direct child of <project>, not the parent's or a dependency's.
    element = ET.fromstring(text).find(f"{POM_NS}version")
    return element.text.strip() if element is not None and element.text else None


def from_csproj(text: str) -> str | None:
    element = ET.fromstring(text).find("PropertyGroup/Version")
    return element.text.strip() if element is not None and element.text else None


def from_pattern(pattern: re.Pattern[str]) -> Callable[[str], str | None]:
    def read(text: str) -> str | None:
        match = pattern.search(text)
        return match.group(1) if match else None

    return read


MANIFESTS: dict[str, tuple[str, Callable[[str], str | None]]] = {
    "node": ("sdks/node/package.json", from_package_json),
    "python": ("sdks/python/pyproject.toml", from_pyproject),
    "java": ("sdks/java/pom.xml", from_pom),
    "dotnet": ("sdks/dotnet/src/MegooFlags/MegooFlags.csproj", from_csproj),
    "ruby": ("sdks/ruby/lib/megooflags/version.rb", from_pattern(RUBY_RE)),
    "php": ("sdks/php/src/Version.php", from_pattern(PHP_RE)),
}


def read_version(root: Path, sdk: str) -> str:
    manifest, reader = MANIFESTS[sdk]
    path = root / manifest
    if not path.is_file():
        raise ManifestError(f"{manifest} is missing")
    try:
        version = reader(path.read_text(encoding="utf-8"))
    except (ValueError, ET.ParseError) as exc:
        raise ManifestError(f"{manifest} cannot be read: {exc}") from exc
    if version is None:
        raise ManifestError(f"{manifest} has no version")
    return version


def check(root: Path, sdk: str, tag: str | None = None) -> list[str]:
    manifest = MANIFESTS[sdk][0]
    try:
        version = read_version(root, sdk)
    except ManifestError as exc:
        return [str(exc)]

    errors: list[str] = []
    if not VERSION_RE.match(version):
        errors.append(f"{manifest} has {version}, expected X.Y.Z")

    if tag is not None:
        match = re.fullmatch(rf"sdk-{sdk}/{TAG_VERSION}", tag)
        if match is None:
            errors.append(
                f"invalid tag {tag!r}: expected sdk-{sdk}/vX.Y.Z or sdk-{sdk}/vX.Y.Z-rc.N"
            )
        elif match.group(1) != version:
            errors.append(f"tag {tag} does not match {manifest} version {version}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, choices=sorted(MANIFESTS), help="SDK to check")
    parser.add_argument("--tag", help="release tag to check, e.g. sdk-node/v0.1.0")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="repository root")
    args = parser.parse_args(argv)

    errors = check(args.root, args.sdk, args.tag)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    suffix = f" (tag {args.tag})" if args.tag else ""
    print(f"sdk {args.sdk} version OK: {read_version(args.root, args.sdk)}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
