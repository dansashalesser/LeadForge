#!/usr/bin/env python3
"""Append one measurement to .claude/memory/metrics.jsonl.

Numbers that a program has to read back get written here as JSON, once, by the
thing that measured them. They are never re-extracted from the prose in
observations.md — that path silently produced a wrong AI-adoption figure for
weeks (a pattern match on "24.5%" yielded 5). Prose is for humans; this file is
the machine channel.

Writing the same (date, metric) twice replaces the earlier record rather than
appending a duplicate, so a re-run of daily-maintenance cannot skew an average.

Usage:
    record_metric.py --metric session-quality --value 4 --meta '{"reverts": 0}'
    record_metric.py --metric session-quality --value 3 --idle
    record_metric.py --metric keep-rate --value 86.0 --meta '{"commits": 11}'
    record_metric.py --metric memory-gap --value 2 --meta '{"topics": "caveman"}'

Exit codes: 0 = written; 2 = usage/validation error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

METRICS = {
    "session-quality": "Session score, 0–5. Set --idle for windows with no user session.",
    "keep-rate": "Percent of Claude-authored lines still present in HEAD.",
    "memory-gap": "Count of re-explanation hits in one session.",
    "sloppiness": "Verbosity score (0-1) from sloppiness-score.sh. Put verdict/erosion in --meta.",
}

RANGES = {
    "session-quality": (0.0, 5.0),
    "keep-rate": (0.0, 100.0),
    "memory-gap": (0.0, float("inf")),
    "sloppiness": (0.0, 1.0),
}


def metrics_path(repo: Path) -> Path:
    return repo / ".claude" / "memory" / "metrics.jsonl"


def load_records(path: Path) -> list[dict]:
    """Read existing records. A malformed line is an error, not something to skip."""
    if not path.exists():
        return []
    records = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{lineno} is not valid JSON: {e}") from e
    return records


def write_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records),
        encoding="utf-8",
    )
    tmp.replace(path)


def record(repo: Path, metric: str, value: float, idle: bool = False,
           meta: dict | None = None, day: str | None = None) -> dict:
    """Write one measurement, replacing any earlier record for the same day+metric."""
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}; known: {', '.join(sorted(METRICS))}")
    lo, hi = RANGES[metric]
    if not lo <= value <= hi:
        raise ValueError(f"{metric} value {value} outside {lo}–{hi}")

    record_obj = {
        "date": date.fromisoformat(day).isoformat() if day else date.today().isoformat(),
        "metric": metric,
        "value": value,
        "idle": idle,
        "meta": meta or {},
    }

    path = metrics_path(repo)
    records = load_records(path)
    kept = [
        r for r in records
        if (r.get("date"), r.get("metric")) != (record_obj["date"], record_obj["metric"])
    ]
    kept.append(record_obj)
    kept.sort(key=lambda r: (r.get("date", ""), r.get("metric", "")))
    write_records(path, kept)
    return record_obj


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metric", required=True, choices=sorted(METRICS))
    ap.add_argument("--value", required=True, type=float)
    ap.add_argument(
        "--idle",
        action="store_true",
        help="Mark as an idle-routine window (no user session). Excluded from averages.",
    )
    ap.add_argument("--meta", default="{}", help="JSON object of supporting detail")
    ap.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD")
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    args = ap.parse_args()

    try:
        meta = json.loads(args.meta)
    except json.JSONDecodeError as e:
        print(f"ERROR: --meta is not valid JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(meta, dict):
        print("ERROR: --meta must be a JSON object", file=sys.stderr)
        return 2

    try:
        written = record(args.repo, args.metric, args.value, args.idle, meta, args.date)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print(f"recorded {written['metric']}={written['value']} on {written['date']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
