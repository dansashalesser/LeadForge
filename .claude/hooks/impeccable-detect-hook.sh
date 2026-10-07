#!/bin/bash
# Impeccable anti-pattern detector — PostToolUse hook
# Runs after Write/Edit tool calls on frontend files.
# Requires: npm install -g impeccable  (one-time setup)

set -euo pipefail

YELLOW='\033[1;33m'
RED='\033[0;31m'
GREEN='\033[0;32m'
BOLD='\033[1m'
RESET='\033[0m'

FRONTEND_EXTS=("tsx" "jsx" "css" "scss" "less" "vue" "svelte" "html")

# --- Parse file_path from stdin JSON event ---
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

if [[ -z "$FILE_PATH" ]] || [[ ! -f "$FILE_PATH" ]]; then
  exit 0
fi

# --- Check extension ---
EXT="${FILE_PATH##*.}"
IS_FRONTEND=false
for FEXT in "${FRONTEND_EXTS[@]}"; do
  if [[ "$EXT" == "$FEXT" ]]; then
    IS_FRONTEND=true
    break
  fi
done

[[ "$IS_FRONTEND" == false ]] && exit 0

# --- Resolve impeccable ---
if command -v impeccable &>/dev/null; then
  IMPECCABLE_BIN="impeccable"
else
  # Not installed — skip silently (avoid npx cold-download on every write)
  exit 0
fi

# --- Run detection ---
echo -e "${BOLD}Impeccable — scanning $(basename "$FILE_PATH")${RESET}"

OUTPUT=$("$IMPECCABLE_BIN" detect "$FILE_PATH" 2>&1 || true)

if [[ -z "$OUTPUT" ]] || echo "$OUTPUT" | grep -qi "no issues\|0 issue\|clean"; then
  echo -e "${GREEN}✓ No anti-patterns detected.${RESET}"
  exit 0
fi

echo -e "${YELLOW}${OUTPUT}${RESET}"
echo ""
echo -e "${BOLD}Fix these before committing. Run:${RESET} impeccable detect $FILE_PATH"
