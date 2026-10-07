<!-- L0: Cross-session state — current priorities, active specs, key decisions -->

# Hot Memory

<!-- Harness Trust Score: informational only, never gates actions.
     Updated by /kiro:daily-maintenance. See kiro/settings/rules/session-quality-rubric.md. -->
## Harness Trust Score: 20.0% (▬ +0.0 today, 7d: —)

## Current Priorities
<!-- Top 3-5 priorities for the project right now -->
1. Split `specs/lead-source-adapters/` requirements.md (67KB) and design.md (76KB) into
   sub-16KB files before running spec-tasks — otherwise spec-tasks hits the read gate.
2. Fix the subagent read-gate dead end in `.claude/hooks/lean-ctx-nudge-hook.sh`
   (denies Read >=16KB, remedy names ctx_read, which subagents cannot call).
3. Make timed-out/errored detectors emit a suppress-scoring sentinel instead of 0.0.

## Active Specs
<!-- Specs currently in progress: name, phase, next action -->
- **lead-source-adapters** — phase `grill-approved` (requirements/design/grill all
  approved; tasks not generated). Next: `/kiro:spec-tasks`, but blocked on priority 1.
  Files: specs/lead-source-adapters/{requirements,design,research,CONTEXT}.md

## Key Decisions
<!-- Recent decisions that affect ongoing work -->
- Append to `observations.md` only via Bash `printf ... >>`. Write/Edit are blocked by
  `.claude/hooks/ledger-append-only.sh`, and ctx_shell refuses `>>` — Bash is the one
  legal path, despite the general ctx_* preference.
- Spec file size is treated as an authoring-time constraint (<16KB/file), not a
  read-time problem to work around.

## System Notes
<!-- Blockers, context, environment state -->
- Repo is bootstrap-stage: 1 commit (`5b41741 Initial commit`), no app code yet.
  `.claude/`, `specs/`, `CLAUDE.md` are gitignored, so harness work is invisible to git
  and to any git-based metric. Keep-rate cohort is empty (undefined, not 0%).
- Session scoring is unreliable right now: `[detector-down]` (claude --print timed out
  at 120s) means 2026-10-04/05 windows have no [impl]/[debug]/[decision] capture.
  Judge deltas of 0.0 for those days reflect missing instrumentation, not idleness.
- lean-ctx shell allowlist refuses `python3 -c`, `zsh -c`, `find -exec`, and any `>`/`>>`
  redirect in ctx_shell. Use jq / script files / native Bash. `lean-ctx allow <cmd>`
  widens it; the "permanent restriction" wording is not a policy refusal.
- `ctx_read` cannot reach /tmp scratchpads or `~/.claude/skill-library` ("path escapes
  project root"); use native Read or Bash there.
