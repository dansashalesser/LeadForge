# Alignment Scoring — Quantitative Agent Output Evaluation

Inspired by DSPy's NMSE (Normalized Mean Squared Error) approach to measuring prompt quality, this rule defines a scoring system for measuring how well agent outputs align with expected outcomes.

## Why Alignment Scoring

Binary pass/fail in `trace.log` tells you *that* an agent failed, not *how far* it was from correct. Alignment scoring provides:
- **Magnitude**: How wrong was the output? (score 1 vs score 3 are both "not perfect" but very different)
- **Direction**: Is the agent too strict, too lenient, missing format, or misunderstanding the task?
- **Trend**: Is a prompt change improving or degrading alignment over time?

## Scoring Rubric (0-5 Scale)

| Score | Label | Criteria |
|-------|-------|----------|
| 5 | Perfect | Correct outcome, correct format, no issues, reasoning is sound |
| 4 | Good | Minor omissions or extra content, correct conclusion reached |
| 3 | Adequate | Right direction but missing important details or weak reasoning |
| 2 | Poor | Partially correct but materially wrong in key aspects |
| 1 | Failed | Wrong conclusion, misunderstood the task, or fundamentally off-target |
| 0 | Broken | No useful output, wrong format entirely, or hallucinated content |

## Structural Reliability

Separately from alignment, track whether the agent returned output in the expected format:
- `structural:ok` — output follows the agent's defined output format (## Summary, ## Changes Made, etc.)
- `structural:malformed` — output is prose, missing sections, wrong structure, or unparseable

Structural reliability is measured independently because a structurally broken output with correct content (alignment 3-4) signals a different problem than a well-formatted wrong answer (alignment 1-2).

## How to Score

The **orchestrating command** (not the agent itself) scores alignment after reviewing agent output. Scoring guidelines:

1. **Compare output to the command's success criteria** — each command defines what "good" looks like
2. **Score the conclusion, not the verbosity** — a concise correct answer scores higher than a verbose wrong one
3. **Consider the task difficulty** — a score of 3 on a complex adversarial review may be acceptable; a score of 3 on mechanical steering generation is a problem
4. **Default to omitting** — if the command cannot meaningfully score alignment, omit the field (backward compatible)

## Trace Format Extension

The alignment and structural fields extend the existing trace format (see `agent-tracing.md`):

```
YYYY-MM-DD HH:MM | agent-name | tier | outcome | duration-hint | alignment:N | structural:ok
```

Both new fields are optional — older trace entries without them remain valid.

## Aggregate Metrics (Computed by Evolve Agent)

From alignment scores in trace.log, the evolve agent computes:

| Metric | Formula | Signal |
|--------|---------|--------|
| Mean alignment | avg(alignment) per agent | Overall prompt quality |
| Structural reliability | count(ok) / count(all) per agent | Format compliance |
| Alignment trend | compare last 5 vs previous 5 scores | Improving or degrading |
| Alignment variance | stddev(alignment) per agent | Consistency of output quality |

## Thresholds for Action

| Condition | Action |
|-----------|--------|
| Mean alignment < 3.0 | Candidate for prompt diagnosis (see prompt-diagnosis-agent) |
| Structural reliability < 90% | Prompt needs format reinforcement (fix prompt, not model tier) |
| Alignment trend declining (>1.0 drop) | Investigate recent prompt or rule changes |
| Alignment >= 4.0 consistently | Candidate for tier demotion (try cheaper model) |

## Anti-Overfitting Guardrails

When using alignment data to improve prompts:
1. **No keyword copying** — improvements must not embed specific content from scored scenarios
2. **No task drift** — prompt changes must not alter the agent's core mission or scoring thresholds
3. **No scale manipulation** — never adjust what constitutes GO/NO-GO to improve scores
4. **Generalization required** — every proposed change must address patterns across 2+ instances, not a single failure
5. **Removal path** — every instruction added must be removable without breaking other instructions

## Relationship to Other Rules

- **agent-tracing.md**: Defines the trace format this rule extends
- **model-tiering.md**: Alignment data informs tier promotion/demotion decisions
- **self-tightening.md**: Alignment scoring is the prompt-level analog of the code-level self-tightening loop
