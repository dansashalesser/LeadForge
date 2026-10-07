#!/usr/bin/env python3
"""skill-quality-scan.py — bulk SkillOS quality score for every installed skill.

`skill-curator-prompt.md` Phase 1 asked the model to score each of ~1000
SKILL.md files one at a time across four dimensions. That is enough tool calls
to burn a headless `claude --print` session's whole turn/time budget before it
ever reaches Phase 4 (writing `reports/skill-curation-report.md`) — the
report's own Aug-6 "Methodology note" already admits the scores are "heuristic,
computed in bulk (not a per-skill manual read of all 991 bodies)", meaning
whichever run produced that report improvised a bulk pass; every run since has
not, and the report has not been replaced since. This script is that bulk pass,
checked in so it is the same computation every week instead of reinvented (or
skipped) each time.

Four SkillOS dimensions (0-3 each, 12 total), same as skill-curator-prompt.md:
    Task relevance:      description present + has an explicit trigger cue
    Operational validity: body word count as a substance proxy
    Content quality:      body word count, stricter buckets
    Compression ratio:    body words vs description token cost

Also flags the structural YAML defect from 2026-09: `description: ">"` (a
quoted literal two-char string) instead of a bare `>` folded-scalar indicator
— this silently truncates the description to nothing at parse time.

Usage:
    python3 scripts/skill-quality-scan.py
    python3 scripts/skill-quality-scan.py --threshold 6 --json
    python3 scripts/skill-quality-scan.py --dir ~/.claude/skills
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_SKILL_DIRS = [
    Path.home() / ".claude" / "skills",
]

TRIGGER_CUES = (
    "use when", "use this when", "use for", "trigger", "whenever",
    "invoke when", "call when", "when the user", "when you need",
)


def read_skill(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None

    desc_raw = ""
    defect = False
    i = 1
    while i < end:
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("description:"):
            value = stripped[len("description:"):].strip()
            if value in ('">"', "'>'"):
                defect = True
                desc_raw = ""
            elif value in ("|", ">", "|-", ">-", "|+", ">+"):
                block = []
                i += 1
                while i < end and (not lines[i].strip() or lines[i][:1].isspace()):
                    block.append(lines[i].strip())
                    i += 1
                desc_raw = " ".join(p for p in block if p)
                continue
            else:
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                desc_raw = value
        i += 1

    body = "\n".join(lines[end + 1:])
    body_words = len(body.split())

    return {"description": desc_raw, "yaml_defect": defect, "body_words": body_words}


def score_task_relevance(desc: str, defect: bool) -> int:
    if defect or not desc:
        return 0
    lowered = desc.lower()
    if any(cue in lowered for cue in TRIGGER_CUES):
        return 3
    if len(desc) < 20:
        return 1
    return 2


def score_operational_validity(body_words: int) -> int:
    if body_words < 20:
        return 0
    if body_words < 60:
        return 1
    if body_words < 150:
        return 2
    return 3


def score_content_quality(body_words: int) -> int:
    if body_words < 40:
        return 0
    if body_words < 100:
        return 1
    if body_words < 250:
        return 2
    return 3


def score_compression_ratio(body_words: int, desc_chars: int) -> int:
    desc_tokens = max(1, desc_chars // 4)
    ratio = body_words / desc_tokens
    if ratio < 2:
        return 0
    if ratio < 5:
        return 1
    if ratio < 15:
        return 2
    return 3


def collect(skill_dirs: list[Path]) -> list[dict]:
    results = []
    seen = set()
    for root in skill_dirs:
        if not root.is_dir():
            continue
        for skill_md in sorted(root.glob("*/SKILL.md")):
            name = skill_md.parent.name
            if name in seen:
                continue
            seen.add(name)
            parsed = read_skill(skill_md)
            if parsed is None:
                continue
            desc = parsed["description"]
            defect = parsed["yaml_defect"]
            body_words = parsed["body_words"]
            dims = {
                "task_relevance": score_task_relevance(desc, defect),
                "operational_validity": score_operational_validity(body_words),
                "content_quality": score_content_quality(body_words),
                "compression_ratio": score_compression_ratio(body_words, len(desc)),
            }
            total = sum(dims.values())
            results.append({
                "name": name,
                "path": str(skill_md),
                "desc_chars": len(desc),
                "body_words": body_words,
                "yaml_defect": defect,
                **dims,
                "total": total,
            })
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", action="append", type=Path,
                     help="Skill root to scan (repeatable). Default: ~/.claude/skills")
    ap.add_argument("--threshold", type=int, default=6,
                     help="Low-quality cutoff, total <= threshold (default 6, out of 12)")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of a report")
    args = ap.parse_args()

    dirs = args.dir or DEFAULT_SKILL_DIRS
    skills = collect([Path(d).expanduser() for d in dirs])

    if not skills:
        print(f"No skills found under: {', '.join(str(d) for d in dirs)}", file=sys.stderr)
        return 2

    low_quality = sorted(
        [s for s in skills if s["total"] <= args.threshold],
        key=lambda s: s["total"],
    )
    defects = [s for s in skills if s["yaml_defect"]]

    if args.json:
        print(json.dumps({
            "skills_audited": len(skills),
            "threshold": args.threshold,
            "low_quality_count": len(low_quality),
            "yaml_defect_count": len(defects),
            "low_quality": low_quality,
            "yaml_defects": [s["name"] for s in defects],
        }, indent=2))
    else:
        print(f"Skills audited: {len(skills)}")
        print(f"Low-quality flags (score <= {args.threshold}/12): {len(low_quality)}")
        print(f"Structural YAML defect (description quoted \">\" instead of bare): {len(defects)}")
        print()
        print("Low-Quality Candidates, weakest first:")
        for s in low_quality:
            print(f"  {s['name']} — {s['total']}/12 "
                  f"(relevance={s['task_relevance']} validity={s['operational_validity']} "
                  f"content={s['content_quality']} compression={s['compression_ratio']}) "
                  f"— desc {s['desc_chars']} chars, body {s['body_words']} words"
                  f"{' — YAML DEFECT' if s['yaml_defect'] else ''}")
        if defects:
            print()
            print("YAML defect (description silently empty at parse time):")
            for s in defects:
                print(f"  {s['name']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
