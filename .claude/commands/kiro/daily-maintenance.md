---
description: Nightly harness maintenance — judge, reflect, housekeep, update trust score, augment skills
allowed-tools: Read, Write, Edit, Task, Glob, Grep, Bash
---

# Kiro Daily Maintenance — Nightly Orchestrator

Wires existing harness commands + the Judge into one pipeline. Designed to be invoked on a daily schedule via Claude Code [Routines](https://claude.com/blog/introducing-routines-in-claude-code), one Routine per installed project.

Never edits code or specs. Touches `.claude/memory/` and `~/.claude/skills/` only — skills are augmented with session learnings, not rewritten.

## Pre-check

Use Glob to verify `.claude/memory/` exists. If it does not, log `routine-skip: memory-not-bootstrapped` to stdout and exit — this project has not yet run `/kiro:reflect` once, so there is nothing to maintain.

Use Bash to guard against double-runs on the same calendar day:

```bash
today=$(date +%Y-%m-%d)
if grep -q "^- $today \[judge\]:" .claude/memory/observations.md 2>/dev/null; then
  echo "routine-skip: already ran today (idempotent no-op)"
  exit 0
fi
```

If the guard trips, return immediately. The individual steps below are also idempotent, but the pre-check saves work.

## Step 1 — Judge (independent scorer, no proposals) — run 3×

The judge is an LLM at temperature > 0, and its `score_delta` lands in a **cumulative** score that nothing ever revisits. One draw is not a measurement: until this step ran three times, "the score fell 4 points" and "the judge sampled differently" were the same observation. Spawn it **three times** and let Step 4 reconcile them.

Spawn all three in a **single message** so they run concurrently — they are independent and read-only, so there is no ordering constraint and no reason to pay for three sequential round-trips.

```
Task(
  subagent_type="session-judge",
  description="Score last 24h of harness behavior (run 1 of 3)",
  prompt="""
Score the last 24 hours of harness behavior against the rubric.

Window: last 24h (use date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ if needed).

Files to read:
- .claude/kiro/settings/rules/session-quality-rubric.md
- .claude/memory/observations.md
- .claude/memory/trace.log (if present)
- .claude/memory/hot-memory.md

Emit JSON verdict on stdout matching the rubric schema.
Do NOT append a [judge] observation — the caller writes one line covering all
three runs. Do NOT propose fixes. Do NOT edit hot-memory.
"""
)
```

Repeat identically for runs 2 and 3, changing only the `description`. **Do not** tell any run about the others' verdicts, and do not vary the prompt between runs — the point is three independent draws from the same question. Correlating them defeats the purpose as thoroughly as running one.

Only the **caller** appends the `[judge]` observation, one line for all three runs, per Step 5 of the judge protocol. Three subagents each appending their own would triple-count every tag into `trust_score.py auto-score`, which counts tag occurrences.

Capture all three JSON verdicts. Extract each `score_delta`, plus the `summary` from the run whose delta is the **median**. Handle degraded runs honestly:

| Situation | Action |
|---|---|
| All 3 verdicts valid | Pass all three deltas to Step 4 |
| 1–2 verdicts missing or malformed | Log `routine-error: judge-verdict-invalid (N of 3)` as a `[routine-error]` observation, pass **only the valid deltas** to Step 4 |
| All 3 missing or malformed | Log the same `[routine-error]` and pass a single `--delta 0` |

A run that failed is **missing**, not a vote for zero. Substituting 0 for a crashed judge pulls the median toward no-change and manufactures agreement out of a failure.

## Step 2 — Reflect (seeded with Judge's drains)

Use the Task tool to invoke the Subagent:

```
Task(
  subagent_type="reflect-agent",
  description="Mine session for observations and patterns, seeded with Judge drains",
  prompt="""
Review recent session activity and update memory files.

Priority signals from today's session-judge verdict:
<paste Judge verdict JSON here, or "no drains identified" if empty>

Focus the reflection on converting Judge drains into new memory entries or
pattern promotions. Each [memory-gap] observation should either produce a
new memory entry or be explained (if it's a known false positive).

File patterns to read:
- .claude/memory/*.md
- .claude/memory/meta/*.md
- .claude/kiro/settings/rules/memory-conventions.md

Do not touch the Trust Score line in hot-memory.md — /kiro:daily-maintenance
updates it in Step 4.
"""
)
```

## Step 3 — Housekeeping

Use the Task tool to invoke the Subagent:

```
Task(
  subagent_type="housekeeping-agent",
  description="Prune and maintain memory files",
  prompt="""
Perform memory maintenance: archive, prune, validate, index.

File patterns to read:
- .claude/memory/*.md
- .claude/memory/meta/*.md
- .claude/memory/glacier/*.md
- .claude/kiro/settings/rules/memory-conventions.md

Do not touch the Trust Score line in hot-memory.md.
"""
)
```

## Step 4 — Update Trust Score

Pass **every** `score_delta` from Step 1 — one `--delta` per judge run:

```bash
python3 .claude/scripts/trust_score.py apply \
  --delta "<score_delta run 1>" \
  --delta "<score_delta run 2>" \
  --delta "<score_delta run 3>" \
  --summary "<summary from the median run, first 80 chars>"
```

The helper:
- takes the **median** of the samples, so one outlier draw cannot move the score
- records the day as **inconclusive** and applies `0.0` when the samples spread by more than `JUDGE_SPREAD_LIMIT` (2.0 on the ±4.5 scale) — a reading that depends on which draw you looked at is not a reading
- persists the raw `samples`, `spread` and `inconclusive` flag into `trust-score.jsonl`, so an inconclusive day is distinguishable from a day nothing ran
- clamps the applied delta to `[-4.5, +4.5]`
- rewrites the `## Harness Trust Score:` line at the top of `hot-memory.md`
- is idempotent per calendar day (same-day re-runs skip)

A single `--delta` is still accepted and skips the spread gate entirely — it reports `"spread": null`, not `0.0`, because one sample has no spread and claiming otherwise would read as perfect agreement.

Do not interpret or act on the score. It is observability only — never gates behavior. An `"status": "inconclusive"` result is **not** a problem to fix; it is the helper correctly declining to commit a number it cannot stand behind.

## Step 5 — Surface unresolved memory-gaps

## Step 6 — Augment Skills

Use the Task tool to invoke the Subagent:

```
Task(
  subagent_type="skill-augment-agent",
  description="Encode today's session learnings into relevant SKILL.md files",
  prompt="""
Review today's session learnings and augment relevant skills.

Judge verdict from Step 1:
<paste Judge verdict JSON here, or "no verdict" if Step 1 failed>

Today's date: <today's date>

Focus on:
1. Skills invoked during today's sessions (grep trace.log and observations for [skill:*] tags)
2. Judge drains that map to a skill domain (re-explanation → memory skills, gate bypass → verification skills, etc.)
3. Harness-own skills (superpowers:*, kiro context) referenced in today's observations
4. learn-eval **Route** candidates — skill-tied lessons deliberately kept out of memory (look for `- ... [learn-eval] Evaluated ... routed` markers in observations, or "Routed to Skills" lines). Push each into its named skill. This is the skills-over-memory handoff.

Rules:
- Max 3 skills updated
- Append-only — never delete existing content
- Every change must cite a specific observation or drain
- Each addition ≤ 150 chars
"""
)
```

Log the result. If skill-augment-agent errors, log `[routine-error]: skill-augment-agent failed` and continue. Skill augmentation is best-effort — it must never block trust score or gap detection.

## Step 6b — Mine Behavior Specs

Use the Task tool to invoke the Subagent:

```
Task(
  subagent_type="behavior-spec-agent",
  description="Draft/update BEHAVIOR.md conduct specs from recurring evidence",
  prompt="""
Review today's session evidence for recurring agent-conduct patterns and
draft or revise durable BEHAVIOR.md specs under .claude/behaviors/.

Judge verdict from Step 1:
<paste Judge verdict JSON here, or "no verdict" if Step 1 failed>

Today's date: <today's date>

Rules:
- Max 3 specs created/updated
- Every spec cites a judge drain, a type: feedback memory, or a [revert]/[drain]
  observation recurring ≥2 times (type: feedback auto-qualifies at 1 occurrence)
- Validate every spec with .claude/scripts/validate-behavior-spec.py before finalizing
- Never edit ~/.claude/skills/, CLAUDE.md, or any runtime-visible file — .claude/behaviors/ only
"""
)
```

Log the result. If behavior-spec-agent errors, log `[routine-error]: behavior-spec-agent failed` and continue. This step is best-effort — it must never block trust score or gap detection.

## Step 6c — AI-Surface Drift Check

Best-effort, advisory only — see `~/.claude/skill-library/ai-surface-audit/SKILL.md`. Skip silently if
`npx` isn't on PATH or `.claude/memory/.ai-surface-scan.json` doesn't exist yet
(no baseline to diff against — the initial scan is user-invoked, not run here).

```bash
if command -v npx >/dev/null 2>&1 && [ -f .claude/memory/.ai-surface-scan.json ]; then
  npx --yes geiger-scan --strict --json --diff .claude/memory/.ai-surface-scan.json 2>/dev/null
fi
```

If the diff shows a new capability on an existing server or a new server
entirely, append a `[routine-alert]` observation naming it — otherwise stay
silent. Never fails the pipeline: any `geiger-scan` error is logged as
`[routine-error]: ai-surface-drift failed` and skipped.

## Step 7 — Skill Eval Staleness

`skill-eval-gate` measures a skill's lift once and writes `eval-verdict.json`
beside its `SKILL.md`. That verdict is a joint fact about the instructions *and*
the model that read them. `skill-validate-hook.sh` catches the first half — an
edited `SKILL.md` — because a write is something a hook can fire on. A model
change invalidates every verdict at once with no write anywhere, so it needs a
scan on the tick instead.

Run it with the exact model ID **you are running under**, taken from your own
system prompt, not from a config file:

```bash
python3 .claude/scripts/skill-eval-staleness.py --current-model "<your exact model ID>"
```

There is no default and no auto-detection on purpose: a wrong guess marks every
stale verdict as current, which is the one outcome this step exists to prevent.
If you genuinely cannot name your model ID, log
`[routine-error]: skill-eval-staleness skipped — model ID unknown` and move on.
Do not pass a plausible-looking value.

If the scan reports any flagged verdicts, append one observation:

```
- YYYY-MM-DD [routine-alert]: N skill eval verdicts stale (M stale-model, K unknown-model, J hash-mismatch) — re-run skill-eval-gate before trusting their lift
```

Do **not** re-run `skill-eval-gate` from here, and do not touch the flagged
skills. Re-measuring one skill costs 12 agent spawns; doing it unattended for
every skill on a model-change day would be the most expensive thing the nightly
routine has ever done. This step reports; the user decides what is worth
re-measuring.

Skills with no `eval-verdict.json` at all are counted but never flagged — most
installed skills are vendored and never went through the gate, so treating
absence as a finding would bury the real ones.

Best-effort like the steps above: on a non-zero exit other than a finding, log
`[routine-error]: skill-eval-staleness failed` and continue.

---



Scan for `[memory-gap]` observations from the last 24h that have no corresponding new memory entry produced by Step 2:

```bash
# Gaps flagged today:
grep "^- $(date +%Y-%m-%d) \[memory-gap\]:" .claude/memory/observations.md 2>/dev/null | \
  wc -l
```

If the count is > 0 and Step 2's output shows no new memory entries on the same topics, append a single `[routine-alert]` observation so the user sees it next session:

```
- YYYY-MM-DD [routine-alert]: N memory-gaps unresolved — re-explanation drain is compounding
```

## Error Isolation

Each step runs in its own Task invocation. If Step 1 or Step 2 fails:
- Log a `[routine-error]` observation with the step name and error summary
- **Continue** to the remaining steps. A bad Judge pass must not block housekeeping.

The only fatal condition is a missing `.claude/memory/` — covered by the pre-check.

## Display Result

Print a brief summary to stdout (visible to the developer next session):

```
Daily Maintenance complete — YYYY-MM-DD
- Judge: delta=-1.0 (2 charges, 3 drains). Summary: "…"
- Reflect: +2 observations, 0 patterns promoted
- Housekeeping: 0 archived, hot-memory 34/50 lines
- Trust Score: 42.3% (▼ -1.0 today, 7d: ▲ +3.1)
- Unresolved memory-gaps: 1 → [routine-alert] appended
- Skills augmented: 2 (brainstorming: +1 anti-pattern, systematic-debugging: +1 learned pattern)
- Behavior specs: 1 (verify-before-claiming-done: created — evidence: 2 recurring judge drains)
- Eval staleness: 3 verdicts flagged (2 stale-model, 1 hash-mismatch), 41 never gated
```

## Notes

- Intended cadence: **nightly, per repo** — registered by `install.sh` as a Claude Code Routine.
- Manual invocation (`/kiro:daily-maintenance`) is fine; the same-day guard makes it safe.
- Same-day guard uses the `[judge]` observation as the sentinel. Deleting that entry re-enables the pipeline (intentional escape hatch).
- See `docs/harness-documentation/SDD-USAGE.md` — "Daily Maintenance" section for the full model.
