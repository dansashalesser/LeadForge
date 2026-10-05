#!/usr/bin/env python3
"""skill-listing-budget.py — measure the aggregate skill *listing* cost.

Every installed skill contributes its `name` and `description` to the skill
listing that is injected into context at session start. Bodies load only on
invocation, but the listing is paid for on every single session, unconditionally.

`skill-curator` already audits descriptions one at a time (>150 chars ⚠️,
>200 chars 🔴) and prints a total. What it lacks is a *ceiling* for that total,
which makes the aggregate decorative: every skill can pass its individual check
while the sum is still many times over what the listing should cost. The
working guidance is that the listing budget is ~1% of the context window
(source: Addy Osmani, "Audit your agent files", 2026-08-27 —
docs/sources/articles/README.md).

Usage:
    python3 scripts/skill-listing-budget.py
    python3 scripts/skill-listing-budget.py --window 200000
    python3 scripts/skill-listing-budget.py --top 40
    python3 scripts/skill-listing-budget.py --strict     # exit 1 when over budget
    python3 scripts/skill-listing-budget.py --dir ~/.claude/skills --json

Token estimate is chars/4 — the same approximation skill-curator uses. It is a
rough figure and is labelled as such in the output; the ratio to budget is the
number that matters, not the absolute.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

DEFAULT_SKILL_DIRS = [
    Path.home() / ".claude" / "skills",
]

# Windows to report against. The budget line is 1% of each.
WINDOWS = [
    ("200k", 200_000),
    ("1M", 1_000_000),
]

BUDGET_FRACTION = 0.01


def read_frontmatter(path: Path) -> dict:
    """Return top-level YAML frontmatter keys as strings.

    Deliberately a line-structure reader over the fenced frontmatter block, not
    a pattern match over prose: keys are read at column 0, block scalars (`|`,
    `>`) are gathered from the following indented lines. Nested keys are
    ignored — nothing here needs them.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}

    out: dict[str, str] = {}
    i = 1
    while i < end:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or line[:1].isspace():
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            block = []
            i += 1
            while i < end and (not lines[i].strip() or lines[i][:1].isspace()):
                block.append(lines[i].strip())
                i += 1
            value = " ".join(p for p in block if p)
        else:
            i += 1

        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def collect(skill_dirs: list[Path]) -> list[dict]:
    """One record per skill with a readable SKILL.md."""
    skills = []
    seen = set()
    for root in skill_dirs:
        if not root.is_dir():
            continue
        for skill_md in sorted(root.glob("*/SKILL.md")):
            name_from_dir = skill_md.parent.name
            if name_from_dir in seen:
                continue
            seen.add(name_from_dir)
            fm = read_frontmatter(skill_md)
            name = fm.get("name") or name_from_dir
            desc = fm.get("description", "")
            # The listing line is roughly "- <name>: <description>".
            listing_chars = len(name) + len(desc) + 4
            skills.append({
                "name": name,
                "dir": name_from_dir,
                "path": str(skill_md),
                "name_chars": len(name),
                "desc_chars": len(desc),
                "listing_chars": listing_chars,
                "has_description": bool(desc),
                "pinned": fm.get("pinned", "").lower() in ("true", "yes"),
            })
    return skills


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", action="append", type=Path,
                    help="Skill root to scan (repeatable). Default: ~/.claude/skills")
    ap.add_argument("--window", type=int,
                    help="Report against a single context window size instead of both")
    ap.add_argument("--top", type=int, default=20,
                    help="How many worst offenders to list (default 20, 0 to hide)")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of a report")
    ap.add_argument("--strict", action="store_true",
                    help="Exit 1 if the listing is over budget for the smallest window shown")
    args = ap.parse_args()

    dirs = args.dir or DEFAULT_SKILL_DIRS
    skills = collect([Path(d).expanduser() for d in dirs])

    if not skills:
        print(f"No skills found under: {', '.join(str(d) for d in dirs)}", file=sys.stderr)
        return 2

    total_chars = sum(s["listing_chars"] for s in skills)
    total_tokens = math.ceil(total_chars / 4)
    windows = [(f"{args.window}", args.window)] if args.window else WINDOWS

    verdicts = []
    for label, size in windows:
        budget = int(size * BUDGET_FRACTION)
        verdicts.append({
            "window": label,
            "window_tokens": size,
            "budget_tokens": budget,
            "actual_tokens": total_tokens,
            "ratio": round(total_tokens / budget, 2) if budget else None,
            "over": total_tokens > budget,
        })

    no_desc = [s for s in skills if not s["has_description"]]
    worst = sorted(skills, key=lambda s: -s["listing_chars"])[:args.top]

    if args.json:
        print(json.dumps({
            "skills": len(skills),
            "total_listing_chars": total_chars,
            "total_listing_tokens_est": total_tokens,
            "budget_fraction": BUDGET_FRACTION,
            "verdicts": verdicts,
            "missing_description": [s["dir"] for s in no_desc],
            "worst": worst,
        }, indent=2))
    else:
        print("## Skill Listing Budget")
        print()
        print(f"Skills scanned:  {len(skills)}")
        print(f"Listing size:    {total_chars:,} chars  (~{total_tokens:,} tokens est. at chars/4)")
        print(f"Budget:          {int(BUDGET_FRACTION * 100)}% of the context window")
        print()
        print("| Window | Budget (tok) | Actual (tok) | Ratio | Verdict |")
        print("|--------|--------------|--------------|-------|---------|")
        for v in verdicts:
            mark = "🔴 OVER" if v["over"] else "✓ within"
            print(f"| {v['window']} | {v['budget_tokens']:,} | {v['actual_tokens']:,} "
                  f"| {v['ratio']}× | {mark} |")
        print()
        if any(v["over"] for v in verdicts):
            worst_v = max(verdicts, key=lambda v: v["ratio"] or 0)
            excess = worst_v["actual_tokens"] - worst_v["budget_tokens"]
            print(f"Over by ~{excess:,} tokens on the {worst_v['window']} window. Individual")
            print("description limits cannot fix this alone — at this count the driver is")
            print("skill COUNT, so pair compression with skill-curator's Usage Evidence")
            print("deprecation (cold 30d) and archive (cold 90d) passes.")
            print()
        if no_desc:
            print(f"⚠️  {len(no_desc)} skill(s) have no description — they cost listing")
            print("   space while giving the router nothing to match on:")
            for s in no_desc[:15]:
                print(f"     - {s['dir']}")
            if len(no_desc) > 15:
                print(f"     … and {len(no_desc) - 15} more")
            print()
        if args.top:
            print(f"### Worst {len(worst)} by listing cost")
            print()
            print("| Skill | Desc chars | Listing chars |")
            print("|-------|-----------|---------------|")
            for s in worst:
                print(f"| {s['dir']} | {s['desc_chars']} | {s['listing_chars']} |")

    if args.strict and any(v["over"] for v in verdicts):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
