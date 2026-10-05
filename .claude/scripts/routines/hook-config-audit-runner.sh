#!/bin/bash
# hook-config-audit-runner.sh — weekly sweep of the repo's own hooks/MCP config
# as attack surface (secrets, network-exfil, over-broad permission grants).
#
# Deterministic (no LLM call). Thin wrapper around scripts/utils/hook-config-audit.py,
# which reuses scan-pii.sh's OPF engine for the secrets check and adds exfil +
# permission-grant checks of its own. Writes .claude/reports/security/hook-config-audit.json.
# Self-paces to weekly via a state-file guard, so calling it daily from the
# orchestrator is a cheap no-op between runs.
#
# Usage:
#   hook-config-audit-runner.sh            — current repo (cwd), respecting cadence
#   hook-config-audit-runner.sh --force    — ignore cadence guard, run now
# Env:
#   SDD_SKIP_HOOK_CONFIG_AUDIT=1           — opt out entirely
#   HOOK_CONFIG_AUDIT_GAP_DAYS=<days>      — cadence override (default 7)

set -u

[ "${SDD_SKIP_HOOK_CONFIG_AUDIT:-0}" = "1" ] && exit 0

REPO="$(pwd)"
FORCE=false
[ "${1:-}" = "--force" ] && FORCE=true

MIN_GAP_DAYS="${HOOK_CONFIG_AUDIT_GAP_DAYS:-7}"
STATE_FILE="$REPO/.claude/memory/.last-hook-config-audit"
TODAY="$(date +%Y-%m-%d)"

# Only meaningful inside an installed repo
[ -d "$REPO/.claude" ] || exit 0

if [ "$FORCE" = false ] && [ -f "$STATE_FILE" ]; then
  last_day="$(cut -dT -f1 "$STATE_FILE" 2>/dev/null | head -1)"
  if [ -n "$last_day" ]; then
    gap="$(python3 -c "
import datetime
print((datetime.date.today() - datetime.date.fromisoformat('$last_day')).days)
" 2>/dev/null || echo "")"
    if [ -n "$gap" ] && [ "$gap" -lt "$MIN_GAP_DAYS" ] 2>/dev/null; then
      exit 0
    fi
  fi
fi

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
AUDIT_PY="$SCRIPT_DIR/../utils/hook-config-audit.py"

[ -f "$AUDIT_PY" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

mkdir -p "$REPO/.claude/reports/security"
OUT_FILE="$REPO/.claude/reports/security/hook-config-audit.json"

python3 "$AUDIT_PY" "$REPO" --json > "$OUT_FILE.tmp" 2>/dev/null
AUDIT_EXIT=$?
mv "$OUT_FILE.tmp" "$OUT_FILE"

if [ "$AUDIT_EXIT" -eq 1 ]; then
  echo "hook-config-audit: findings recorded in $OUT_FILE" >&2
fi

# Record run date for the cadence guard regardless of finding count — only a
# script-level failure (exit >1) should skip marking the run as done.
if [ "$AUDIT_EXIT" -eq 0 ] || [ "$AUDIT_EXIT" -eq 1 ]; then
  date -Iseconds > "$STATE_FILE"
fi

exit 0
