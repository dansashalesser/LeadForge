#!/usr/bin/env bash
# pr-mention-nudge.sh — UserPromptSubmit hook
#
# Detects when the user mentions a PR in conversation and no PR exists yet for
# the current branch — triggers the same auto-create-or-confirm flow as
# pr-auto-create-hook.sh (which fires on push instead). Fires on every prompt;
# exits immediately if no keywords match.
#
# REGISTRATION (in settings.json under "UserPromptSubmit"):
# {
#   "matcher": "",
#   "hooks": [{"type": "command", "command": "bash .claude/hooks/pr-mention-nudge.sh"}]
# }

set -uo pipefail

PROMPT_TEXT=$(python3 - 2>/dev/null <<'PYEOF'
import json, sys
try:
    d = json.load(sys.stdin)
    text = d.get("prompt", "") or ""
    print(text.lower())
except Exception:
    pass
PYEOF
)

[[ -z "$PROMPT_TEXT" ]] && exit 0
echo "$PROMPT_TEXT" | grep -qE "\bpr\b|pull request|open a pr|create a pr|merge this" || exit 0

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

SCRIPT=".claude/scripts/pr/detect_base_and_create.sh"
[ -f "$SCRIPT" ] || exit 0

bash "$SCRIPT"
