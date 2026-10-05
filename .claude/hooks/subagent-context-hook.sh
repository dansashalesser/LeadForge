#!/usr/bin/env bash
# subagent-context-hook.sh — SubagentStart
#
# Injects the harness's load-bearing conventions DIRECTLY into each spawned
# subagent's context, via JSON `hookSpecificOutput.additionalContext`.
#
# Why this exists: CLAUDE.md, .claude/rules/, and SessionStart hook output are
# parent-thread only — a subagent starts without them and re-derives (or
# violates) conventions the main thread already knows. Before `SubagentStart`
# existed the only available mechanism was to nudge the PARENT at
# `PreToolUse:Agent` and hope it briefed the child (see gbrain-agent-spawn.sh).
# That is a request, not a guarantee. This is the guarantee.
#
# Two implementation constraints, both load-bearing:
#
#   1. Plain stdout is NOT injected as context for this event. Claude Code adds
#      plain-text stdout as context only for UserPromptSubmit,
#      UserPromptExpansion, SessionStart, and PostModelSwitch. Every other hook
#      in this directory uses `cat << 'RULES'`; that would be silently discarded
#      here. This hook must emit JSON on stdout instead.
#   2. NEVER block on stdin. A hook that waits forever on a read that never
#      completes stalls the subagent spawn itself. The read below is bounded and
#      every failure path falls through to injecting anyway — agent_type is used
#      only to tailor the text, so losing it degrades the message rather than
#      the mechanism.
#
# Keep the injected text SHORT. It is prepended to every subagent, so its cost
# is multiplied by spawn count and it competes with the actual task for
# attention. Rules only — no explanations, no examples.
#
# Opt out: SDD_SKIP_SUBAGENT_CONTEXT=1

set -u

[ "${SDD_SKIP_SUBAGENT_CONTEXT:-0}" = "1" ] && exit 0
command -v jq >/dev/null 2>&1 || exit 0

# Bounded stdin read. `read -d ''` consumes to EOF and returns non-zero at EOF
# even on success, so the exit status is deliberately ignored; -t caps the wait
# when stdin is a terminal or is never closed.
INPUT=""
IFS= read -r -d '' -t 2 INPUT || true

AGENT_TYPE=""
if [ -n "$INPUT" ]; then
  AGENT_TYPE="$(printf '%s' "$INPUT" | jq -r '.agent_type // empty' 2>/dev/null || true)"
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

# Handoff pointer only when the snapshot is real and fresh (<24h). A stale
# pointer is worse than none: the child reads state that no longer holds.
HANDOFF=""
HANDOFF_FILE="$PROJECT_DIR/.claude/memory/handoff/latest.md"
if [ -f "$HANDOFF_FILE" ]; then
  # find -mtime -1 is the portable freshness check; no `timeout`/`stat -c` here.
  if [ -n "$(find "$HANDOFF_FILE" -mtime -1 2>/dev/null)" ]; then
    HANDOFF="- Main-session state snapshot: .claude/memory/handoff/latest.md (fresh). Read it before re-deriving project context."
  fi
fi

CONTEXT="$(cat <<CTX
== Harness conventions (injected at subagent start — these are not optional) ==

TOOLS
- Prefer ctx_* MCP tools over native equivalents. Native Grep/Glob are policy-denied.
  Native Read is reserved for the read-before-write edit gate.

BLAST RADIUS (before editing an existing function/class/method — run ONE, in this order)
- Python symbol: mcp__serena__find_referencing_symbols. LSP-accurate, authoritative.
- Anything else: mcp__gitnexus__impact. If the index errors or reports a version
  mismatch, say so and fall back to ctx_callgraph(action="callers"). A broken index
  is an unknown answer, never "no callers".
- Report HIGH/CRITICAL risk instead of proceeding silently.
- After editing any .py file: mcp__serena__get_diagnostics_for_file(path).

PARSING
- Do not parse prose with regex to extract structured facts. Emit structured data
  at the source (JSON, jq) and read fields. This is a hard rule, not a preference.

EVIDENCE
- Never report a task complete without verification evidence produced in this
  run. Hedged future tense ("should work", "looks right") is a tell that the
  check was not run — run it or say plainly that you did not.
- A non-zero exit from a probe (ls, grep, test) is an ANSWER, not a failure.
- Report outcomes faithfully: if a step was skipped or blocked, say so.

SCOPE
- Change size: prefer changes touching <=1 folder/module.
- No shared extraction until 3 real call sites exist.
- Do not commit .claude/, specs/, CLAUDE.md, AGENTS.md, or ERRORS.md — they are
  installed harness output and gitignored. Exception: in sdd-harness itself,
  CLAUDE.md is a committed shared reference (CLAUDE.local.md stays local).
  The top-level source tree IS the product.

REPORTING
- Address the user as "Husband" in any user-facing text you produce.
- End with: Files changed / What changed / Not touched.
$HANDOFF
CTX
)"

jq -cn \
  --arg ctx "$CONTEXT" \
  --arg agent "$AGENT_TYPE" \
  '{
     hookSpecificOutput: {
       hookEventName: "SubagentStart",
       additionalContext: (
         if ($agent | length) > 0
         then "(subagent: " + $agent + ")\n" + $ctx
         else $ctx
         end
       )
     }
   }'

exit 0

# REGISTRATION — settings.json
# {
#   "hooks": {
#     "SubagentStart": [
#       {
#         "hooks": [
#           {
#             "type": "command",
#             "command": "bash \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/subagent-context-hook.sh\""
#           }
#         ]
#       }
#     ]
#   }
# }
#
# No matcher: the conventions above apply to every agent type. To scope it,
# add "matcher" with an agent-type pattern (e.g. "general-purpose|claude").
