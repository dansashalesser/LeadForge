#!/bin/bash
# PreToolUse(Skill) — resolve Library-tier skills the Skill tool cannot see.
#
# scripts/setup/sync-skills.sh installs every skill named in
# scripts/setup/skill-library.txt to ~/.claude/skill-library/, which is NOT in
# the session's skill index — so Skill("<name>") on one of them fails as
# "unknown skill" even though the skill is installed. Any bare-name invocation
# (a command, another skill, a headless prompt) used to dead-end there; the
# spec approval gates lost proof-collaborative-review that way (2026-10-04).
#
# Listed skill (user or project) → allow. Library skill → block with the path
# to read instead (exit 2: stderr is fed back to Claude). Anything else → allow,
# so the Skill tool reports a genuinely missing skill itself.
set -u

EVENT=$(cat)

SKILL_NAME=$(printf '%s' "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
if e.get('tool_name') != 'Skill':
    sys.exit(0)
print((e.get('tool_input') or {}).get('skill', ''))
" 2>/dev/null)

# Plugin skills ("plugin:name") never live in the library.
case "$SKILL_NAME" in
  ""|*:*|*/*|.*) exit 0 ;;
esac

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
[ -f "$HOME/.claude/skills/$SKILL_NAME/SKILL.md" ] && exit 0
[ -f "$PROJECT_DIR/.claude/skills/$SKILL_NAME/SKILL.md" ] && exit 0

LIB_SKILL="$HOME/.claude/skill-library/$SKILL_NAME/SKILL.md"
[ -f "$LIB_SKILL" ] || exit 0

cat >&2 <<MSG
[skill-library] '$SKILL_NAME' is installed as a Library-tier skill — it is not in
the Skill tool's index, so this call cannot resolve. It is NOT missing.
Read $LIB_SKILL and follow it directly
(relative paths inside it resolve against $HOME/.claude/skill-library/$SKILL_NAME/).
MSG
exit 2
