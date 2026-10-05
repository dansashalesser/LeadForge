#!/bin/bash
# PostToolUse(Skill) — token-free skill usage tracker.
# Appends one JSON line per Skill-tool invocation to a global log so the
# skill-curator can make evidence-based deprecation decisions and the
# dashboard can surface hot/cold skills. Pattern lifted from the Hermes
# agent's curator "usage log" (load count + last-use timestamp).
#
# NEVER blocks, NEVER prints — pure side-effect logging. Always exits 0.
set -euo pipefail

EVENT=$(cat)

# Global aggregation point — every repo's copy of this hook appends here, so
# usage is counted once across all projects. This is a deliberate cross-repo
# SINGLETON, not per-repo self-location: the dashboard/orchestrator read this one
# central log, so resolving a per-repo $HARNESS_DIR would scatter the data and
# break aggregation. Portable via $SDD_HARNESS_HOME override for non-standard
# installs; defaults to the canonical $HOME-relative location.
LOG_DIR="${SDD_HARNESS_HOME:-$HOME/.claude/sdd-harness}/logs"
LOG_FILE="$LOG_DIR/skill-usage.jsonl"

# Extract the skill name from tool_input.skill. Bail silently on anything odd.
SKILL=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    if e.get('tool_name', '') != 'Skill':
        sys.exit(0)
    print(e.get('tool_input', {}).get('skill', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

[ -z "$SKILL" ] && exit 0

# Whitelist the name charset: lowercase/digits/dash plus ':' '/' for
# plugin-namespaced and scoped skills (e.g. kiro:reflect, apps/web:deploy).
# Anything else → drop, never write attacker-controlled bytes to the log.
SKILL=$(printf '%s' "$SKILL" | tr -cd 'a-zA-Z0-9:/_-' | head -c 80)
[ -z "$SKILL" ] && exit 0

# Refuse to follow a symlink planted where the log should be.
[ -L "$LOG_FILE" ] && exit 0

mkdir -p "$LOG_DIR" 2>/dev/null || exit 0

TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# Single-line append is atomic for small writes on local filesystems.
printf '{"ts":"%s","skill":"%s"}\n' "$TS" "$SKILL" >> "$LOG_FILE" 2>/dev/null || true

exit 0

# REGISTRATION — add to settings.json PostToolUse array (projects via
# templates/settings.json.template, copied to each repo's .claude/settings.json):
# {
#   "matcher": "Skill",
#   "hooks": [{ "type": "command", "command": "bash .claude/hooks/skill-usage-tracker.sh" }]
# }
