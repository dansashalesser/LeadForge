#!/usr/bin/env python3
"""rtk-net-effect.py — rerun rate and recovery-path usage from transcripts.

RTK (and every shell-compression layer) reports local savings only: bytes
removed from one command's output. It cannot see whether the agent, missing
detail it needed, re-issued the same command or re-read the same file later
in the same session — the cost that would make a "saved" number net-negative
globally. This script measures that recovery-path signal directly from
`~/.claude/projects/**/*.jsonl` transcripts. It does not attribute cause;
see the "honest caveat" in its JSON/text output.

Two proxies, both same-session, both exact-match (no fuzzy/semantic
matching — a genuinely different command or a deliberate re-read after an
edit both count as "not a rerun"):

1. **Bash rerun rate** — a Bash command, normalized (whitespace-collapsed),
   re-issued verbatim later in the same session.
2. **Read reread rate** — a file path passed to Read, re-read later in the
   same session.

Both are noisy proxies for recovery cost, not proof of it — a high rate is
evidence, not causation, that compression cost detail the agent had to go
get back. Read `scripts/utils/token-forensics.py`'s docstring for the same
disclaimer pattern applied to a different signal.

Usage:
    python3 scripts/utils/rtk-net-effect.py               # last 30 days
    python3 scripts/utils/rtk-net-effect.py --days 7
    python3 scripts/utils/rtk-net-effect.py --project sdd-harness --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


def normalize_command(cmd: str) -> str:
    """Whitespace-collapse only — no regex, no semantic normalization."""
    return " ".join(cmd.split())


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


class SessionStats:
    """Recovery-path counters for one transcript (= one session)."""

    def __init__(self) -> None:
        self.bash_total = 0
        self.bash_reruns = 0
        self.read_total = 0
        self.read_rereads = 0
        self._seen_commands: set[str] = set()
        self._seen_paths: set[str] = set()
        self.touched = False

    def observe_bash(self, command: str) -> None:
        norm = normalize_command(command)
        if not norm:
            return
        self.touched = True
        self.bash_total += 1
        if norm in self._seen_commands:
            self.bash_reruns += 1
        else:
            self._seen_commands.add(norm)

    def observe_read(self, path: str) -> None:
        if not path:
            return
        self.touched = True
        self.read_total += 1
        if path in self._seen_paths:
            self.read_rereads += 1
        else:
            self._seen_paths.add(path)


def analyze_file(path: Path, cutoff) -> SessionStats | None:
    stats = SessionStats()
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if cutoff is not None:
            ts = parse_ts(entry.get("timestamp"))
            if ts is not None and ts < cutoff:
                continue

        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                continue
            if name == "Bash":
                stats.observe_bash(str(tool_input.get("command", "")))
            elif name == "Read":
                file_path = tool_input.get("file_path") or tool_input.get("path") or ""
                stats.observe_read(str(file_path))

    return stats if stats.touched else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="lookback window (default 30, 0 = all)")
    ap.add_argument("--project", help="only project dirs whose name contains this substring")
    ap.add_argument("--limit", type=int, default=0, help="cap files scanned (newest first)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not CLAUDE_PROJECTS.is_dir():
        print(f"no transcripts at {CLAUDE_PROJECTS}", file=sys.stderr)
        return 2

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)) if args.days else None
    files = [p for p in CLAUDE_PROJECTS.glob("*/*.jsonl") if not args.project or args.project in p.parent.name]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if args.limit:
        files = files[: args.limit]

    sessions = 0
    bash_total = bash_reruns = 0
    read_total = read_rereads = 0
    for path in files:
        stats = analyze_file(path, cutoff)
        if stats is None:
            continue
        sessions += 1
        bash_total += stats.bash_total
        bash_reruns += stats.bash_reruns
        read_total += stats.read_total
        read_rereads += stats.read_rereads

    if sessions == 0:
        print("No Bash/Read tool calls found in window.", file=sys.stderr)
        return 2

    bash_rerun_rate = (bash_reruns / bash_total * 100) if bash_total else 0.0
    read_reread_rate = (read_rereads / read_total * 100) if read_total else 0.0

    result = {
        "days": args.days,
        "sessions": sessions,
        "bash_total": bash_total,
        "bash_reruns": bash_reruns,
        "bash_rerun_rate_pct": round(bash_rerun_rate, 1),
        "read_total": read_total,
        "read_rereads": read_rereads,
        "read_reread_rate_pct": round(read_reread_rate, 1),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "caveat": "rerun/reread rate is evidence, not proof, of net-negative compression — "
        "a genuinely new task can also re-issue an old command or re-read an old file",
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"RTK net-effect — last {args.days}d ({sessions} sessions, {len(files)} files scanned)")
    print(f"  Bash commands: {bash_total:,} total, {bash_reruns:,} exact reruns ({bash_rerun_rate:.1f}%)")
    print(f"  Read calls:    {read_total:,} total, {read_rereads:,} rereads ({read_reread_rate:.1f}%)")
    print()
    print("  Caveat: this measures rerun rate, not causation. A high rate is evidence,")
    print("  not proof, that compression cost detail the agent had to go get back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
