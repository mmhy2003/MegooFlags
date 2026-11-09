#!/usr/bin/env python3
"""Compute release metadata (image tags, pre-release flag, notes) from a vX.Y.Z[-rc.N] tag."""

import argparse
import sys
from pathlib import Path

from check_version import DEFAULT_ROOT, Tag, parse_tag


def image_tags(tag: Tag) -> list[str]:
    if tag.rc is not None:
        return [tag.image_version]
    tags = [tag.version, f"{tag.major}.{tag.minor}"]
    if tag.major >= 1:
        tags.append(str(tag.major))
    tags.append("latest")
    return tags


def is_prerelease(tag: Tag) -> bool:
    return tag.major == 0 or tag.rc is not None


def changelog_section(text: str, heading: str) -> str | None:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(f"## [{heading}]")), None)
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## ["):
            break
        body.append(line)
    return "\n".join(body).strip() or None


def release_notes(changelog: str, tag: Tag) -> str:
    notes = changelog_section(changelog, tag.version)
    if notes is None and tag.rc is not None:
        notes = changelog_section(changelog, "Unreleased")
    if notes is None:
        fallback = " or Unreleased" if tag.rc is not None else ""
        raise ValueError(f"CHANGELOG.md has no section for {tag.version}{fallback}")
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="release tag, e.g. v0.1.0 or v0.1.0-rc.1")
    parser.add_argument(
        "--changelog", type=Path, default=DEFAULT_ROOT / "CHANGELOG.md", help="CHANGELOG path"
    )
    parser.add_argument("--notes-out", type=Path, help="write the release notes to this file")
    args = parser.parse_args(argv)

    try:
        tag = parse_tag(args.tag)
        notes = release_notes(args.changelog.read_text(encoding="utf-8"), tag)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.notes_out is not None:
        args.notes_out.write_text(f"{notes}\n", encoding="utf-8")

    print(f"version={tag.version}")
    print(f"image_version={tag.image_version}")
    print(f"tags={','.join(image_tags(tag))}")
    print(f"prerelease={'true' if is_prerelease(tag) else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
