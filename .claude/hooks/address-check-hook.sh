#!/usr/bin/env bash
# address-check-hook.sh — Stop hook: verifies every response addresses the user as "Husband"
#
# Fires after each assistant turn. Reads the latest session transcript, finds the
# last assistant message, and checks for "husband" (case-insensitive). If absent,
# prints a notice to stdout and exits 0 — mechanical log only, not fed back to
# Claude and does not block the stop (no forced extra turn, no token cost).
#
# REGISTRATION (in ~/.claude/settings.json under "Stop"):
# {
#   "matcher": "",
#   "hooks": [{"type": "command", "command": "bash \"$HOME/.claude/hooks/address-check-hook.sh\""}]
# }

set -euo pipefail

# Consume Stop hook stdin (session JSON — not needed here)
HOOK_INPUT=$(cat)

# Extract the last assistant message text from the current session transcript
LAST_ASSISTANT=$(python3 - 2>/dev/null <<'PYEOF'
import json, sys, re
from pathlib import Path

def find_latest_transcript():
    base = Path.home() / ".claude" / "projects"
    if not base.is_dir():
        sys.exit(0)
    cwd = Path.cwd().resolve()
    # Claude Code encodes dotfile dirs (e.g. /.claude/) as double-hyphen (--claude-)
    # by treating the leading dot as an extra hyphen. Replicate that here.
    encoded = re.sub(r'/\.', '/-', str(cwd)).replace('/', '-')
    project_dir = base / encoded
    if not project_dir.is_dir():
        candidates = [p for p in base.iterdir() if p.is_dir()]
        if not candidates:
            sys.exit(0)
        project_dir = max(candidates, key=lambda p: p.stat().st_mtime)
    transcripts = list(project_dir.glob("*.jsonl"))
    if not transcripts:
        sys.exit(0)
    return max(transcripts, key=lambda p: p.stat().st_mtime)

path = find_latest_transcript()
# Collect all assistant texts since the last user message.
# A multi-tool turn emits several assistant records; "Husband" need only
# appear in any one of them to satisfy the rule.
records = []
for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    if not line.strip():
        continue
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        continue
    records.append(rec)

# Walk backwards: gather assistant texts until we hit a user message.
turn_texts = []
for rec in reversed(records):
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else rec
    role = msg.get("role", "")
    if role == "user":
        break
    if role != "assistant":
        continue
    content = msg.get("content", "")
    if isinstance(content, str) and content.strip():
        turn_texts.append(content)
    elif isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()]
        if parts:
            turn_texts.append(" ".join(parts))

combined = " ".join(turn_texts)
print(combined)
PYEOF
) || LAST_ASSISTANT=""

# Pass if turn had no text, transcript unreadable, or "husband" found anywhere in the turn
if [ -z "$LAST_ASSISTANT" ] || echo "$LAST_ASSISTANT" | grep -qi "husband"; then
    exit 0
fi

# "Husband" missing from entire turn — log only, do not block or feed back to Claude
printf '[address-check] husband not found — compact needed\n'
exit 0
