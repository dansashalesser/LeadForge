#!/usr/bin/env bash
# cheap-model-delegation-hook.sh — PreToolUse(Bash) advisory nudge.
#
# Fires when a single Bash command reads more than a handful of files at once
# (cat/head/tail with 6+ path-looking args, or a glob expansion). Suggests the
# cheap-model-delegation skill instead of doing bulk mechanical extraction at
# the primary model's rate. Advisory only — never blocks, exits 0 always.
#
# Scope note: this is deliberately soft. A PreToolUse(Read) hard gate already
# exists for single large files (lean-ctx-nudge-hook.sh); bulk multi-file Bash
# reads can't be reliably judged as "extraction work" vs. "legitimate small
# multi-file read" from the command line alone, so this only nudges.
set -uo pipefail

EVENT=$(cat)

CMD=$(printf '%s' "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    if e.get('tool_name', '') != 'Bash':
        sys.exit(0)
    print(e.get('tool_input', {}).get('command', ''))
except Exception:
    pass
" 2>/dev/null || echo "")

[[ -z "$CMD" ]] && exit 0

case "$CMD" in
  cat\ *|head\ *|tail\ *) ;;
  *) exit 0 ;;
esac

# Count path-looking whitespace-separated tokens after the command name,
# excluding flags (leading -). 6+ is the bulk-read threshold.
FILE_COUNT=$(printf '%s' "$CMD" | awk '{
  n=0
  for (i=2; i<=NF; i++) { if ($i !~ /^-/) n++ }
  print n
}')

[[ "$FILE_COUNT" -lt 6 ]] && exit 0

cat << NUDGE
╔══ cheap-model-delegation Opportunity ══════════════════════════════╗
║  This command reads $FILE_COUNT+ files in one call.
║  If this is bulk extraction (pull one fact/summary per file, no deep
║  reasoning per file), consider delegating to a haiku-tier subagent
║  instead of reading all of it at the primary model's rate.
║  → Skill("cheap-model-delegation")
╚═══════════════════════════════════════════════════════════════════╝
NUDGE

exit 0

# REGISTRATION (settings.json — PreToolUse, matcher "Bash"):
# {
#   "matcher": "Bash",
#   "hooks": [
#     { "type": "command", "command": "bash \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/cheap-model-delegation-hook.sh\"" }
#   ]
# }
