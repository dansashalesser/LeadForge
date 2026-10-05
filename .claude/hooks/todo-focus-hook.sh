#!/usr/bin/env bash
# todo-focus-hook.sh — PostToolUse, matcher TodoWrite
#
# Enforces one in-progress todo at a time.
#
# Why: TodoWrite accepts any number of concurrent `in_progress` entries and
# enforces nothing. The failure mode is not cosmetic — an agent that marks four
# items in_progress starts all four, splits attention across them, and finishes
# none cleanly. The single-active constraint is the load-bearing part of a todo
# tool; without it the list is a wish list, not a work queue.
#
# Strength: SOFT. The write already happened (PostToolUse) and this hook does not
# undo it. It names the competing items and asks for one to be picked.
#
# Exit code is deliberately 2, not 0. Per the hooks docs, stdout is injected as
# context only for UserPromptSubmit, UserPromptExpansion, SessionStart, and
# PostModelSwitch; for PostToolUse both stdout and (on exit 0) stderr go to the
# debug log where Claude never sees them. Exit 2 is the documented way to surface
# stderr to Claude from PostToolUse — "the tool already ran", so it warns without
# blocking. An `echo` here on exit 0 would be a hook that appears to work and
# silently does nothing.
#
# Parsing note: this reads structured JSON fields with jq. It does NOT pattern
# match prose — .tool_input.todos[].status is emitted as data at the source.
#
# Opt out: SDD_SKIP_TODO_FOCUS=1

set -u

[ "${SDD_SKIP_TODO_FOCUS:-0}" = "1" ] && exit 0
command -v jq >/dev/null 2>&1 || exit 0

INPUT=""
IFS= read -r -d '' -t 2 INPUT || true
[ -n "$INPUT" ] || exit 0

# Guard the matcher in-script too: a mis-scoped registration must be inert, not
# noisy, on unrelated tools.
TOOL="$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)"
[ "$TOOL" = "TodoWrite" ] || exit 0

ACTIVE="$(printf '%s' "$INPUT" \
  | jq -r '[.tool_input.todos[]? | select(.status == "in_progress")] | length' 2>/dev/null || echo 0)"
case "$ACTIVE" in ''|*[!0-9]*) exit 0 ;; esac
[ "$ACTIVE" -gt 1 ] || exit 0

ITEMS="$(printf '%s' "$INPUT" \
  | jq -r '.tool_input.todos[]? | select(.status == "in_progress") | "  - " + (.content // .activeForm // "(unnamed)")' 2>/dev/null || true)"

{
  echo "[todo-focus] $ACTIVE todos are in_progress at once:"
  echo "$ITEMS"
  echo
  echo "Keep exactly one in_progress. Pick the item you are actually working on"
  echo "now, set the others back to pending, and finish before starting the next."
  echo "Parallel in_progress items split attention and none of them land cleanly."
} >&2

exit 2

# REGISTRATION — settings.json
# {
#   "hooks": {
#     "PostToolUse": [
#       {
#         "matcher": "TodoWrite",
#         "hooks": [
#           {
#             "type": "command",
#             "command": "bash \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/todo-focus-hook.sh\""
#           }
#         ]
#       }
#     ]
#   }
# }
