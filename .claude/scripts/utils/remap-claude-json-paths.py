#!/usr/bin/env python3
"""Re-key ~/.claude.json project entries after moving a repo on disk.

Claude Code stores per-project state (most importantly hasTrustDialogAccepted, which
gates every permissions.allow rule in .claude/settings.json) under the project's
ABSOLUTE PATH. Move a repo and the entry no longer matches: the workspace silently
becomes untrusted and its permission rules are ignored, with the only symptom a line
in a background log.

MUST BE RUN WITH CLAUDE CODE FULLY QUIT. Nothing holds the file open — Claude Code
reads and writes it on demand — so the hazard is a read-modify-write race: a running
instance rewrites the whole file from its in-memory copy and silently drops an edit
made underneath it. This script refuses to run when it finds a live process, rather
than relying on you to remember.

Usage:
    python3 remap-claude-json-paths.py --from /old/prefix --to /new/prefix [--apply]

Without --apply it prints what would change and touches nothing.
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CONFIG = Path.home() / ".claude.json"


def claude_is_running() -> list[str]:
    """Return one line per distinct live Claude Code executable, empty if none.

    Matches on the exact process NAME (pgrep -x), not the full command line. A -f
    match catches Claude Desktop's helper processes and anything merely mentioning
    "claude-code" in its arguments — none of which touch ~/.claude.json — and would
    make this check refuse forever.

    Note that nothing holds ~/.claude.json open: Claude Code reads and writes it on
    demand. The hazard is a read-modify-write race, where a running instance rewrites
    the file from its in-memory copy and drops an edit made underneath it. That is
    why process presence, not file locking, is the thing worth checking.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-x", "claude"],
            capture_output=True, text=True, check=False,
        ).stdout
    except FileNotFoundError:
        return []

    pids = [line.strip() for line in out.splitlines() if line.strip()]
    if not pids:
        return []

    paths: dict[str, int] = {}
    for pid in pids:
        argv = subprocess.run(
            ["ps", "-p", pid, "-o", "comm="],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        paths[argv or "claude"] = paths.get(argv or "claude", 0) + 1

    return [f"{count:3d} x {path}" for path, count in sorted(paths.items())]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="old", required=True, help="old absolute path prefix")
    ap.add_argument("--to", dest="new", required=True, help="new absolute path prefix")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--force", action="store_true",
                    help="proceed even if Claude Code appears to be running (unsafe)")
    # Access through vars(): argparse's Namespace.__getattr__ is typed such that
    # attribute access trips Pyright's reportArgumentType on every option name.
    opts = vars(ap.parse_args())
    old_prefix: str = opts["old"]
    new_prefix: str = opts["new"]
    do_apply: bool = opts["apply"]
    force: bool = opts["force"]

    if not CONFIG.is_file():
        print(f"no config at {CONFIG}", file=sys.stderr)
        return 1

    if do_apply and not force:
        running = claude_is_running()
        if running:
            print("REFUSING TO WRITE — Claude Code appears to be running:", file=sys.stderr)
            for line in running:
                print(f"    {line}", file=sys.stderr)
            print("", file=sys.stderr)
            print("A running instance rewrites ~/.claude.json on exit and would silently", file=sys.stderr)
            print("revert this edit. Quit every Claude Code window, CLI session and IDE", file=sys.stderr)
            print("extension, then re-run. Override with --force at your own risk.", file=sys.stderr)
            return 2

    data = json.loads(CONFIG.read_text())
    projects = data.get("projects", {})

    remapped = {}
    collisions = []
    for key in list(projects):
        if key.startswith(old_prefix):
            new_key = new_prefix + key[len(old_prefix):]
            if new_key in projects:
                collisions.append((key, new_key))
            else:
                remapped[key] = new_key

    if not remapped and not collisions:
        print(f"nothing to do — no project keys start with {old_prefix}")
        return 0

    for old_key, new_key in remapped.items():
        trusted = projects[old_key].get("hasTrustDialogAccepted")
        print(f"  {old_key}\n    -> {new_key}   (hasTrustDialogAccepted={trusted})")
    for old_key, new_key in collisions:
        print(f"  SKIP {old_key}\n    -> {new_key} already exists; leaving both alone")

    if not do_apply:
        print(f"\ndry run — {len(remapped)} entr(ies) would move. Re-run with --apply.")
        return 0

    backup = CONFIG.with_suffix(f".json.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(CONFIG, backup)

    for old_key, new_key in remapped.items():
        projects[new_key] = projects.pop(old_key)

    # Serialise fully before touching the original, so a failure here cannot leave a
    # truncated config behind.
    payload = json.dumps(data, indent=2)
    CONFIG.write_text(payload)

    json.loads(CONFIG.read_text())  # prove what landed is parseable
    print(f"\napplied — {len(remapped)} entr(ies) moved.")
    print(f"backup: {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
