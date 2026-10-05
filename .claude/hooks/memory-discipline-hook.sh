#!/bin/bash
# Fires PreToolUse on Write|Edit — gates memory file writes with discipline rules.
# Claude sees these rules before the write executes and can revise the content.
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

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

IS_MEMORY=false
if echo "$FILE_PATH" | grep -qE '/memory/[^/]+\.md$'; then
  IS_MEMORY=true
elif echo "$FILE_PATH" | grep -qE '/MEMORY\.md$'; then
  IS_MEMORY=true
fi

[[ "$IS_MEMORY" == false ]] && exit 0

cat << 'RULES'
╔══ Memory Discipline Gate ══════════════════════════════════════════╗
║  You are about to write to a memory file.                         ║
║  Review your content and REVISE if it violates these rules:       ║
╚═══════════════════════════════════════════════════════════════════╝

STORE — reusable across future tasks:
  ✓ Workflow patterns and process lessons
  ✓ User preferences and collaboration style
  ✓ Reusable procedures and heuristics

DO NOT STORE — case-specific, not reusable:
  ✗ Task/case-specific conclusions or findings
  ✗ Document citations or evidence from this run
  ✗ Investigation outcomes or determinations
  ✗ Entity-specific facts (client names, data values, document titles)

Transfer test: "Would this help a DIFFERENT future task, or only describe THIS task's results?"
If the latter → write it to an artifact/output file, NOT memory.

You may revise the content before proceeding with this write.
RULES
