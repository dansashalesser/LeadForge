#!/bin/bash
# Hard block on Write/Edit/MultiEdit against the harness's own measurement
# ledgers — the files the agent both computes and stores its own scores in.
#
# A self-scored metric stored in an agent-editable file is not a measurement.
# exo (an autonomous-agent harness)'s one safety invariant is that its agent
# cannot alter its own canonical event log; this harness had the opposite
# property until this hook — protected-path-hook.sh guards secrets (.env,
# keys, credentials) but has no entry for any memory file, and
# trust-score.jsonl / metrics.jsonl / caveman-savings.jsonl / learnings.jsonl
# / observations.md were all agent-writable via Write/Edit.
#
# This is a HARD block (exit 2), same convention as
# git-destructive-guard-hook.sh — protected-path-hook.sh is a soft nudge that
# only asks Claude to confirm; this refuses outright. It does not block
# appends done the way every producer in this repo already does them: `>>`
# / `echo` from a Bash-run hook script. Those are a different tool
# (Bash, not Write/Edit/MultiEdit) and this hook's matcher never sees them.
# What it blocks is Claude's own Write/Edit/MultiEdit tool calls against
# these exact files — the path that would let an agent quietly rewrite or
# truncate its own history.
#
# ESCAPE HATCH: housekeeping-agent and /kiro:housekeeping legitimately prune
# and archive these files. SDD_LEDGER_ROTATE=1 (set by the housekeeping
# runner, never by an ordinary agent turn) disables the block for that one
# invocation. This is an env gate, not a soft warn, on purpose — see the
# "Design question" this hook shipped with: a warn can be talked past in the
# same turn that is trying to rewrite the ledger; an env gate the agent does
# not control cannot.
set -euo pipefail

[[ "${SDD_LEDGER_ROTATE:-0}" == "1" ]] && exit 0

EVENT=$(cat)

# Literal suffix match only — no regex, per repo-wide ban (ruff.toml TID251).
PROTECTED_LEDGERS=(
    ".claude/memory/trust-score.jsonl"
    ".claude/memory/metrics.jsonl"
    ".claude/memory/caveman-savings.jsonl"
    ".claude/memory/learnings.jsonl"
    ".claude/memory/observations.md"
)

# FAIL-CLOSED: if python3 is missing or the event is malformed, the parse exits
# non-zero. That used to collapse to an empty string and `exit 0` — a broken
# interpreter silently disabled the block. Now a failed parse blocks whenever
# the raw event names a protected ledger at all; events that don't are allowed,
# so a broken python3 does not block every Write. (Perplexity, "How we engineer
# safer agents", 2026-09-29: a safeguard that can be skipped is not one.)
fail_closed() {
    local ledger
    for ledger in "${PROTECTED_LEDGERS[@]}"; do
        if [[ "$EVENT" == *"$ledger"* ]]; then
            echo "BLOCKED: ledger-append-only.sh could not verify this edit" >&2
            echo "Reason: $1" >&2
            echo "The event mentions protected ledger $ledger; fix the guard's environment (python3 on PATH, well-formed event)." >&2
            exit 2
        fi
    done
    exit 0
}

TOOL_NAME=$(echo "$EVENT" | python3 -c "
import json, sys
e = json.load(sys.stdin)
print(e.get('tool_name', ''))
" 2>/dev/null) || fail_closed "could not parse the hook event (python3 missing or malformed JSON)"

case "$TOOL_NAME" in
    Write|Edit|MultiEdit) ;;
    *) exit 0 ;;
esac

FILE_PATH=$(echo "$EVENT" | python3 -c "
import json, sys
e = json.load(sys.stdin)
inp = e.get('tool_input', {})
print(inp.get('file_path', inp.get('path', '')))
" 2>/dev/null) || fail_closed "could not parse tool_input from the hook event"

[[ -z "$FILE_PATH" ]] && exit 0

MATCHED=""
for ledger in "${PROTECTED_LEDGERS[@]}"; do
    if [[ "$FILE_PATH" == *"$ledger" ]]; then
        MATCHED="$ledger"
        break
    fi
done

[[ -z "$MATCHED" ]] && exit 0

echo "BLOCKED: $TOOL_NAME refused on append-only measurement ledger by ledger-append-only.sh" >&2
echo "File: $FILE_PATH" >&2
echo "This ledger is written by append (>>) from hook/routine scripts only —" >&2
echo "never through Write/Edit/MultiEdit. A self-scored metric stored in an" >&2
echo "agent-editable file is not a measurement." >&2
echo "Legitimate pruning/archival: run via housekeeping-agent /kiro:housekeeping," >&2
echo "which sets SDD_LEDGER_ROTATE=1 for that invocation." >&2
exit 2

# REGISTRATION — add to .claude/settings.json PreToolUse array:
# {
#   "matcher": "Write|Edit|MultiEdit",
#   "hooks": [{ "type": "command", "command": "bash \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/ledger-append-only.sh\"" }]
# }
