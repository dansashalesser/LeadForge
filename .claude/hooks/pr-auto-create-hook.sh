#!/bin/bash
# PR auto-create hook — PostToolUse(Bash)
# Fires after every Bash call; only acts on a plain `git push` (force-push stays
# denied at the permission layer, never reaches here). Auto-detects the real
# base branch and opens a PR against it if one doesn't already exist.

set -uo pipefail

EVENT=$(cat)

CMD=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    print(e.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

RESP=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    r = e.get('tool_response', '')
    print(str(r)[:1000])
except Exception:
    print('')
" 2>/dev/null || echo "")

[[ -z "$CMD" ]] && exit 0
echo "$CMD" | grep -qE "git push($|[^-])" || exit 0
echo "$CMD" | grep -qE -- "--force|(^|\s)-f(\s|$)" && exit 0

# Best-effort failure detection — a rejected/failed push shouldn't trigger a PR.
echo "$RESP" | grep -qiE "rejected|failed to push|non-fast-forward|error:|could not read|permission denied" && exit 0

SCRIPT=".claude/scripts/pr/detect_base_and_create.sh"
[ -f "$SCRIPT" ] || exit 0

bash "$SCRIPT"

# REGISTRATION
# {
#   "matcher": "Bash",
#   "hooks": [{"type": "command", "command": "bash .claude/hooks/pr-auto-create-hook.sh"}]
# }
# Add as a third entry alongside revert-detect-hook.sh / setup-buffer-hook.sh
# under PostToolUse -> matcher "Bash" in templates/settings.json.template.
