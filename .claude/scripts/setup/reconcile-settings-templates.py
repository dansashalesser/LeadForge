#!/usr/bin/env python3
"""reconcile-settings-templates.py — keep the two settings templates from drifting.

The harness ships two settings templates and they are NOT interchangeable:

    templates/settings.json.template          → every installed project's .claude/settings.json
    templates/settings.harness.json.template  → the harness repo's OWN .claude/settings.json

They had silently diverged. As of 2026-08-30 the harness template was missing 14
hook registrations the project template had, and carried 4 the project template
did not. The practical effect is the worst possible direction: hooks were being
*developed and tested* in the harness repo while not firing there, and firing in
every other repo where nobody was watching them.

The rule this enforces:

    hooks(harness) == hooks(project) + HARNESS_ONLY

Permissions are deliberately excluded. They *should* differ — the harness repo
needs write access to its own source tree (hooks/, scripts/, templates/, agents/,
kiro/) that no target project may have, and it denies `git push*` outright where
projects only deny force-push. This script never reads or writes .permissions.

Usage:
    reconcile-settings-templates.py --check    # report drift, exit 1 if any
    reconcile-settings-templates.py --sync     # rewrite the harness template from the project one
    reconcile-settings-templates.py --check --json

The project template is the source of truth for shared hooks: add a hook there,
run --sync, and the harness picks it up. Adding a hook to the harness template
alone is the drift this exists to catch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[2]
PROJECT_TPL = HARNESS_ROOT / "templates" / "settings.json.template"
HARNESS_TPL = HARNESS_ROOT / "templates" / "settings.harness.json.template"

# Hooks that legitimately run ONLY in the harness repo. Each needs a reason: an
# entry here is an exemption from the drift check, so an unjustified one silently
# recreates the problem this script exists to prevent.
#
# (event, matcher, hook filename, why)
HARNESS_ONLY = [
    (
        "Stop", "", "address-check-hook.sh",
        (
            "Checks responses address the user as 'Husband'. That convention lives"
            " in the harness repo's own CLAUDE.md and is deliberately not in"
            " templates/CLAUDE.md.template, so in any other project this hook"
            " would log a violation of a rule that repo never adopted."
        ),
    ),
]


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"missing: {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"not valid JSON: {path}\n  {exc}")


def hook_name(command: str) -> str:
    """Last path segment of a hook command, for comparison across path styles."""
    trimmed = command.rstrip('"').rstrip()
    return trimmed.rsplit("/", 1)[-1] if "/" in trimmed else trimmed


def flatten(settings: dict) -> set[tuple[str, str, str]]:
    """(event, matcher, hook filename) for every registration."""
    out = set()
    for event, groups in (settings.get("hooks") or {}).items():
        for group in groups:
            matcher = group.get("matcher") or ""
            for hook in group.get("hooks") or []:
                out.add((event, matcher, hook_name(hook.get("command", ""))))
    return out


def add_hook(settings: dict, event: str, matcher: str, filename: str) -> None:
    """Append a hook, reusing the matching group or creating it."""
    entry = {
        "type": "command",
        "command": f'bash "${{CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/{filename}"',
    }
    groups = settings.setdefault("hooks", {}).setdefault(event, [])
    for group in groups:
        if (group.get("matcher") or "") == matcher:
            group.setdefault("hooks", []).append(entry)
            return
    groups.append({"matcher": matcher, "hooks": [entry]})


def expected_harness_hooks(project: dict) -> dict:
    """Project hooks plus the harness-only allowlist."""
    hooks = json.loads(json.dumps(project.get("hooks") or {}))
    shell = {"hooks": hooks}
    for event, matcher, filename, _why in HARNESS_ONLY:
        add_hook(shell, event, matcher, filename)
    return shell["hooks"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="report drift, exit 1 if any")
    ap.add_argument("--sync", action="store_true", help="rewrite the harness template")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    if not (args.check or args.sync):
        ap.error("pass --check or --sync")

    project = load(PROJECT_TPL)
    harness = load(HARNESS_TPL)

    allowed = {(e, m, f) for e, m, f, _ in HARNESS_ONLY}
    proj_hooks = flatten(project)
    harn_hooks = flatten(harness)

    missing = proj_hooks - harn_hooks          # in projects, absent from the harness
    extra = harn_hooks - proj_hooks - allowed  # in the harness, unjustified

    if args.sync:
        harness["hooks"] = expected_harness_hooks(project)
        HARNESS_TPL.write_text(
            json.dumps(harness, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        total = len(flatten(harness))
        print(f"synced: {HARNESS_TPL.name} now carries {total} hook registrations "
              f"({len(HARNESS_ONLY)} harness-only)")
        if extra:
            print("\nNOTE: the following were dropped as unjustified harness-only hooks.")
            print("If any belongs in the harness, add it to HARNESS_ONLY with a reason;")
            print("if it belongs everywhere, add it to the project template and re-sync:")
            for e, m, f in sorted(extra):
                print(f"  - {e:<18} {m or '(all)':<28} {f}")
        return 0

    if args.json:
        print(json.dumps({
            "in_sync": not (missing or extra),
            "missing_from_harness": sorted(list(t) for t in missing),
            "unjustified_in_harness": sorted(list(t) for t in extra),
            "harness_only_allowlist": [
                {"event": e, "matcher": m, "hook": f, "why": w}
                for e, m, f, w in HARNESS_ONLY
            ],
        }, indent=2))
        return 1 if (missing or extra) else 0

    if not missing and not extra:
        print(f"✓ settings templates in sync — {len(proj_hooks)} shared hooks, "
              f"{len(HARNESS_ONLY)} harness-only")
        return 0

    print("🔴 settings templates have drifted\n")
    if missing:
        print("Registered for every project but NOT in the harness repo.")
        print("These fire where nobody watches and do NOT fire where they are developed:")
        for e, m, f in sorted(missing):
            print(f"  - {e:<18} {m or '(all)':<28} {f}")
        print()
    if extra:
        print("In the harness repo only, with no HARNESS_ONLY justification.")
        print("Either promote to the project template or add a reason to HARNESS_ONLY:")
        for e, m, f in sorted(extra):
            print(f"  - {e:<18} {m or '(all)':<28} {f}")
        print()
    print("Fix: add shared hooks to templates/settings.json.template, then run")
    print("     python3 scripts/setup/reconcile-settings-templates.py --sync")
    return 1


if __name__ == "__main__":
    sys.exit(main())
