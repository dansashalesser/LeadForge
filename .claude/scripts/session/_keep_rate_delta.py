#!/usr/bin/env python3
"""Attribute a keep-rate movement to the commits and paths that caused it.

Blames the >7d Claude cohort at two revs and reports which paths lost
attributable lines between them. Answers "the series moved — why?" without
re-running the full calculator at the old rev.

Usage: _keep_rate_delta.py --old <rev> --new <rev> [--days N] [--repo PATH]
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    ).stdout


def cohort(repo, cutoff):
    raw = git(repo, "log", "--no-merges", "--format=%H%x1f%aI%x1f%s%x1f%b%x1e")
    shas = {}
    for rec in raw.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) < 4:
            continue
        sha, iso, subject, body = parts
        if "co-authored-by: claude" not in body.lower():
            continue
        if datetime.fromisoformat(iso) >= cutoff:
            continue
        shas[sha] = subject[:60]
    return shas


def blame_counts(repo, rev, paths, shas):
    """(sha, path) -> attributable lines at rev."""
    counts = defaultdict(int)
    for path in paths:
        out = git(repo, "blame", "--line-porcelain", "-M", "-C", rev, "--", path)
        for line in out.splitlines():
            if len(line) >= 40 and line[:40].isalnum() and " " in line:
                head = line.split(" ", 1)[0]
                if len(head) == 40 and head in shas:
                    counts[(head, path)] += 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--repo", default=os.getcwd())
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    shas = cohort(repo, cutoff)

    # Only paths that differ between the two revs can move the number.
    changed = [
        p
        for p in git(repo, "diff", "--name-only", "-M", "-C", f"{args.old}..{args.new}")
        .splitlines()
        if p
    ]
    old_paths = set(git(repo, "ls-tree", "-r", "--name-only", args.old).splitlines())
    new_paths = set(git(repo, "ls-tree", "-r", "--name-only", args.new).splitlines())

    old = blame_counts(repo, args.old, [p for p in changed if p in old_paths], shas)
    new = blame_counts(repo, args.new, [p for p in changed if p in new_paths], shas)

    deltas = []
    for key in set(old) | set(new):
        d = new.get(key, 0) - old.get(key, 0)
        if d:
            sha, path = key
            deltas.append(
                {
                    "sha": sha[:8],
                    "cohort_subject": shas[sha],
                    "path": path,
                    "old": old.get(key, 0),
                    "new": new.get(key, 0),
                    "delta": d,
                }
            )
    deltas.sort(key=lambda x: x["delta"])

    print(
        json.dumps(
            {
                "old_rev": args.old,
                "new_rev": args.new,
                "cohort_size": len(shas),
                "changed_paths_examined": len(changed),
                "net_delta": sum(d["delta"] for d in deltas),
                "movements": deltas,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
