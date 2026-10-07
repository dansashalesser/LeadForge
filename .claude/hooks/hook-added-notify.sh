#!/bin/bash
# Fires PostToolUse (Write|Edit) — detects new hook files added to .claude/hooks/
# and injects a documentation reminder into Claude's context.

set -euo pipefail

EVENT=$(cat)
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

# Only care about .sh files inside a .claude/hooks/ directory
if ! echo "$FILE_PATH" | grep -qE '\.claude/hooks/[^/]+\.sh$'; then
  exit 0
fi

HOOK_NAME=$(basename "$FILE_PATH")
DOCS_FILE="docs/hooks/README.md"

# If the hook is already documented, stay silent
if [[ -f "$DOCS_FILE" ]] && grep -qF "$HOOK_NAME" "$DOCS_FILE" 2>/dev/null; then
  exit 0
fi

cat << EOF
╔══ Hook Documentation Required ════════════════════════════════════╗
║  New hook file detected: $HOOK_NAME
║  Update docs/hooks/README.md before this session ends.           ║
║                                                                   ║
║  Add a section with:                                              ║
║    - Event (SessionStart / Stop / PreToolUse / PostToolUse /     ║
║      PreCompact) and matcher                                      ║
║    - Purpose (one sentence)                                       ║
║    - Why it's needed                                              ║
║    - Output / side effect                                         ║
║    - Status (active / utility)                                    ║
║  Also add the wiring entry to the Wiring Reference table.        ║
╚═══════════════════════════════════════════════════════════════════╝
EOF
