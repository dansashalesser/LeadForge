#!/usr/bin/env bash
# claudemd-edit-notice.sh — PostToolUse (Write|Edit|MultiEdit)
#
# Warns that a just-written CLAUDE.md / AGENTS.md edit is NOT active in the
# current session.
#
# Why this exists: project-root and user-level CLAUDE.md (and AGENTS.md) are
# read once at session start and held in memory. Editing one mid-session
# changes the file on disk and changes nothing about the running session —
# the new rule is not loaded until /clear, /compact, or a restart.
#
# That gap is not theoretical here. `harness-fix-agent`, `skill-augment-agent`,
# `claudemd-review` and `/kiro:evolve` all write CLAUDE.md mid-session, then
# continue as if the rule they just wrote is in force. It is not. The agent
# cannot observe its own stale context, so no prompt or skill can catch this —
# only something outside the model's context can say so.
#
# Strength: soft. The write already happened; this does not undo it.
#
# Exit code is 2, deliberately. For PostToolUse, stdout goes to the debug log
# and stderr on exit 0 is never shown to Claude. Exit 2 is the documented way
# to surface stderr from this event — the tool already ran, so it warns without
# blocking. An `echo` on exit 0 here would be a hook that appears to work and
# does nothing.
#
# Parsing: reads `.tool_input.file_path` with jq and compares the basename to a
# literal list. Structured fields and exact tokens only — no text matching.
#
# Opt out: SDD_SKIP_CLAUDEMD_NOTICE=1

set -u

[ "${SDD_SKIP_CLAUDEMD_NOTICE:-0}" = "1" ] && exit 0
command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)
[ -n "$INPUT" ] || exit 0

FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // ""' 2>/dev/null)
[ -n "$FILE_PATH" ] || exit 0

BASE=$(basename "$FILE_PATH")

case "$BASE" in
  CLAUDE.md|CLAUDE.local.md|AGENTS.md) ;;
  *) exit 0 ;;
esac

{
  echo "[claudemd-edit-notice] $BASE was edited — the change is NOT active in this session."
  echo "  Instruction files are read once at session start and held in memory. The file on"
  echo "  disk now differs from the rules this session is running under."
  echo "  Load it: /compact, /clear, or restart. Until then, do not rely on the new rule,"
  echo "  and do not report it as in effect."
} >&2

exit 2

# REGISTRATION (templates/settings.json.template and .claude/settings.json)
#
#   "PostToolUse": [
#     {
#       "matcher": "Write|Edit|MultiEdit",
#       "hooks": [
#         {
#           "type": "command",
#           "command": "bash \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/claudemd-edit-notice.sh\""
#         }
#       ]
#     }
#   ]
