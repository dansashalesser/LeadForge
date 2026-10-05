#!/bin/bash
# PostToolUse — soft gate that prompts memory capture after high-signal Bash actions.
# Pattern: "memory from what agents DO, not just what they say" (Memori design principle).
# Does NOT block — outputs a reminder; Claude decides whether to save.
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

[[ "$TOOL_NAME" != "Bash" ]] && exit 0

COMMAND=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    print(e.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

OUTPUT=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    resp = e.get('tool_response', '')
    if isinstance(resp, dict):
        resp = str(resp)
    print(str(resp)[:600])
except Exception:
    print('')
" 2>/dev/null || echo "")

SIGNAL=""

if echo "$COMMAND" | grep -qE "^git commit"; then
    SIGNAL="git-commit"
elif echo "$COMMAND" | grep -qE "pytest|python -m pytest"; then
    if echo "$OUTPUT" | grep -qiE "FAILED|ERROR|error"; then
        SIGNAL="test-failure"
    fi
elif echo "$COMMAND" | grep -qE "docker.*(deploy|push)|kubectl apply|helm upgrade|npm run (deploy|release)|make (deploy|release|publish)"; then
    SIGNAL="deploy"
fi

# --- Wake-phase struggle detection (Sleep Cycle Protocol) ---
# Detects non-zero exit codes on non-test Bash commands and auto-writes a
# [seed-target:] observation so the nightly skill-augment-agent knows where to seed.
if [[ -z "$SIGNAL" ]]; then
    EXIT_CODE_VAL=$(echo "$OUTPUT" | python3 -c "
import sys, re
m = re.search(r'[Ee]xit code\s+([1-9]\d*)', sys.stdin.read())
print(m.group(1) if m else '')
" 2>/dev/null || echo "")

    if [[ -n "$EXIT_CODE_VAL" ]]; then
        # Infer skill domain from command content
        SKILL_DOMAIN="systematic-debugging"
        if echo "$COMMAND" | grep -qiE "import |pip |npm |yarn |poetry |uv |pip3 "; then
            SKILL_DOMAIN="dependency-management"
        elif echo "$COMMAND" | grep -qiE "docker |kubectl |helm |k8s"; then
            SKILL_DOMAIN="deployment-pipeline-design"
        elif echo "$COMMAND" | grep -qiE "^git |git "; then
            SKILL_DOMAIN="git-advanced-workflows"
        elif echo "$COMMAND" | grep -qiE "curl |wget |http"; then
            SKILL_DOMAIN="api-patterns"
        elif echo "$COMMAND" | grep -qiE "python |python3 |\.py"; then
            SKILL_DOMAIN="python-pro"
        elif echo "$COMMAND" | grep -qiE "node |npm |tsx? |\.tsx?"; then
            SKILL_DOMAIN="nodejs-backend-patterns"
        fi

        # Auto-write [seed-target:] observation (idempotent within same command+day)
        OBS_FILE=".claude/memory/observations.md"
        if [ -f "$OBS_FILE" ]; then
            TODAY=$(date +%Y-%m-%d)
            CMD_SHORT=$(echo "$COMMAND" | head -c 60 | tr '\n' ' ')
            echo "- $TODAY [seed-target:$SKILL_DOMAIN]: command failed (exit $EXIT_CODE_VAL): ${CMD_SHORT}" >> "$OBS_FILE"
        fi

        SIGNAL="struggle"
    fi
fi

[[ -z "$SIGNAL" ]] && exit 0

case "$SIGNAL" in
  git-commit)
    cat << 'MSG'
╔══ Action Capture ══════════════════════════════════════╗
║  git commit completed.                                 ║
║  Consider: is there a workflow lesson, decision, or   ║
║  pattern here worth saving to memory?                  ║
║  (Transfer test: reusable across future tasks? If not  ║
║  — skip it. Commit rationale belongs in git log.)     ║
╚════════════════════════════════════════════════════════╝
MSG
    ;;
  test-failure)
    cat << 'MSG'
╔══ Action Capture ══════════════════════════════════════╗
║  Tests failed.                                         ║
║  Consider: does this reveal a recurring breakage       ║
║  pattern or brittle setup worth remembering?           ║
║  (If it's a one-off — skip. If it's happened before   ║
║  or likely again — save as a feedback memory.)        ║
╚════════════════════════════════════════════════════════╝
MSG
    ;;
  deploy)
    cat << 'MSG'
╔══ Action Capture ══════════════════════════════════════╗
║  Deployment command ran.                               ║
║  Consider: is there a procedure, gotcha, or env       ║
║  quirk worth capturing for future deploys?            ║
╚════════════════════════════════════════════════════════╝
MSG
    ;;
  struggle)
    cat << 'MSG'
╔══ Action Capture — Struggle Detected ══════════════════╗
║  Command failed (non-zero exit).                       ║
║  [seed-target] observation auto-written to memory.     ║
║  The nightly skill-augment-agent will use this to      ║
║  seed targeted skill improvements (Sleep phase).       ║
╚════════════════════════════════════════════════════════╝
MSG
    ;;
esac

# REGISTRATION — add to ~/.claude/settings.json PostToolUse array:
# {
#   "matcher": "Bash",
#   "hooks": [{ "type": "command", "command": "bash \"$HOME/.claude/hooks/action-capture.sh\"" }]
# }
