---
description: Twice-weekly macro-eval sweep — clusters recurring failure patterns across Raindrop Workshop traces, ranks by impact, backward-traces suspects, writes a report and posts annotations.
allowed-tools: Skill, Read, Write, Bash, mcp__raindrop__query_traces, mcp__raindrop__get_run_outline, mcp__raindrop__get_current_run, mcp__raindrop__search_run, mcp__raindrop__get_span_payload, mcp__raindrop__annotate
argument-hint: "[days-back] [name-filter]   (defaults: 4 days, all runs)"
---

# Macro-Eval Sweep

Run the `evaluation/macro` methodology automatically over recent Raindrop Workshop traces and surface recurring failure patterns. This is the **macro** layer (population patterns), not per-run grading.

**Arguments** (`$ARGUMENTS`): optional `[days-back]` (default `4`) and optional `[name-filter]` SQL `LIKE` fragment matched against `runs_with_hints.name` / `event_name` to scope to one system (default: all runs in the window).

## Step 0 — Preflight (fail loud, never silent)

The Raindrop MCP server is interactively authenticated and may be **absent in headless runs**. Before anything else, confirm it is reachable:

```
mcp__raindrop__query_traces  →  SELECT COUNT(*) AS n FROM runs_with_hints;
```

- If the call errors or the tool is unavailable → **stop**. Write a one-line report file at `.claude/reports/macro-evals/<TODAY>-SKIPPED.md` stating "Raindrop MCP unreachable in this run context" and exit. Do **not** pretend success.
- If `n == 0` → write `<TODAY>-EMPTY.md` noting no traces yet, and exit.

## Step 1 — Load the methodology

Read `~/.claude/skill-library/evaluation/macro/SKILL.md` and follow its five-phase workflow. The phases below bind that methodology to Raindrop's tables. The full SQL/scoring reference is in `~/.claude/skill-library/evaluation/references/pipeline-reference.md`.

## Step 2 — Population window (Phase 1)

Pull the population. Default window = 4 days (or `[days-back]`):

```sql
SELECT id, name, event_name, started_at, finished, span_count, live_event_count, metadata
FROM runs_with_hints
WHERE started_at >= datetime('now', '-<DAYS> days')
  -- AND (name LIKE '%<FILTER>%' OR event_name LIKE '%<FILTER>%')   ← add only if name-filter given
ORDER BY started_at DESC
LIMIT 1000;
```

If the population is < 12 runs, note "below density threshold — results indicative only" in the report but continue.

Then pull per-run failure signal:

```sql
SELECT run_id,
       COUNT(*) AS spans,
       SUM(CASE WHEN status NOT IN ('ok','OK','') THEN 1 ELSE 0 END) AS error_spans,
       SUM(CASE WHEN span_type='tool' THEN 1 ELSE 0 END) AS tool_calls
FROM spans
WHERE run_id IN (<window run ids>)
GROUP BY run_id;
```

## Step 3 — Trace documents + grouping (Phases 2–3)

For each run derive: `run_outcome` (finished & no error_spans → `successful_completion`; error_spans>0 → `hard_failure`; not finished / awaiting markers → `review_escalation`), plus a short `state_digest` from the ordered span `name|span_type|status` sequence (use `get_run_outline` on the runs that have error_spans to get the span list cheaply — don't dump full payloads).

Build one compressed trace document per run. Then **group** the documents into named behavior patterns by reading them (LLM grouping pass — the population is small enough that a clustering stack is unnecessary). Emit `behavior_pattern → [run_ids]` with a one-line rationale per group.

## Step 4 — Impact leaderboard (Phase 4)

Severity weights: `successful_completion=1.0`, `review_escalation=2.0`, `hard_failure=2.5`. For each pattern:

```
impact_score = prevalence_share × Σ(severity_weight over members)
```

Rank patterns descending. Successful-only patterns are context, not findings.

## Step 4b — Consistency Gap

Follow `evaluation/macro`'s "Consistency Gap" section. Group the window's runs by task
signature (same invoked skill/command + same or near-identical initial request, not just
same behavior_pattern cluster). For any signature with ≥3 runs, compute:

```
mean_at_k  = pass_count / total_runs
pass_hat_k = 1 if every run of this signature passed else 0
gap        = mean_at_k - pass_hat_k
```

List signatures with `gap > 0` as `flip_prone: <signature>` — a separate line item from
the Phase 4 leaderboard, even if the same runs also belong to a named behavior_pattern.
For the top flip-prone signature (if any), run the Phase 5 backward-suspect method to
name the specific decision step that varies across the repeated runs' state digests.

## Step 5 — Suspect trace (Phase 5)

For the **top 3** impactful failing patterns: take 2 representative member runs, `get_run_outline`, identify the focus event (first error span or awaiting-review marker), walk `parent_span_id` backward, and name the most likely suspect step (`suspect_score = 0.4·proximity + 0.3·frequency + 0.2·bridge + 0.1·role`). Report it as an **inspection guide, not proof.**

## Step 6 — Persist (report + annotate)

**Report file** — write `.claude/reports/macro-evals/<TODAY>.md`:

```markdown
# Macro-Eval Sweep — <TODAY>
Window: last <DAYS> days · Population: <N> runs · Filter: <filter or "all">

## Pattern leaderboard
| Rank | Behavior pattern | Runs | Outcome mix | Impact | Suspect step |
|------|------------------|------|-------------|--------|--------------|
| 1 | ... | n | x fail / y esc | 0.00 | ... |

## Consistency gap
<!-- Omit this section entirely if no task signature in the window had ≥3 repeated runs. -->
| Task signature | Runs | Mean@k | Pass^k | Gap | Flip-prone step |
|---|---|---|---|---|---|
| ... | n | 0.00 | 0/1 | 0.00 | ... |

## Diagnoses (top 3)
### <pattern> — impact 0.00
- Members: <run ids>
- Focus event: <...>
- Suspect step (inspection guide): <...>
- Evidence: <span/run refs>

## Delta vs previous sweep
<compare to the most recent prior report in this dir; note new/worsening/resolved patterns. If none exists, write "first sweep — no baseline.">
```

**Hand off flip-prone steps** — if Step 4b found any `flip_prone` signature, this is
input for `skill-augment-agent` (not this sweep) to write as a stability guideline into
the relevant `SKILL.md` on its own next run. Do not write the guideline yourself here;
just make sure the flip-prone step and signature are named plainly enough in the report
for that agent to find.

**Annotate Workshop** — for each confirmed recurring failure pattern, post durable annotations:
- Run-level: `annotate(run_id, kind='issue', note='<pattern>: suspect <step>')` on each member run of the top patterns (cap at ~5 runs per pattern to avoid noise; note the cap in the report).
- Span-level: when a single span is the clear focus event, `annotate(run_id, span_id, kind='issue', note=...)`.
- Use `kind='note'` for non-failure observations worth surfacing.

## Step 7 — Close out

Print a 3-line summary: population size, top pattern + impact, report path. If run locally via the scheduler, this output is logged.

**Guardrails**
- Never invent runs/spans — every claim ties to a real `run_id`/`span_id` from `query_traces`.
- Prefer IDs, counts, SUBSTR previews over full payloads; only `get_span_payload` when a diagnosis needs exact bytes.
- If any phase yields nothing, say so explicitly — no silent truncation.
