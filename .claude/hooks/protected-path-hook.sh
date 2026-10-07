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

SENSITIVE=false
REASON=""

if echo "$FILE_PATH" | grep -qE '(^|/)\.env($|\.[^/]*)'; then
  SENSITIVE=true; REASON=".env file (environment secrets)"
elif echo "$FILE_PATH" | grep -qE '\.(pem|key|p12|pfx|cert|crt|jks|keystore)$'; then
  SENSITIVE=true; REASON="cryptographic key or certificate"
elif echo "$FILE_PATH" | grep -qE '(^|/)(credentials?|\.secrets?|secrets?)([^/]*)?$'; then
  SENSITIVE=true; REASON="credentials or secrets file"
elif echo "$FILE_PATH" | grep -qE '(^|/)\.aws/(credentials|config)$'; then
  SENSITIVE=true; REASON="AWS credentials file"
elif echo "$FILE_PATH" | grep -qE '(^|/)\.ssh/'; then
  SENSITIVE=true; REASON="SSH directory"
fi

[[ "$SENSITIVE" == false ]] && exit 0

cat << MSG
╔══ Protected Path — Confirmation Required ══════════════════════════╗
║  ⚠️  You are about to write to a sensitive file.                   ║
╚════════════════════════════════════════════════════════════════════╝

  File:   ${FILE_PATH}
  Reason: ${REASON}

  Do NOT proceed automatically. Ask the user first:

    "This is a sensitive file (${REASON}).
     Are you sure you want me to write to ${FILE_PATH}?"

  Proceed only if the user explicitly confirms in this turn.
MSG
