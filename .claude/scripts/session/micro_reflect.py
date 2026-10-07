#!/usr/bin/env python3
"""
Micro-reflect: immediately ingest re-explanation drains into hot-memory.

Takes drain JSON (from detect_reexplanation.py --mode drain) on stdin.
Calls an LLM to extract the concrete, durable fact the AI should permanently
remember, then appends it to hot-memory.md under an ## Auto-learned section.

Called from the stop hook in the background when drain signals are found.
Auto-learned entries are probationary — the nightly housekeeping-agent either
promotes them to patterns.md (if reinforced) or removes them after 7 days.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

MODEL = "claude-haiku-4-5-20251001"
HOT_MEMORY = Path(".claude/memory/hot-memory.md")
SECTION_HEADER = "## Auto-learned"
TAG = "auto-learn"

PROMPT = """\
You are extracting memory-worthy facts from re-explanation signals in an AI coding session.

The user had to re-explain these things to the AI (verbatim excerpts):
{contexts}

For each re-explanation, extract the ONE concrete, durable fact the AI should \
permanently remember to avoid repeating this mistake.

Rules:
- Write as a short imperative, ≤15 words: e.g. "Always use absolute paths in agent bash calls."
- Must generalize beyond today's task — skip facts specific to the current branch, ticket, or feature
- If two drains encode the same fact, write it once
- Max 3 facts total

Respond ONLY with valid JSON, no prose before or after:
[
  {{"fact": "Always use absolute paths when running bash commands in agent subshells."}}
]

If nothing is worth remembering permanently, respond with: []"""


def _call_llm(prompt: str) -> str | None:
    """Extract facts via headless Claude Code.

    The `anthropic` SDK path was removed: it required an ANTHROPIC_API_KEY that
    bills separately from the Claude subscription, and with no key set every call
    raised and fell through to this same CLI anyway. SDD_HEADLESS=1 keeps the stop
    hook from launching its own detector when this child session ends.
    """
    try:
        result = subprocess.run(
            ["claude", "--print", "--model", MODEL, "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "SDD_HEADLESS": "1"},
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"WARN: `claude --print` unavailable ({e})", file=sys.stderr)
        return None

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:200]
        print(f"WARN: `claude --print` exited {result.returncode}: {detail}", file=sys.stderr)
        return None

    return result.stdout.strip()


def _parse_facts(raw: str) -> list[str]:
    try:
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start == -1 or end == 0:
            return []
        items = json.loads(raw[start:end])
        return [
            item["fact"].strip()
            for item in items
            if isinstance(item, dict) and item.get("fact")
        ]
    except (json.JSONDecodeError, KeyError):
        return []


def _already_known(fact: str, content: str) -> bool:
    """Skip if 3+ content words overlap with an existing auto-learn line."""
    stopwords = {"always", "never", "use", "the", "a", "an", "in", "on", "to",
                 "for", "of", "with", "when", "is", "are", "be", "do", "not"}
    existing = [l for l in content.splitlines() if f"[{TAG}" in l]
    fact_words = {w for w in fact.lower().split() if w not in stopwords}
    for line in existing:
        line_words = {w for w in line.lower().split() if w not in stopwords}
        if len(fact_words & line_words) >= 3:
            return True
    return False


def main() -> int:
    raw_input = sys.stdin.read().strip()
    if not raw_input:
        return 0

    try:
        drains = json.loads(raw_input)
    except json.JSONDecodeError:
        return 0

    if not drains or not HOT_MEMORY.is_file():
        return 0

    contexts = "\n".join(
        f'- "{d["context"]}"' for d in drains if d.get("context")
    )
    if not contexts:
        return 0

    raw = _call_llm(PROMPT.replace("{contexts}", contexts))
    if not raw:
        return 0

    facts = _parse_facts(raw)
    if not facts:
        return 0

    content = HOT_MEMORY.read_text(encoding="utf-8")
    today = date.today().isoformat()
    new_lines = [
        f"- [{TAG}, {today}]: {fact}"
        for fact in facts[:3]
        if not _already_known(fact, content)
    ]

    if not new_lines:
        return 0

    lines = content.splitlines()
    section_idx = next(
        (i for i, l in enumerate(lines) if l.strip() == SECTION_HEADER), None
    )
    if section_idx is not None:
        lines = lines[: section_idx + 1] + new_lines + lines[section_idx + 1 :]
    else:
        lines += ["", SECTION_HEADER] + new_lines

    HOT_MEMORY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"micro-reflect: {len(new_lines)} fact(s) ingested into hot-memory.md",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
