#!/bin/bash
# PostToolUse — soft gate that fires when any SKILL.md is written or edited.
# Skills are mini-agents: they direct Claude to take actions with tools.
# This hook ensures every new skill is reviewed through agent-permissions-design
# before the creation workflow is marked complete.
# Does NOT block — outputs a reminder; Claude decides whether any issue requires action.
set -euo pipefail

EVENT=$(cat)

TOOL_NAME=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    print(e.get('tool_name', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

[[ "$TOOL_NAME" != "Write" && "$TOOL_NAME" != "Edit" ]] && exit 0

FILE_PATH=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    inp = e.get('tool_input', {})
    print(inp.get('file_path', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

# Only fire for skill files — not other .md writes
if [[ "$FILE_PATH" != */skills/*/SKILL.md ]]; then
  exit 0
fi

SKILL_NAME=$(basename "$(dirname "$FILE_PATH")")

cat << MSG
╔══ Skill Permissions Gate ══════════════════════════════════════╗
║  Skill written: $SKILL_NAME
║                                                                ║
║  Skills are mini-agents that direct Claude to act. Before     ║
║  marking this skill complete, follow                           ║
║  ~/.claude/skill-library/agent-permissions-design/SKILL.md     ║
║  and verify:                                                   ║
║                                                                ║
║  1. Tool access — does this skill direct use of destructive    ║
║     tools (Bash rm/force-push, Write overwrite)?              ║
║  2. Irreversible gates — are high-risk steps preceded by an   ║
║     explicit verification or user-confirmation instruction?    ║
║  3. Scope boundary — are "When to Use" / "Do Not Use when"    ║
║     sections specific enough to prevent accidental misfire?    ║
║  4. External access — does the skill touch external services,  ║
║     credentials, or APIs? If yes, is it least-privilege?      ║
║                                                                ║
║  No issues? Proceed. Issues found? Update before completing.  ║
╚════════════════════════════════════════════════════════════════╝
MSG

# REGISTRATION
# Add to PostToolUse Write|Edit in $SDD_HARNESS/.claude/settings.json:
# {
#   "type": "command",
#   "command": "bash .claude/hooks/skill-permissions-gate.sh"
# }
