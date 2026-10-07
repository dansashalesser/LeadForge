You are running the local daily maintenance loop for this repository. This invocation runs LOCALLY (not in Anthropic cloud), so you have access to:

- `~/.claude/skills/` via the Skill tool (listed tier only — the `Skill()` tool resolves nothing in `~/.claude/skill-library/`; read those by path instead)
- The full repo file tree (you are already in the repo's working directory)
- All slash commands defined in `.claude/commands/`

Today's date: TODAY_PLACEHOLDER

Execute the seven steps below in order. Each is error-isolated — if one step fails, log the failure and continue to the next.

## Step A — Daily Maintenance (trust-battery loop)

Read `.claude/commands/kiro/daily-maintenance.md` and execute its pipeline:
1. Judge — score the last 24h of observations using `kiro/settings/rules/session-quality-rubric.md`. Write **one** `[judge]` observation covering the run, but **do NOT call `trust_score.py apply`** — scoring is handled in Step D (after session-quality and keep-rate are written). `daily-maintenance.md` runs the judge three times and reconciles the deltas by median before calling `apply`; that reconciliation belongs to `apply`, which this routine does not use, so its three-sample spread gate never applies here. Whatever the run count, only one `[judge]` line is appended — Step D's `auto-score` counts tag occurrences, so a second appended `[judge]` line double-counts every tag it cites.
2. Reflect — convert drains (especially [memory-gap] entries) into memory updates
3. Housekeep — archive observations.md if >50 entries
4. Alert — append `[routine-alert]` if any [memory-gap] entries remain unresolved after reflect

The pre-check at the top of daily-maintenance.md skips if today's `[judge]:` entry already exists. Respect that.

## Step B — Session Quality Assessment

Invoke the `session-quality` skill via the Skill tool. Apply its workflow:
- Collect today's git activity (commits, reverts, file rework counts)
- Score the session 1–5 based on charges vs drains
- Append a single `[session-quality]` observation to `.claude/memory/observations.md`

If today's `[session-quality]:` line already exists, skip silently.

## Step C — Keep Rate Evaluation

Invoke the `keep-rate` skill via the Skill tool. Apply its workflow:
- Find Claude-co-authored commits older than 7 days
- For each, compute lines added vs lines still in HEAD
- Append a single `[keep-rate]` observation with the overall %, trend, and any low-keep-rate flag

If today's `[keep-rate]:` line already exists, skip silently.

## Step D — Trust Score

Run `python3 .claude/scripts/trust_score.py auto-score` from the repo root.

**Must run AFTER Steps B and C** so today's `[session-quality]` and `[keep-rate]` observations are visible.
Reads `observations.md` directly and scores mechanically from tagged signals
(`[session-charge]`, `[memory-gap]`, `[session-quality]`, `[keep-rate]`).
Idempotent — running it twice on the same day is safe.
Deterministic, so it submits its total as a **single** sample: the multi-sample
spread gate is skipped and the record reports `"spread": null`, not `0.0`.

## Step E — Skill Augmentation (Sleep-Phase Knowledge Seeding)

Invoke the `skill-augment-agent` via the Agent tool. Pass this as the task prompt:

```
Augment skills from today's session.
Date: TODAY_PLACEHOLDER
Judge verdict: <paste the full judge JSON from Step A, or "no verdict" if Step A was skipped>
```

This is the Sleep-phase Knowledge Seeding step: converts today's drains **and any `[seed-target:]` observations** (auto-written by the action-capture hook during the Wake phase) into targeted, evidence-backed skill improvements. The agent will:
1. Collect `[seed-target:]` observations as additional evidence alongside judge drains
2. Run the Dreaming phase to generate synthetic worked examples for each gap
3. Address all `[seed-target:]` observations from TODAY_PLACEHOLDER, logging each as `[skill-update]`. Stop when none remain unaddressed; max 5 updates as circuit breaker.

Idempotent: if `[skill-update]:` entries already exist for TODAY_PLACEHOLDER, the agent exits without duplicate writes.

If Step A failed and no judge verdict is available, pass "no verdict" — the agent falls back to `[seed-target:]` observations only.

## Step F — Skill Update Adversarial Check (Maker/Checker Gate)

Check if any `[skill-update]:` entries were written for TODAY_PLACEHOLDER:
- If none exist (Step E wrote nothing or was skipped), skip silently.
- If entries exist, spawn a verification agent via the Agent tool with this task:

```
Review today's skill updates in .claude/memory/observations.md (entries tagged [skill-update] for TODAY_PLACEHOLDER).
For each update:
1. Find the skill file it modified and read the changed content.
2. Find the [seed-target] or [memory-gap] observation that motivated the update.
3. Ask: does the update actually address the gap? Does it contradict existing guidance in the same skill?
If an update fails either check: append to observations.md as `[skill-update-flagged]: <skill-name> — <reason>`.
If all updates pass: append `[skill-update-verified]: N updates passed adversarial check (TODAY_PLACEHOLDER)`.
Be skeptical. Default to flagging if uncertain. You are the checker; Step E was the maker — you share no loyalty to its output.
```

Idempotent: if `[skill-update-verified]:` or `[skill-update-flagged]:` entries already exist for TODAY_PLACEHOLDER, skip silently.

## Step G — Behavior Spec Mining

Invoke the `behavior-spec-agent` via the Agent tool. Pass this as the task prompt:

```
Draft/update BEHAVIOR.md conduct specs from recurring evidence.
Date: TODAY_PLACEHOLDER
Judge verdict: <paste the full judge JSON from Step A, or "no verdict" if Step A was skipped>
```

This is the process-focused sibling of Step E: instead of augmenting runtime-visible `SKILL.md` content, it drafts or revises durable `.claude/behaviors/<name>/BEHAVIOR.md` specs — answer-key material for grading future trajectories, never shown to the agent being graded. It reads today's judge drains, `type: feedback` memories, and `[revert]`/`[drain]` observations, requires the same conduct class to recur ≥2 times (a `type: feedback` memory auto-qualifies at 1), validates every spec with `.claude/scripts/validate-behavior-spec.py` before finalizing, and caps at 3 specs/run. Logs each as `[behavior-update]`.

Idempotent: if `[behavior-update]:` entries already exist for TODAY_PLACEHOLDER, the agent exits without duplicate writes.

If Step A failed and no judge verdict is available, pass "no verdict" — the agent falls back to feedback memories and observations only.

## Output

When all seven steps are done, emit a single summary line on stdout:

```
Daily maintenance complete: judge=<delta> session-quality=<N/5> keep-rate=<N%> trust-score=<score>% skill-updates=<N> skill-check=<passed|flagged|skipped> behavior-specs=<N>
```

If any step was skipped or failed, replace the value with `skipped` or `failed`.

_Last synced: 2026-10-01_
