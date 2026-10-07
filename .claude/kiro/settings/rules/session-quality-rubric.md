# Session Quality Rubric

Behavioral rubric the `session-judge` agent uses to score the harness's own work over a window (typically the last 24 hours of `observations.md` plus any `[memory-gap]` entries).

Single-user, single-scalar. Per-person trust batteries do not apply to this harness — there is one human per installed project.

## Asymmetric Scoring

Same philosophy as `kiro/settings/rules/alignment-scoring.md` and `validate-adversarial`: drains count double. Missing a drain is costlier than missing a charge.

| Outcome | Weight |
|---|---|
| Confirmed charge | +1 |
| Confirmed drain | -2 |
| Ambiguous signal | 0 (discard) |

Daily cap: **±4.5%** (matches the source trust-battery design; prevents runaway swings from one noisy day).

## Charges (+1 each)

- **Clean gate pass** — a spec phase gate (`/kiro:spec-requirements`, `spec-design`, `spec-tasks`, `spec-impl`) passed without the user rejecting output or asking for a redo.
- **Self-caught mistake** — an observation tagged `debug` or `insight` shows the harness found and fixed an issue *before* the user flagged it.
- **Reused existing utility** — `impl` observation cites a pre-existing function/pattern rather than introducing a new one. Reuse is the norm we reward.
- **Cited memory during work** — session work references an observation, pattern, or hot-memory entry (grep for `observations.md#`, `patterns.md#`, or direct quotes).
- **Root-cause fix** — `debug` observation describes a root cause, not a symptom (heuristic: mentions "why" or "because", not just "fixed by").
- **Rule obeyed under pressure** — `decision` observation explicitly cites a rule file as the reason a shortcut was rejected.

## Drains (-2 each)

- **Re-explanation** — any `[memory-gap]` observation from the detector: the user had to re-explain context the harness should already have in memory. **Flagship drain.**
- **Silent failure** — trace log or observations show an agent returned success when output was clearly wrong (caught in a later turn by the user).
- **Gate bypass** — a spec implementation shipped without a passing `/kiro:verify` run, or a phase was skipped without explicit user approval.
- **Rationalized rule-skip** — observation or trace shows the harness argued its way out of a rule (see `anti-rationalization.md` — this is the exact failure mode that rule exists to prevent).
- **Stale context** — harness acted on a fact that `git log` or file contents show had changed, and nobody caught it until the user did.
- **Churn** — multiple redo cycles on the same artifact within the window (3+ edits of the same file from reflection/evolve without a clear learning captured).

## Auto-scored Signals (machine-counted by `trust_score.py auto-score`)

These are scored deterministically from observation tags — no Judge pass needed. The Judge may still cite them as supporting evidence, but the score is already applied mechanically.

| Tag | Condition | Weight | Cap/day |
|---|---|---|---|
| `[session-charge]` | Any count | +1 each | 3 |
| `[memory-gap]` | Any count | -2 each | 3 |
| `[session-quality]` | Score ≥ 4/5 | +1 | 1 |
| `[session-quality]` | Score ≤ 2/5 | -1 | 1 |
| `[keep-rate]` | N ≥ 80% | +1 | 1 |

`[session-charge]` entries are written automatically by the stop hook when the user's session transcript contains unambiguous approval phrases ("that's perfect", "that works!", "great work", etc.). They require no manual action.

`auto-score` is deterministic, so it submits its total as a **single** sample — the multi-sample spread gate that reconciles repeated judge runs is skipped, and the record reports `"spread": null` rather than `0.0`. Because the count is over observation tags, a routine that appended one `[judge]` observation per judge run would multiply every tag it cites; that is why only the caller appends, once, for all runs.

## Judging Constraints

- **No proposals.** The judge names what it sees and assigns a weight. It does not recommend fixes, rewrite memories, or edit rules. That is the reflector's job, run separately so the judge has no incentive to soften its own scoring.
- **Evidence required.** Every weighted entry must cite an observation ID, file path, or trace line. Gut feelings score 0.
- **Bounded.** Maximum 10 entries per pass (5 charges + 5 drains). More than that means the rubric is being stretched — stop and take only the strongest.
- **Idempotent — except in sample mode.** If an observation is already tagged `[judge]` for today's window, it is not scored again. This is suspended when the caller runs the judge k times and tells it not to append: each run must score the window in full. Otherwise run 1 scores the day, runs 2 and 3 find run 1's entry and return `score_delta: 0`, and k independent draws collapse into one real sample plus k−1 fabricated zeros — a median with the variance removed by construction rather than by measurement.

## Output Schema

```json
{
  "window": "2026-04-20T00:00Z..2026-04-21T00:00Z",
  "positives": [
    {"tag": "root-cause-fix", "evidence": "observations.md entry 2026-04-20 [debug]", "weight": 1}
  ],
  "negatives": [
    {"tag": "re-explanation", "evidence": "observations.md entry 2026-04-20 [memory-gap]", "weight": -2}
  ],
  "score_delta": -1.0,
  "summary": "One sentence summarizing the day."
}
```

`score_delta` is the sum of all weights, clamped to `[-4.5, +4.5]`. When the caller collects several runs, each run's `score_delta` is passed to `trust_score.py apply` as its own `--delta`; the median is applied, and a spread above 2.0 across the samples is recorded as inconclusive with a delta of `0.0`.

_Last synced: 2026-09-03_
