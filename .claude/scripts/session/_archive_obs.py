#!/usr/bin/env python3
"""Archive whole dates out of observations.md into glacier/.

Selection is BY DATE, never by count. This is deliberate and is the whole point
of the script: the keep-most-recent-N rewrite reads the file, decides what to
keep, and rewrites it whole, so any entry appended between the read and the
write is destroyed. That race destroyed an entry on 2026-09-01. Archiving a
fixed set of PAST dates commutes with a concurrent append — a new entry is
dated today, today is refused, so a same-day append can never be in the
selected set even if it lands mid-run.

Usage:
    _archive_obs.py --dates 2026-09-07 [2026-09-09 ...] [--repo PATH] [--dry-run]
"""

import argparse
import os
import re
import shutil
import sys
from datetime import date

ENTRY_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+", required=True)
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = date.today().isoformat()
    if today in args.dates:
        print(f"refusing to archive today ({today}) — same-day entries are "
              f"still being appended", file=sys.stderr)
        return 2

    selected = set(args.dates)
    mem = os.path.join(args.repo, ".claude", "memory")
    obs_path = os.path.join(mem, "observations.md")
    glacier_dir = os.path.join(mem, "glacier")
    os.makedirs(glacier_dir, exist_ok=True)
    out_path = os.path.join(glacier_dir, f"observations-{today}.md")

    with open(obs_path, encoding="utf-8") as fh:
        lines = fh.readlines()

    keep, archive = [], []
    for line in lines:
        m = ENTRY_RE.match(line)
        if m and m.group(1) in selected:
            archive.append(line)
        else:
            keep.append(line)

    total_entries = sum(1 for ln in lines if ENTRY_RE.match(ln))
    kept_entries = sum(1 for ln in keep if ENTRY_RE.match(ln))

    if not archive:
        print("nothing matched — no dates in that set are present")
        return 1

    if len(archive) + kept_entries != total_entries:
        print("integrity check failed before write — refusing", file=sys.stderr)
        return 3

    print(f"{len(archive)} entries selected, {kept_entries} remain "
          f"(of {total_entries})")
    if args.dry_run:
        return 0

    shutil.copy2(obs_path, obs_path + ".bak")

    dates_sorted = sorted(selected)
    header = (
        f"<!-- Archived {today} from observations.md. "
        f"{len(archive)} entries, dates {dates_sorted[0]}..{dates_sorted[-1]}. "
        f"Selected by whole date, not by count. -->\n\n"
        f"# Observations archive ({dates_sorted[0]}..{dates_sorted[-1]})\n\n"
    )
    mode = "a" if os.path.exists(out_path) else "w"
    with open(out_path, mode, encoding="utf-8") as fh:
        if mode == "w":
            fh.write(header)
        fh.writelines(archive)

    with open(obs_path, "w", encoding="utf-8") as fh:
        fh.writelines(keep)

    # verify after write, not before
    with open(obs_path, encoding="utf-8") as fh:
        after = sum(1 for ln in fh if ENTRY_RE.match(ln))
    with open(out_path, encoding="utf-8") as fh:
        arch = sum(1 for ln in fh if ENTRY_RE.match(ln))
    print(f"verified: {after} live, {arch} in {os.path.basename(out_path)}, "
          f"backup at {os.path.basename(obs_path)}.bak")
    if after != kept_entries:
        print("POST-WRITE MISMATCH — restore from .bak", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
