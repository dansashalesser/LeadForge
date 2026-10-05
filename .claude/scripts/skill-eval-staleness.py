#!/usr/bin/env python3
"""skill-eval-staleness.py — find skills whose PASS verdict was measured on a
model that is no longer the one running.

`skill-eval-gate` measures a skill's with-vs-without pass-rate lift once, at
authoring time, and writes `eval-verdict.json` beside the `SKILL.md`. Nothing
ever re-measures it. That is fine while the model holds still, and wrong the
moment it does not: skills written for an older Claude are documented to break
on a newer one, and Anthropic's own guidance for Fable 5 is to simplify or
delete existing skills rather than trust them across the boundary (dbreunig,
"What We Can Learn from Claude's Fable 5.1 System Prompt", 2026-09-07 —
docs/sources/articles/README.md).

`skill-validate-hook.sh` already catches the *other* invalidation — a SKILL.md
edited after it was measured — but it only fires on a write. A model change
invalidates every verdict at once, with no write to hang a check on, so it needs
a scan. This is that scan.

Findings, worst first:

    stale-model    measured on a different model than the one now running
    unknown-model  verdict predates the measured_on_model field, or recorded
                   "unknown" — cannot be shown to still hold
    hash-mismatch  SKILL.md changed since it was measured (the hook's check,
                   re-run here to catch edits that bypassed it — an update.sh
                   copy, a manual edit, a sync from another machine)

A skill with no `eval-verdict.json` at all is counted but never flagged: most
installed skills are vendored and never went through the gate, so treating
absence as a finding would bury the real ones.

Usage:
    python3 scripts/skill-eval-staleness.py --current-model claude-opus-5
    python3 scripts/skill-eval-staleness.py --current-model "$SDD_CURRENT_MODEL" --json
    python3 scripts/skill-eval-staleness.py --current-model claude-opus-5 --strict
    python3 scripts/skill-eval-staleness.py --current-model X --dir ./skills

The current model is not discoverable from inside a shell — pass it explicitly,
or set SDD_CURRENT_MODEL. There is deliberately no default: guessing it would
turn every verdict green on a wrong guess, which is the exact failure this
script exists to prevent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

DEFAULT_SKILL_DIRS = [
    Path.home() / ".claude" / "skills",
]

VERDICT_FILE = "eval-verdict.json"

# Worst first — a skill can only carry one finding, and this is the order.
FINDING_ORDER = ["hash-mismatch", "stale-model", "unknown-model"]

UNKNOWN_VALUES = {"", "unknown", "unspecified", "none", "null"}


def sha256_of(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def read_verdict(path: Path) -> dict | None:
    """Parse eval-verdict.json, or None if it is absent or not readable JSON."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def classify(verdict: dict, skill_md: Path, current_model: str) -> str | None:
    """Return the finding for one skill, or None when the verdict still holds.

    Order matters. A skill whose SKILL.md changed has no valid verdict at all,
    so the model it was measured on is beside the point — report the mismatch
    and stop, rather than reporting both and implying two separate problems.
    """
    recorded_hash = str(verdict.get("skill_md_sha256", "")).strip().lower()
    actual_hash = sha256_of(skill_md)
    if recorded_hash and actual_hash and recorded_hash != actual_hash:
        return "hash-mismatch"

    measured_on = str(verdict.get("measured_on_model", "")).strip()
    if measured_on.lower() in UNKNOWN_VALUES:
        return "unknown-model"
    if measured_on != current_model:
        return "stale-model"
    return None


def collect(skill_dirs: list[Path], current_model: str) -> tuple[list[dict], int]:
    """One record per skill carrying a verdict, plus a count of skills without one."""
    records: list[dict] = []
    ungated = 0
    seen: set[str] = set()

    for root in skill_dirs:
        if not root.is_dir():
            continue
        for skill_md in sorted(root.glob("*/SKILL.md")):
            slug = skill_md.parent.name
            if slug in seen:
                continue
            seen.add(slug)

            verdict = read_verdict(skill_md.parent / VERDICT_FILE)
            if verdict is None:
                ungated += 1
                continue

            records.append({
                "skill": slug,
                "path": str(skill_md),
                "verdict": verdict.get("verdict", ""),
                "date": verdict.get("date", ""),
                "measured_on_model": verdict.get("measured_on_model", ""),
                "finding": classify(verdict, skill_md, current_model),
            })
    return records, ungated


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--current-model", default=os.environ.get("SDD_CURRENT_MODEL", ""),
                    help="Exact model ID now running (or set SDD_CURRENT_MODEL). Required.")
    ap.add_argument("--dir", action="append", type=Path,
                    help="Skill root to scan (repeatable). Default: ~/.claude/skills")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of a report")
    ap.add_argument("--strict", action="store_true",
                    help="Exit 1 when any skill carries a finding")
    args = ap.parse_args()

    current_model = args.current_model.strip()
    if not current_model:
        print("error: --current-model is required (or set SDD_CURRENT_MODEL).",
              file=sys.stderr)
        print("       There is no default — a wrong guess would mark every stale",
              file=sys.stderr)
        print("       verdict as current, which is the failure this script prevents.",
              file=sys.stderr)
        return 2

    dirs = [Path(d).expanduser() for d in (args.dir or DEFAULT_SKILL_DIRS)]
    records, ungated = collect(dirs, current_model)

    if not records and not ungated:
        print(f"No skills found under: {', '.join(str(d) for d in dirs)}", file=sys.stderr)
        return 2

    flagged = [r for r in records if r["finding"]]
    by_finding = {f: [r for r in flagged if r["finding"] == f] for f in FINDING_ORDER}

    if args.json:
        print(json.dumps({
            "current_model": current_model,
            "skills_with_verdict": len(records),
            "skills_without_verdict": ungated,
            "current": len(records) - len(flagged),
            "flagged": len(flagged),
            "findings": {f: [r["skill"] for r in by_finding[f]] for f in FINDING_ORDER},
            "detail": flagged,
        }, indent=2))
    else:
        print("## Skill Eval Staleness")
        print()
        print(f"Current model:        {current_model}")
        print(f"Skills with verdict:  {len(records)}")
        print(f"Skills never gated:   {ungated} (not a finding — most are vendored)")
        print(f"Verdicts still valid: {len(records) - len(flagged)}")
        print()
        if not records:
            # Vacuous truth reads exactly like a clean bill of health, so say
            # which one this is. Nothing has been checked, not everything passed.
            print("No skill carries an eval-verdict.json, so there is nothing to")
            print("check. This is not a pass — it is the absence of any measured")
            print("verdict at all. The first skill through skill-eval-gate will")
            print("start populating this.")
        elif not flagged:
            print("✓ Every recorded verdict was measured on the current model and")
            print("  still matches its SKILL.md.")
        else:
            print("| Skill | Finding | Measured on | Dated |")
            print("|-------|---------|-------------|-------|")
            for f in FINDING_ORDER:
                for r in by_finding[f]:
                    shown = r["measured_on_model"] or "—"
                    print(f"| {r['skill']} | {f} | {shown} | {r['date'] or '—'} |")
            print()
            print(f"{len(flagged)} verdict(s) no longer describe what they claim to.")
            print("Re-run skill-eval-gate on each before trusting its lift. A stale")
            print("verdict is not evidence the skill got worse — it is the absence")
            print("of evidence that it still helps.")

    if args.strict and flagged:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
