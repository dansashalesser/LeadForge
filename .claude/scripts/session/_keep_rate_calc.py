#!/usr/bin/env python3
"""Keep Rate calculator — pinned recipe from skills/keep-rate/SKILL.md Step 2.

Keep Rate = lines a Claude-co-authored commit added that are STILL attributable
to that commit in HEAD, over all lines it added. A durability signal, not a
correctness one.

Pinned choices (changing any of these makes the figure incomparable with
previously recorded ones — record the basis alongside the value):
  * working branch only, --no-merges  (--all inflates the denominator)
  * blame map built ONCE over every text path in HEAD, keyed by commit, so
    lines that moved to a renamed file still count as kept
  * -M -C on both blame and numstat (rename/copy detection)
  * --line-porcelain for blame (field-count filtering misses continuations)
  * binariness tested on the WORKING-TREE file, not the diff blob
    (git-LFS pointers read as text in a diff and binary in the worktree)
  * empty-tree base for root commits
  * denominator spans EVERY text path a commit touched, including paths later
    deleted or renamed — restricting it to surviving-HEAD paths yields >100%
  * per-commit blame credit clamped to lines added

Usage:
    _keep_rate_calc.py [--days N] [--repo PATH] [--json]
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def git(repo, *args):
    out = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        errors="replace",
    )
    return out.stdout


def is_binary_worktree(repo, path):
    """Test the working-tree file, not the diff blob."""
    full = os.path.join(repo, path)
    if not os.path.isfile(full):  # isfile, not exists — gitlinks are dirs
        return False
    try:
        with open(full, "rb") as fh:
            chunk = fh.read(8000)
    except OSError:
        return True
    if b"\0" in chunk:
        return True
    if chunk.startswith(b"version https://git-lfs"):
        return True
    return False


def claude_commits(repo, cutoff):
    """Claude-co-authored commits on the working branch, older than cutoff."""
    raw = git(
        repo, "log", "--no-merges", "--format=%H%x1f%aI%x1f%s%x1f%b%x1e"
    )
    found = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) < 4:
            continue
        sha, iso, subject, body = parts[0], parts[1], parts[2], parts[3]
        blob = (subject + "\n" + body).lower()
        if "co-authored-by: claude" not in blob and "noreply@anthropic" not in blob:
            continue
        when = datetime.fromisoformat(iso)
        if when >= cutoff:
            continue  # inside the 7-day grace window; not yet scorable
        found.append((sha, iso, subject))
    return found


def build_blame_map(repo, text_paths):
    """commit -> lines currently attributable to it anywhere in HEAD."""
    kept = defaultdict(int)
    for path in text_paths:
        out = git(
            repo, "blame", "--line-porcelain", "-M", "-C", "HEAD", "--", path
        )
        if not out:
            continue
        for line in out.splitlines():
            # A porcelain header line starts each blamed line: "<sha> a b c"
            if len(line) >= 40 and line[:40].isalnum() and " " in line:
                head = line.split(" ", 1)[0]
                if len(head) == 40:
                    try:
                        int(head, 16)
                    except ValueError:
                        continue
                    kept[head] += 1
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="grace window in days")
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    head = git(repo, "rev-parse", "--short", "HEAD").strip()

    tracked = [p for p in git(repo, "ls-files").splitlines() if p]
    text_paths = [p for p in tracked if not is_binary_worktree(repo, p)]
    binary_count = len(tracked) - len(text_paths)

    commits = claude_commits(repo, cutoff)
    kept_map = build_blame_map(repo, text_paths)

    total_added = 0
    total_kept = 0
    touched_instances = 0
    per_commit = []

    for sha, iso, subject in commits:
        has_parent = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--verify", "-q", sha + "^"],
            capture_output=True,
        ).returncode == 0
        base = sha + "^" if has_parent else EMPTY_TREE

        added = 0
        numstat = git(repo, "diff", f"{base}..{sha}", "--numstat", "-M", "-C")
        for row in numstat.splitlines():
            cols = row.split("\t")
            if len(cols) < 3:
                continue
            a, _d, path = cols[0], cols[1], cols[2]
            if a == "-":
                continue  # binary row
            # rename rows read "old => new"; credit the destination path
            if "=>" in path:
                path = path.split("=>")[-1].strip().strip("}").strip()
            if is_binary_worktree(repo, path):
                continue
            touched_instances += 1
            added += int(a)

        k = min(kept_map.get(sha, 0), added)  # clamp credit to lines added
        total_added += added
        total_kept += k
        per_commit.append(
            {
                "sha": sha[:8],
                "date": iso[:10],
                "subject": subject[:60],
                "added": added,
                "kept": k,
                "rate": round(100.0 * k / added, 1) if added else None,
            }
        )

    rate = round(100.0 * total_kept / total_added, 2) if total_added else None

    result = {
        "branch": branch,
        "head": head,
        "commits": len(commits),
        "tracked_paths": len(tracked),
        "text_paths": len(text_paths),
        "binary_paths": binary_count,
        "touched_path_instances": touched_instances,
        "lines_added": total_added,
        "lines_kept": total_kept,
        "keep_rate": rate,
        "cutoff": cutoff.date().isoformat(),
        "basis": "-M -C pinned, worktree binary test, empty-tree root, "
                 "blame map over HEAD text paths, credit clamped to added",
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))
        worst = sorted(
            [c for c in per_commit if c["rate"] is not None],
            key=lambda c: c["rate"],
        )[:6]
        print("\nlowest survivors:")
        for c in worst:
            print(f"  {c['sha']} {c['rate']:5.1f}% ({c['kept']}/{c['added']}) {c['subject']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
