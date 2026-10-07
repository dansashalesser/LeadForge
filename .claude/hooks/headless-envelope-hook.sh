#!/bin/bash
# headless-envelope-hook — SessionStart. Injects a stricter operating envelope,
# but ONLY when the session is an unattended `claude --print` routine run.
#
# Gap this closes: seven unattended entry points (the six scripts/routines/*-runner.sh
# and daily-orchestrator.sh's drift review) all invoke
#   SDD_HEADLESS=1 claude --print --permission-mode bypassPermissions
# so the least-supervised sessions in the harness run with the *widest* permissions.
# Before this hook, SDD_HEADLESS was read only to SUPPRESS interactive behaviour
# (stop-hook.sh, caveman-savings-hook.sh, scripts/utils/dashboard.py) — nothing
# anywhere read it to TIGHTEN behaviour. Interactive sessions have a human at the
# keyboard as the backstop; these have none.
#
# Extracted from the "global rules vs factory rules" separation in the AI-dark-factory
# walkthrough (youtube.com/watch?v=eecUhBpTz_g): rules that apply when a human is
# watching are not the rules that should apply when nobody is. The loop guard comes
# from the same source's stated reason for keeping a human fail-safe — agents "go
# through an infinite loop of trying to fix a problem".
#
# Advisory by construction: SessionStart stdout becomes context, it cannot block.
# Exits 0 always. Silent — truly zero bytes — in every interactive session.
set -u

# Drain stdin so the caller never blocks on a full pipe. Event payload is unused:
# the gate is the environment, not the event.
cat >/dev/null 2>&1 || true

[ "${SDD_HEADLESS:-}" = "1" ] || exit 0

# Opt-out for a routine that legitimately needs the wider envelope (e.g. a future
# runner whose whole job is committing). Set in that runner only, never globally.
[ "${SDD_SKIP_HEADLESS_ENVELOPE:-}" = "1" ] && exit 0

cat <<'ENVELOPE'
=== UNATTENDED RUN — STRICTER ENVELOPE APPLIES ===

This session is a headless routine (`claude --print`, SDD_HEADLESS=1). No human is
reading along and no permission prompt will stop you. These constraints are stricter
than the interactive rules in CLAUDE.md and they override anything looser:

1. ONE UNIT OF WORK. Do exactly what the routine prompt asks. Do not start adjacent
   work you notice along the way — record it in the report instead.
2. NO HISTORY-REWRITING OR PUBLISHING GIT. No `push`, `reset --hard`, `rebase`,
   `--force`, branch or tag deletion. Commit only if the routine prompt explicitly
   instructs you to commit.
3. WRITES STAY IN THE ROUTINE'S OWN LANE. Report files and `.claude/memory/` are
   always fair game. Harness artifacts — `skills/`, `hooks/`, `agents/`, `commands/`,
   `templates/`, `CLAUDE.md`, `settings.json`, `.claude/behaviors/` — are off-limits
   UNLESS the routine prompt explicitly names that artifact class as its output (some
   routines do: repairing SKILL.md files, drafting BEHAVIOR.md specs). When it does,
   stay inside the caps and gates that prompt states, and do not generalise from the
   permission you were given to any other artifact class. Everything else: propose it
   in the report, do not write it.
4. TWO-STRIKE LOOP GUARD. If the same action fails twice, stop retrying it. Write
   `ESCALATION: <what failed, what you tried, what a human should check>` into the
   report and move on to the next item. A third attempt is never the right call here.
5. REPORT HONESTLY. Partial completion is an acceptable outcome; a fabricated one is
   not. If a step was blocked or skipped, say so explicitly and say why. Never widen
   scope or invent findings to make the run look productive.
6. NO NEW DEPENDENCIES. Do not install packages, add MCP servers, or write cron /
   launchd entries. If the routine cannot proceed without one, escalate per rule 4.

Escalating is a successful outcome. Guessing is not.
ENVELOPE

exit 0

# REGISTRATION (settings.json — SessionStart, matcher ""):
# {
#   "matcher": "",
#   "hooks": [
#     { "type": "command", "command": "bash \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/headless-envelope-hook.sh\"" }
#   ]
# }
#
# Gate:     SDD_HEADLESS=1 (set by scripts/routines/*-runner.sh and daily-orchestrator.sh)
# Opt-out:  SDD_SKIP_HEADLESS_ENVELOPE=1 (per-runner, never global)
# Tests:    bash hooks/claude/headless-envelope-hook.test.sh
