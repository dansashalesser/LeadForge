#!/bin/bash
# Revert detection hook — PostToolUse(Bash)
# Fires immediately when Claude runs a git revert/reset/restore command.
# Writes a [revert] drain observation so the trust-battery Judge has concrete evidence
# rather than having to infer it from transcript analysis at session end.

set -euo pipefail

EVENT=$(cat)

CMD=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    print(e.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

[[ -z "$CMD" ]] && exit 0

REVERT_TYPE=""
if   echo "$CMD" | grep -qE "git revert\b";                       then REVERT_TYPE="git-revert"
elif echo "$CMD" | grep -qE "git reset\b.*(--hard|--mixed|HEAD)"; then REVERT_TYPE="git-reset"
elif echo "$CMD" | grep -qE "git (restore|checkout)\s+--";        then REVERT_TYPE="git-restore"
fi

[[ -z "$REVERT_TYPE" ]] && exit 0

OBS_FILE=".claude/memory/observations.md"
[[ ! -f "$OBS_FILE" ]] && exit 0

today=$(date +%Y-%m-%d)
CMD_SHORT=$(echo "$CMD" | tr '\n' ' ' | cut -c1-80)

# Skip if this exact command was already recorded today
grep -qF "$CMD_SHORT" "$OBS_FILE" 2>/dev/null && exit 0

echo "- $today [revert]: $REVERT_TYPE — \`${CMD_SHORT}\`" >> "$OBS_FILE"
