#!/bin/bash
# Reject-feedback hook — UserPromptSubmit, SOFT (never blocks).
#
# When the user rejects/interrupts a tool call, Claude Code records it in the
# transcript and the user's explanation arrives as their NEXT prompt. This
# hook walks the transcript backward, detects that pattern, classifies the
# explanation into a reject reason, and — for actionable categories only —
# appends a [friction] line to the SAME observations.md that
# revert-detect-hook.sh and action-capture.sh already write to. No new file,
# no new ledger, no dashboard: it rides the existing append-only log so
# whatever already consumes observations.md (nightly consolidation,
# /kiro:reflect) picks it up for free.
#
# Deliberately distinct from tool-failure-capture.sh / tool-failure-recall.sh:
# those record when a Bash/MCP call RAN and ERRORED (a command-execution
# signal with its own ledger + promotion routine). This hook fires on the
# user DECLINING or REDIRECTING a proposed action before/without it running —
# a different signal, so it does not feed or duplicate that ledger.
#
# Adapted from claude-codex-settings' claude-telemetry-hooks plugin
# (github.com/fcakyon/claude-codex-settings), with the OTel export dropped —
# this harness has no OTel backend configured, so that half would be dead
# code. The categorization regex taxonomy is the part worth keeping.
set -u

EVENT_FILE="$(mktemp)"
cat > "$EVENT_FILE"
trap 'rm -f "$EVENT_FILE"' EXIT

OBS_FILE=".claude/memory/observations.md"
[ -f "$OBS_FILE" ] || exit 0

EVENT_FILE="$EVENT_FILE" OBS_FILE="$OBS_FILE" python3 - <<'PY' 2>/dev/null || true
import json, os, re, sys
from pathlib import Path

try:
    e = json.load(open(os.environ["EVENT_FILE"]))
except Exception:
    sys.exit(0)

prompt = e.get("prompt", "") or ""
transcript_path = e.get("transcript_path", "") or ""
if not prompt or not transcript_path:
    sys.exit(0)

# Categories worth a memory line — an actual redirect/correction the user had
# to spell out. Noise (profanity, bare "no", rhetorical "why", "try again")
# is deliberately NOT logged: it would flood observations.md's own
# "max 5 new entries per /kiro:reflect" discipline without adding signal.
CATEGORIES = [
    ("wrong_target", re.compile(
        r"\b(i meant|wrong (file|place|repo|branch|machine|directory)|not that|not the|same (file|place|location))\b|^no,? i\b",
        re.I)),
    ("tool_steering", re.compile(
        r"\b(use|just use|stick to|prefer)\b.*\b(rg|grep|gh|tavily|mcp|slack|bun|uv|pytest|jq)\b|\binstead of\b", re.I)),
    ("scope_drift", re.compile(
        r"\b(without breaking|only (do|fix|change|the)|don'?t (touch|change|modify|add|create|put|run)|overengin|bloated|no need (to|for))\b",
        re.I)),
    ("verify_first", re.compile(
        r"\b(check (first|docs|code|source|the)|have you (checked|read|verified)|read (the )?(code|docs|source)|move with evidence)\b",
        re.I)),
    ("rule_setting", re.compile(r"\b(never|always|from now|next time|remember to)\b", re.I)),
    ("factual_challenge", re.compile(
        r"\b(you (said|did|claimed|forgot|missed)|hallucin|made up|no such|doesn'?t exist)\b", re.I)),
]


def find_recent_reject(transcript_path: str) -> bool:
    """Walk transcript backward for a rejected/interrupted tool call in the last N lines."""
    p = Path(transcript_path)
    if not p.exists():
        return False
    lines = p.read_text().splitlines()
    for line in reversed(lines[-30:]):
        try:
            entry = json.loads(line)
        except Exception:
            continue
        msg = entry.get("message", entry)
        content = msg.get("content") if isinstance(msg, dict) else None
        tur = entry.get("toolUseResult")
        tur = tur if isinstance(tur, dict) else {}
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                    return True
        if tur.get("interrupted"):
            return True
    return False


if not find_recent_reject(transcript_path):
    sys.exit(0)

category = None
for name, pat in CATEGORIES:
    if pat.search(prompt):
        category = name
        break
if category is None:
    sys.exit(0)

obs_file = os.environ["OBS_FILE"]
today = __import__("datetime").date.today().isoformat()
excerpt = re.sub(r"\s+", " ", prompt).strip()[:120]

existing = Path(obs_file).read_text()
line = f"- {today} [friction]: tool rejected ({category}) — \"{excerpt}\""
if excerpt not in existing:
    with open(obs_file, "a") as f:
        f.write(line + "\n")
PY

exit 0

# REGISTRATION (settings.json) — wired in templates/settings.json.template:
# "UserPromptSubmit": [
#   { "matcher": "",
#     "hooks": [ { "type": "command", "command": "bash .claude/hooks/reject-feedback-hook.sh" } ] }
# ]
