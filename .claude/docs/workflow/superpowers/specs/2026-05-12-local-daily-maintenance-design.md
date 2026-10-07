# Local Daily Maintenance — Design

**Date:** 2026-05-12
**Status:** Approved, ready for implementation plan
**Replaces:** CCR-based `Kiro Daily Maintenance`, `Session Quality Assessor`, `Keep Rate Evaluator` routines

## Problem

The harness has three cloud routines (`Kiro Daily Maintenance`, `Session Quality Assessor`, `Keep Rate Evaluator`) that were intended to auto-improve each installed repo's memory and harness state. Two failures stop this from working:

1. **CCR is cloud-only** — it clones the GitHub repo but cannot see local-only state. `.claude/memory/` is gitignored (and must stay so — this is production code shared with teammates), so the routines no-op every night via the `memory-not-bootstrapped` guard. Three routines have been auto-disabled after repeated failures.
2. **trust-battery's stop-hook wiring was designed but never installed.** `docs/trust-battery/README.md` describes a stop-hook that runs `detect_reexplanation.py` after every session. The actual `hooks/stop-hook.sh` does not invoke it. The flagship drain signal (re-explanation detection) is silently broken.

## Goal

Replace the cloud routines with a **local, per-repo daily maintenance loop** that:

- Runs daily at 11:30 IST on the user's machine
- Catches up automatically if the laptop was off at 11:30
- Keeps each repo's memory fully isolated and local (never pushed)
- Treats the harness as a template: each installed repo is self-contained and works in isolation
- Fixes the missing trust-battery stop-hook wiring as part of the same change

## Decisions

| Question | Decision | Reason |
|---|---|---|
| Trigger mechanism | Windows Task Scheduler + session-start hook (belt + suspenders) | Task Scheduler handles the daily case; session-start hook handles missed runs without depending on Windows wake behavior |
| Which routines | Daily-maintenance + session-quality + keep-rate, **all chained, daily at 11:30 IST** | One scheduling surface, one timestamp, one log; keep-rate is cheap to run daily even if it changes slowly |
| Loop model | One global orchestrator reads `projects.txt`, calls each repo's local runner | Adding/removing a repo from `projects.txt` is the only operation needed to change coverage |
| install.sh scope | Installs hooks + per-repo runner + registers in `projects.txt`; separate one-time `setup-global-orchestrator.sh` for Task Scheduler | Keeps install.sh idempotent and decoupled from Windows-side wiring |
| Memory location | Stays gitignored, local-only | Production code is shared with teammates; personal memory must not leak |
| Memory commits | None — no git operations inside daily-runner | Avoids polluting project history; `observations.md` timestamps already provide an audit trail |
| Catch-up policy | Fire if `>24h` since last run; collapse missed days to one | Simpler, matches "what's current state" intent over per-day score deltas |

## Component Architecture

### Per-repo (installed by `install.sh` into each project's `.claude/`)

```
<project>/.claude/
  scripts/
    daily-runner.sh                  NEW      runs this repo's daily maintenance pipeline
    daily-maintenance-prompt.md      NEW      the prompt body sent to claude --print — instructs the model to invoke /kiro:daily-maintenance, then run the session-quality skill workflow, then run the keep-rate skill workflow. Because the runner executes locally, the model has direct access to ~/.claude/skills/, so the prompt does not embed those workflows inline — it references them by skill name.
    trust_score.py                   EXISTING
    detect_reexplanation.py          EXISTING
  hooks/
    session-start-hook.sh            MODIFIED add catch-up trigger
    stop-hook.sh                     MODIFIED add detect_reexplanation call (fixes trust-battery bug)
  memory/
    .last-routine-run                NEW      ISO timestamp, gitignored
```

### Harness master (lives at `~/.claude/sdd-harness/`)

```
~/.claude/sdd-harness/
  scripts/
    daily-orchestrator.sh            NEW      reads projects.txt, calls each repo's daily-runner.sh
    setup-global-orchestrator.sh     NEW      one-time Windows Task Scheduler bootstrap
  logs/
    orchestrator.log                 NEW      rolling cross-repo run log
  hooks/
    stop-hook.sh                     MODIFIED template — same change as per-repo
    session-start-hook.sh            MODIFIED template — same change as per-repo
  install.sh                         MODIFIED copies the new scripts; appends repo to projects.txt (unchanged)
```

### Isolation property

Each repo's `.claude/scripts/daily-runner.sh` is fully self-contained. Running `cd <repo> && bash .claude/scripts/daily-runner.sh` executes that repo's full daily maintenance with no dependency on `projects.txt`, the orchestrator, or any harness master file. The orchestrator is only a convenience loop on top of N independent runners.

## Data Flow

### Normal day (laptop on, scheduler fires)

```
11:30 IST  Windows Task Scheduler triggers
   └─► wsl.exe bash ~/.claude/sdd-harness/scripts/daily-orchestrator.sh
        ├─► reads projects.txt
        └─► for each repo:
             ├─► [ -d "<repo>/.claude" ] || log [orphan] and skip
             ├─► flock -n <repo>/.claude/memory/.last-routine-run
             ├─► read .last-routine-run; if dated today: skip with "already ran"
             └─► else: bash <repo>/.claude/scripts/daily-runner.sh
                  ├─► write today's date to .last-routine-run (at START, not end)
                  ├─► claude --print < .claude/scripts/daily-maintenance-prompt.md
                  │    ├─► Step A: /kiro:daily-maintenance
                  │    │           (Judge → Reflect → Housekeep → TrustScore → Alert)
                  │    ├─► Step B: session-quality skill workflow
                  │    │           (appends [session-quality] observation)
                  │    └─► Step C: keep-rate skill workflow
                  │                (appends [keep-rate] observation)
                  │        (all writes go to .claude/memory/*; no git ops)
                  └─► exit code; orchestrator logs to orchestrator.log and continues
```

### Catch-up (laptop off at 11:30, user opens Claude later)

```
User opens Claude in <repo>
   └─► session-start-hook.sh fires
        ├─► existing logic (gitnexus check, etc.)
        └─► NEW catch-up block:
             ├─► read .claude/memory/.last-routine-run
             ├─► if absent or >24h old:
             │    └─► nohup bash .claude/scripts/daily-runner.sh > /dev/null 2>&1 &
             │        (background, doesn't block session start)
             └─► hook returns immediately
```

### Continuous re-explanation detection (independent of daily loop)

```
Claude session ends in <repo>
   └─► stop-hook.sh fires
        ├─► existing checks (harness update notice, obs count nudge)
        └─► NEW: python3 .claude/scripts/detect_reexplanation.py --auto-transcript >> .claude/memory/observations.md
             ├─► scans today's user turns for "I already told you"-class phrases
             └─► appends [memory-gap] observation once per day (script already idempotent)
```

`[memory-gap]` entries appended by stop-hook are consumed by the next daily-runner's Judge step, then converted into memory updates by the Reflector. Closes the loop the docs describe but the code never wired.

## Concurrency & State

**Single source of truth:** `.claude/memory/.last-routine-run` (per repo).

- Written at the **start** of `daily-runner.sh`, not the end. A second invocation sees today's date and exits in <1s.
- Tradeoff: if the runner crashes after writing the timestamp but before completing, that repo is skipped for the day. Acceptable — next day catches up, any error is logged.

**Race protection:** `flock -n` on the timestamp file at runner entry. Second concurrent process gets a non-zero exit and bails immediately.

## Logging

| Stream | Location | Format |
|---|---|---|
| Per-repo run results | `<repo>/.claude/memory/observations.md` | `- YYYY-MM-DD [routine-*]: ...` (existing pattern) |
| Cross-repo orchestrator | `~/.claude/sdd-harness/logs/orchestrator.log` | `YYYY-MM-DDTHH:MM:SSZ <repo> <exit_code> <duration_s>` |
| Stop-hook detector | `<repo>/.claude/memory/observations.md` | `- YYYY-MM-DD [memory-gap]: ...` (existing format from `detect_reexplanation.py`) |

Errors do not propagate across repos: one repo's failure logs and the orchestrator moves on. Matches existing error-isolation pattern in `commands/kiro/daily-maintenance.md`.

## install.sh Changes

Minimal additions to the existing flow:

1. Copy `scripts/daily-runner.sh` and `scripts/daily-maintenance-prompt.md` into `<project>/.claude/scripts/`
2. Use the modified `stop-hook.sh` and `session-start-hook.sh` templates (already copied; just need new content)
3. Add `.last-routine-run` to the project's `.claude/.gitignore` if not already covered by `.claude/memory/` blanket ignore
4. Print a hint at the end pointing to `setup-global-orchestrator.sh` for the one-time Task Scheduler wiring

`install.sh` does **not** create the Task Scheduler entry. That is a separate, one-time, user-invoked step:

```bash
bash ~/.claude/sdd-harness/scripts/setup-global-orchestrator.sh
```

This script generates a Windows Task Scheduler XML and imports it via `schtasks.exe /Create /XML ...` from WSL. The task is named `SDD Daily Orchestrator`, triggers daily at 11:30 local, has "run as soon as possible after a missed start" enabled, and executes `wsl.exe bash ~/.claude/sdd-harness/scripts/daily-orchestrator.sh`. Idempotent — re-running updates the existing task rather than duplicating.

## Testing Strategy

| Unit | Standalone test |
|---|---|
| `daily-runner.sh` | `cd <any-repo> && bash .claude/scripts/daily-runner.sh` — runs maintenance, writes timestamp |
| `daily-orchestrator.sh --dry-run` | Prints which repos it would visit and their `.last-routine-run` state |
| Session-start catch-up | `touch -d '2 days ago' .claude/memory/.last-routine-run && open Claude` → should background-fire runner |
| `detect_reexplanation.py` integration | `echo "I already told you to use X" \| python3 .claude/scripts/detect_reexplanation.py --stdin` |
| Task Scheduler entry | `schtasks.exe /Run /TN "SDD Daily Orchestrator"` |

## Failure Modes

| Mode | Behavior |
|---|---|
| `claude --print` fails for one repo | Per-repo try-block, logged to `orchestrator.log`; `.last-routine-run` not updated → next trigger retries; other repos unaffected |
| WSL not running when Task Scheduler fires | Task Scheduler "run after missed" handles next boot; session-start hook is the second safety net |
| Two runners race for same repo | `flock` rejects second; if flock unavailable, date-check in runner short-circuits within 1s |
| `detect_reexplanation.py` crashes in stop-hook | Wrapped in `\|\| true`; stop-hook never blocks session end |
| Repo deleted but still in projects.txt | Orchestrator logs `[orphan]` and skips; user prunes manually (or `--prune` flag — out of scope for v1) |
| Memory file corrupted | Existing `[judge]` pre-check in daily-maintenance prompt handles re-entry; next day recovers |
| Anthropic auth expired | `claude --print` fails; logged; user re-auths on next interactive session |

## DST & WSL2 Specifics

- **DST**: Task Scheduler uses Windows local time. 11:30 IST stays 11:30 IST across transitions.
- **WSL2 cold start**: `wsl.exe bash ...` cold-starts WSL if it had idled out. Adds ~5s overhead. Acceptable.
- **WSL2 shutdown with Windows**: This is exactly the failure mode the session-start hook fallback covers.

## Out of Scope

Explicitly deferred / not built:

- Cross-repo aggregation (one trust score across all repos) — non-goal per `docs/trust-battery/README.md`
- Pushing memory to a private mirror repo (Option A from the brainstorming discussion) — chose local-only Option B
- Automatically deleting the existing CCR routines — user disables/deletes manually after local loop is verified
- Re-running historical missed days as separate score entries — chose "collapse to one run"
- `daily-orchestrator.sh --prune` for orphan repos — manual edit of `projects.txt` is the v1 path
