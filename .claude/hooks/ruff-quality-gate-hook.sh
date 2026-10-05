#!/bin/bash
# ruff-quality-gate-hook — PostToolUse soft gate (Write/Edit/MultiEdit) on .py files.
# CLAUDE.md's Quality Gates section claims "ruff check: on every .py file write" as
# automated — until this hook, nothing actually ran it; a `Bash(ruff check *)`
# permission entry only lets Claude run it if it remembers to. This closes that gap.
# Advisory only — surfaces findings back into context, never blocks. Exits 0 always.
set -euo pipefail

EVENT=$(cat)

FILE_PATH=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    if e.get('tool_name', '') not in ('Write', 'Edit', 'MultiEdit'):
        sys.exit(0)
    inp = e.get('tool_input', {}) or {}
    print(inp.get('file_path', inp.get('path', '')))
except Exception:
    pass
" 2>/dev/null || echo "")

[[ -z "$FILE_PATH" ]] && exit 0
[[ "$FILE_PATH" != *.py ]] && exit 0
[[ ! -f "$FILE_PATH" ]] && exit 0

# CLAUDE.md's Serena rule (diagnostics after every .py edit) had no enforcement — nudge it here.
echo "serena: run mcp__serena__get_diagnostics_for_file(\"$FILE_PATH\") — ruff alone misses type errors."

command -v ruff >/dev/null 2>&1 || exit 0

OUTPUT=$(ruff check "$FILE_PATH" 2>/dev/null || true)
[[ -z "$OUTPUT" ]] && exit 0

Y="\033[1;33m"; B="\033[1m"; R="\033[0m"
echo -e "${B}${Y}⚠  ruff-quality-gate — $(basename "$FILE_PATH")${R}"
echo "$OUTPUT"
echo ""
echo -e "${B}Advisory only — this write already happened.${R} Fix findings above if real, or explain why they don't apply."

exit 0

# REGISTRATION (settings.json — PostToolUse, matcher "Write|Edit|MultiEdit"):
# {
#   "matcher": "Write|Edit|MultiEdit",
#   "hooks": [
#     { "type": "command", "command": "bash .claude/hooks/ruff-quality-gate-hook.sh" }
#   ]
# }
