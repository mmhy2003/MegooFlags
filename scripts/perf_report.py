#!/usr/bin/env python3
"""Load test results (scripts/perf.sh): merge one run's files into perf/results/<version>.json.

`merge --dir <run> --version V --profile P --out FILE` reads from the run directory:

- `host.json`: the machine (`started_at` moves to the top of the document);
- `meta.json`: per scenario, its `status` (full, reduced or skipped), `reduced_to`, `limits` and
  `notes`, as perf.sh decided them;
- `<scenario>.json`: what mfload or local_eval.sh wrote, `{"metrics": {...}, "errors": n}`.

A scenario in `meta.json` without its file is an error, unless it was skipped.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCENARIOS = (
    *(f"local_eval_{sdk}" for sdk in ("go", "node", "python", "java", "dotnet", "ruby", "php")),
    "remote_ofrep",
    "remote_evaluate",
    "propagation_replicas",
    "propagation_cluster",
    "flags_10k",
    "streams_100k",
    "footprint_single",
)
STATUSES = ("full", "reduced", "skipped")


class MergeError(Exception):
    pass


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MergeError(f"{path.name} is missing from {path.parent}") from None
    except json.JSONDecodeError as exc:
        raise MergeError(f"{path} is not JSON: {exc}") from None


def _scenario(run: Path, name: str, meta: dict[str, Any]) -> dict[str, Any]:
    if name not in SCENARIOS:
        raise MergeError(f"unknown scenario {name!r} in meta.json")
    status = meta.get("status")
    if status not in STATUSES:
        raise MergeError(f"scenario {name!r} has status {status!r}, expected one of {STATUSES}")
    metrics: dict[str, Any] = {}
    if status != "skipped":
        data = _read(run / f"{name}.json")
        metrics = dict(data.get("metrics") or {})
        metrics.setdefault("errors", data.get("errors", 0))
    return {
        "status": status,
        "reduced_to": meta.get("reduced_to"),
        "limits": meta.get("limits") or {},
        "metrics": metrics,
        "notes": meta.get("notes") or "",
    }


def merge(run: Path, *, version: str, profile: str) -> dict[str, Any]:
    host = dict(_read(run / "host.json"))
    started_at = host.pop("started_at", None)
    meta = _read(run / "meta.json")
    return {
        "version": version,
        "profile": profile,
        "started_at": started_at,
        "host": host,
        "scenarios": {name: _scenario(run, name, m) for name, m in meta.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    m = commands.add_parser("merge", help="merge a run directory into one results file")
    m.add_argument("--dir", type=Path, required=True)
    m.add_argument("--version", required=True)
    m.add_argument("--profile", required=True, choices=("full", "tiny"))
    m.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        doc = merge(args.dir, version=args.version, profile=args.profile)
    except MergeError as exc:
        print(f"perf_report merge: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
