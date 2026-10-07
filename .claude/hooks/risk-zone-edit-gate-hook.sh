#!/bin/bash
# Risk-zone edit gate — PreToolUse(Write|Edit|MultiEdit), soft gate.
#
# Reads .claude/steering/risk-zones.md (seeded weekly by
# risk-zone-reseed-runner.sh from git churn + test-file presence + gitnexus
# impact) and warns before an edit lands in a red/yellow zone file. Soft, not
# a hard deny: PreToolUse can only allow/deny/ask, and a wrong or stale zone
# call must never block real work — same tradeoff protected-path-hook.sh
# makes for sensitive files. Silent for green/unlisted files.

set -u

EVENT=$(cat)
MAP=".claude/steering/risk-zones.md"

[ -f "$MAP" ] || exit 0

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

REL_PATH="${FILE_PATH#"$(pwd)"/}"

ROW=$(grep -F "\`$REL_PATH\`" "$MAP" 2>/dev/null | head -1)
[ -z "$ROW" ] && exit 0

ZONE=$(echo "$ROW" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $3); print $3}')
SIGNAL=$(echo "$ROW" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $4); print $4}')

[[ "$ZONE" == "green" ]] && exit 0
[[ -z "$ZONE" ]] && exit 0

cat << MSG
╔══ Risk Zone: ${ZONE} — ${REL_PATH} ══════════════════════════╗
║  Signal: ${SIGNAL}
╚════════════════════════════════════════════════════════════════════╝

  This file is flagged '${ZONE}' in .claude/steering/risk-zones.md
  (high churn / low test coverage / high blast radius).

  Before editing: check for characterization tests, and run
  gitnexus impact on the touched symbol if this is a red zone.
  Mention the zone to the user if the change is non-trivial.
MSG

exit 0

# REGISTRATION (settings.json):
# {
#   "matcher": "Write|Edit|MultiEdit",
#   "hooks": [{"type": "command", "command": "bash .claude/hooks/risk-zone-edit-gate-hook.sh"}]
# }
# Add as an entry under PreToolUse in templates/settings.json.template.
