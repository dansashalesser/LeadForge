#!/usr/bin/env python3
"""fix-inert-write-rules.py — retire permission rules Claude Code silently ignores.

Claude Code matches file-permission rules on `Edit(path)` only. `Write(path)` is
NOT consulted, and `Edit(path)` already covers every file-editing tool (Write,
Edit, MultiEdit). A `Write(...)` entry therefore grants or denies nothing. Claude
Code says so itself at startup, once per rule:

    Permission allow rule (.claude/settings.json): Write(docs/**) is not matched
    by file permission checks — only Edit(path) rules are. Use Edit(docs/**)
    instead (Edit rules cover all file-editing tools).

Measured 2026-09-01 across the registered fleet: 12 inert rules in 4 of 5 repos,
9 of them in sdd-harness itself — so writing a NEW file under `hooks/**` or
`scripts/**` prompted for permission despite an allow rule that looked correct.

Two different repairs, because allow and deny fail in opposite directions:

  allow  Write(X) with an Edit(X) twin present  -> DROP the Write rule.
         Pure deduplication; the capability already comes from the twin.
  allow  Write(X) with no twin                  -> RENAME to Edit(X).
         Dropping it would remove a capability that was intended.
  deny   Write(X)                               -> always RENAME to Edit(X).
         Never drop a deny. An inert deny is a protection the user believes they
         have and does not; deleting it silently confirms the gap instead of
         closing it.

Dry run by default — it edits settings for every registered repo, so the
destructive form is opt-in.

Usage:
    python3 scripts/setup/fix-inert-write-rules.py                # report only
    python3 scripts/setup/fix-inert-write-rules.py --apply        # fleet-wide
    python3 scripts/setup/fix-inert-write-rules.py --file PATH --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

WRITE_PREFIX = "Write("
EDIT_PREFIX = "Edit("


def find_harness_root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "projects.txt").is_file() and (p / "install.sh").is_file():
            return p
    raise RuntimeError(f"cannot locate harness root from {__file__}")


def to_edit(rule: str) -> str:
    """Write(X) -> Edit(X). Rules are exact strings, so this is a slice, not a match."""
    return EDIT_PREFIX + rule[len(WRITE_PREFIX):]


def plan_list(rules, *, is_deny: bool):
    """Return (new_rules, actions) for one allow/deny list."""
    existing = set(rules)
    out, actions = [], []
    for rule in rules:
        if not rule.startswith(WRITE_PREFIX):
            out.append(rule)
            continue
        twin = to_edit(rule)
        if is_deny:
            if twin in existing:
                actions.append(("drop-deny-dup", rule, twin))
            else:
                out.append(twin)
                actions.append(("rename", rule, twin))
        elif twin in existing:
            actions.append(("drop", rule, twin))
        else:
            out.append(twin)
            actions.append(("rename", rule, twin))
    return out, actions


def plan_file(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [], f"unreadable: {exc}"

    perms = data.get("permissions")
    if not isinstance(perms, dict):
        return data, [], None

    all_actions = []
    for key, is_deny in (("allow", False), ("deny", True)):
        rules = perms.get(key)
        if not isinstance(rules, list):
            continue
        new_rules, actions = plan_list(rules, is_deny=is_deny)
        if actions:
            perms[key] = new_rules
            all_actions += [(key, *a) for a in actions]
    return data, all_actions, None


def settings_files(harness: Path, single: str | None):
    if single:
        return [Path(single).expanduser()]
    out = []
    projects = harness / "projects.txt"
    if projects.is_file():
        for line in projects.read_text(encoding="utf-8").splitlines():
            repo = line.strip()
            if not repo or repo.startswith("#"):
                continue
            for name in ("settings.json", "settings.local.json"):
                f = Path(repo) / ".claude" / name
                if f.is_file():
                    out.append(f)
    for tmpl in ("settings.json.template", "settings.harness.json.template"):
        f = harness / "templates" / tmpl
        if f.is_file():
            out.append(f)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default: report only)")
    ap.add_argument("--file", help="operate on one settings file instead of the fleet")
    args = ap.parse_args()

    harness = find_harness_root()
    targets = settings_files(harness, args.file)
    if not targets:
        print("no settings files found")
        return 1

    total = 0
    changed_files = 0
    for path in targets:
        data, actions, err = plan_file(path)
        if err:
            print(f"  !  {path}: {err}")
            continue
        if not actions:
            continue

        changed_files += 1
        total += len(actions)
        try:
            label = path.relative_to(harness)
        except ValueError:
            label = path
        print(f"\n{label}")
        for scope, kind, rule, twin in actions:
            if kind == "drop":
                print(f"   drop   {scope}: {rule}  (inert; {twin} already grants this)")
            elif kind == "drop-deny-dup":
                print(f"   drop   {scope}: {rule}  (inert; {twin} already denies this)")
            else:
                print(f"   rename {scope}: {rule}  ->  {twin}")

        if args.apply:
            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            print(f"   written (backup: {backup.name})")

    if total == 0:
        print("No inert Write() rules found — nothing to do.")
        return 0

    print(f"\n{total} inert rule(s) across {changed_files} file(s).")
    if not args.apply:
        print("Dry run. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
