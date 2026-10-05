#!/bin/bash
# Sloppiness warn hook — PostToolUse(Write|Edit|MultiEdit), soft, never blocks.
# Scores the single file just written against scripts/quality/sloppiness-score.sh's
# Verbosity/Erosion metrics (non-LLM-judge, see earendil.com "Measuring code
# sloppiness"). Warns only when the file itself crosses the published AI-agent
# baseline — a single elevated file isn't worth interrupting on, high-slop is.

set -u

EVENT=$(cat)
SCRIPT=".claude/scripts/quality/sloppiness-score.sh"

[ -f "$SCRIPT" ] || exit 0

FILE_PATH=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    inp = e.get('tool_input', {})
    print(inp.get('file_path', inp.get('path', '')))
except Exception:
    print('')
" 2>/dev/null || echo "")

[[ -z "$FILE_PATH" ]] && exit 0
[ -f "$FILE_PATH" ] || exit 0

echo "$FILE_PATH" | grep -qE '\.(py|js|jsx|ts|tsx|go|rs|java|rb|sh|bash|c|cc|cpp|h|hpp)$' || exit 0

RESULT="$(bash "$SCRIPT" "$FILE_PATH" 2>/dev/null)"
[ -z "$RESULT" ] && exit 0

read -r VERBOSITY EROSION VERDICT <<< "$(echo "$RESULT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('verbosity', ''), d.get('erosion', ''), d.get('verdict', ''))
except Exception:
    print('', '', '')
" 2>/dev/null)"

RECORDER=".claude/scripts/session/record_metric.py"
if [ -n "$VERBOSITY" ] && [ -f "$RECORDER" ]; then
  python3 "$RECORDER" --metric sloppiness --value "$VERBOSITY" \
    --meta "{\"verdict\": \"$VERDICT\", \"erosion\": $EROSION, \"file\": \"$FILE_PATH\"}" \
    >/dev/null 2>&1 || true
fi

[[ "$VERDICT" != "high-slop" ]] && exit 0

echo "[SLOPPINESS] $FILE_PATH scored high-slop (at/above AI-agent baseline verbosity 0.33 / erosion 0.68): $RESULT — consider deduplicating repeated blocks or flattening nested branches before moving on. Advisory only."

exit 0

# REGISTRATION
# {
#   "matcher": "Write|Edit|MultiEdit",
#   "hooks": [{"type": "command", "command": "bash .claude/hooks/sloppiness-warn-hook.sh"}]
# }
# Add under PostToolUse (.py/.ts/.js quality-gate group) in templates/settings.json.template.
