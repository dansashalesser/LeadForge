#!/usr/bin/env python3
"""Keep Rate with path-class split — companion to _keep_rate_calc.py.

Same pinned recipe as _keep_rate_calc.py (see its docstring), plus the path
classifier the recorded series has used since 2026-09-24:

    lockfile = pnpm-lock.yaml / package-lock.json / yarn.lock
    infra    = dotfiles, build configs, .github/, .claude/, *.md, *.txt
    feature  = remainder

Added lines are classified by the path the commit touched; kept lines by the
HEAD path they currently live in. That asymmetry is inherent to blame-based
survival (a line can move between classes) and matches how the series was
recorded, so the figures stay comparable.

Usage: _keep_rate_classified.py [--days N] [--repo PATH]
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

LOCKFILES = {"pnpm-lock.yaml", "package-lock.json", "yarn.lock"}
INFRA_SUFFIXES = (".md", ".txt", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg")
INFRA_PREFIXES = (".github/", ".claude/", ".vscode/", ".cursor/", "infra/")


def git(repo, *args):
    out = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    return out.stdout


def classify(path):
    base = os.path.basename(path)
    if base in LOCKFILES:
        return "lockfile"
    if base.startswith("."):
        return "infra"
    if path.startswith(INFRA_PREFIXES):
        return "infra"
    if path.endswith(INFRA_SUFFIXES):
        return "infra"
    return "feature"


def is_binary_worktree(repo, path):
    full = os.path.join(repo, path)
    if not os.path.isfile(full):  # isfile, not exists — gitlinks are dirs
        return False
    try:
        with open(full, "rb") as fh:
            chunk = fh.read(8000)
    except OSError:
        return False
    return b"\0" in chunk or chunk.startswith(b"version https://git-lfs")


def claude_commits(repo, cutoff):
    raw = git(
        repo,
        "log",
        "--no-merges",
        "--format=%H%x1f%aI%x1f%s%x1f%b%x1e",
    )
    found = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        parts = rec.split("\x1f")
        if len(parts) < 4:
            continue
        sha, iso, subject, body = parts[0], parts[1], parts[2], parts[3]
        if "co-authored-by: claude" not in body.lower():
            continue
        if datetime.fromisoformat(iso) >= cutoff:
            continue  # inside grace window; not yet scorable
        found.append((sha, iso, subject))
    return found


def build_blame_map(repo, text_paths, pin_mc=True):
    """(commit, class) -> lines currently attributable to it in HEAD."""
    kept = defaultdict(int)
    flags = ["-M", "-C"] if pin_mc else []
    for path in text_paths:
        cls = classify(path)
        out = git(repo, "blame", "--line-porcelain", *flags, "HEAD", "--", path)
        if not out:
            continue
        for line in out.splitlines():
            if len(line) >= 40 and line[:40].isalnum() and " " in line:
                head = line.split(" ", 1)[0]
                if len(head) != 40:
                    continue
                try:
                    int(head, 16)
                except ValueError:
                    continue
                kept[(head, cls)] += 1
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument(
        "--unpinned",
        action="store_true",
        help="drop -M -C from blame (the recorded headline series basis); "
        "figures are NOT comparable across this flag",
    )
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    tracked = [p for p in git(repo, "ls-files").splitlines() if p]
    text_paths = [p for p in tracked if not is_binary_worktree(repo, p)]

    commits = claude_commits(repo, cutoff)
    kept_map = build_blame_map(repo, text_paths, pin_mc=not args.unpinned)

    added = defaultdict(int)
    kept = defaultdict(int)
    for sha, _iso, _subject in commits:
        has_parent = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--verify", "-q", f"{sha}^"],
            capture_output=True,
            check=False,
        ).returncode == 0
        base = f"{sha}^" if has_parent else EMPTY_TREE
        per_cls_added = defaultdict(int)
        numstat = git(repo, "diff", f"{base}..{sha}", "--numstat", "-M", "-C")
        for row in numstat.splitlines():
            cols = row.split("\t")
            if len(cols) < 3:
                continue
            a, _d, path = cols[0], cols[1], cols[2]
            if a == "-":  # binary row
                continue
            path = path.split("=>")[-1].strip().strip("}").strip()
            per_cls_added[classify(path)] += int(a)
        for cls, n in per_cls_added.items():
            added[cls] += n
            kept[cls] += min(kept_map.get((sha, cls), 0), n)

    def pct(k, a):
        return round(100.0 * k / a, 1) if a else None

    total_a = sum(added.values())
    total_k = sum(kept.values())
    ex_lock_a = total_a - added["lockfile"]
    ex_lock_k = total_k - kept["lockfile"]

    print(
        json.dumps(
            {
                "branch": git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip(),
                "head": git(repo, "rev-parse", "--short", "HEAD").strip(),
                "commits": len(commits),
                "cutoff": cutoff.date().isoformat(),
                "text_paths": len(text_paths),
                "by_class": {
                    c: {"added": added[c], "kept": kept[c], "pct": pct(kept[c], added[c])}
                    for c in ("lockfile", "infra", "feature")
                },
                "raw_with_lockfiles": {
                    "added": total_a,
                    "kept": total_k,
                    "pct": pct(total_k, total_a),
                },
                "ex_lockfile": {
                    "added": ex_lock_a,
                    "kept": ex_lock_k,
                    "pct": pct(ex_lock_k, ex_lock_a),
                },
                "basis": ("unpinned blame" if args.unpinned else "-M -C pinned")
                + ", worktree binary test, empty-tree root, "
                "blame map over HEAD text paths keyed by (commit, class), "
                "credit clamped to added per class",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
