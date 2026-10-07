# Evaluation Skills

All evaluation capabilities live in a single `evaluation/` skill family. The router (`evaluation`) dispatches to sub-skills based on your situation; you can load multiple sub-skills when a task spans more than one layer.

## Folder Structure

```
skills/evaluation/
  SKILL.md                  ← router: decision tree + multi-skill loading guide
  micro/SKILL.md            ← error-analysis bootstrap, per-run grading, LLM-as-judge, rubrics, quality gates
  macro/SKILL.md            ← population patterns, impact ranking, suspect tracing
  funnel/SKILL.md           ← pre-experiment filtering, A/B decisions, judge calibration
  long-trajectory/SKILL.md  ← long-horizon agents, stateful verification, rubric adaptation
  references/
    metrics.md              ← detailed metrics reference
    pipeline-reference.md   ← macro-eval BERTopic pipeline, scoring derivations
```

## Sub-Skills

### `evaluation` — Router
`~/.claude/skill-library/evaluation/SKILL.md`

Always invoke first. Provides the decision tree and tells you which sub-skill(s) to load. For multi-layer tasks, invoke all relevant sub-skills before implementation.

**Invoke:** `Skill("evaluation")`

---

### `evaluation/micro` — Per-Run Grading
`~/.claude/skill-library/evaluation/micro/SKILL.md`

The foundational grading layer. Covers:
- Error-analysis bootstrap — read ~100 real traces, build the initial failure-category taxonomy before you have any rubric
- Multi-dimensional rubric design (factual accuracy, completeness, tool efficiency, citation accuracy)
- LLM-as-judge implementation and prompt patterns
- Test set construction and complexity stratification
- Continuous evaluation pipelines and regression detection
- End-state vs. process evaluation

Error-analysis section extracted from: Hamel Husain's `error-analysis` skill ([hamelsmu/evals-skills](https://github.com/hamelsmu/evals-skills)) (2026-08-02). Human-driven, not automatable — findings written to `.claude/memory/failure-taxonomy-<date>.md`, picked up by the dashboard's memory-files panel automatically.

**Activate with:** "evaluate agent performance", "build test framework", "measure agent quality", "LLM-as-judge", "evaluation rubric", "quality gate", "error analysis", "failure taxonomy", "what's breaking in my agent"

**Invoke:** `Skill("evaluation/micro")`

---

### `evaluation/macro` — Population-Scale Pattern Discovery
`~/.claude/skill-library/evaluation/macro/SKILL.md`

Extracted from: [OpenAI Cookbook — Macro Evals for Agentic Systems](https://developers.openai.com/cookbook/examples/partners/macro_evals_for_agentic_systems/macro_evals_for_agentic_systems) (2026-05-31)

Five-phase methodology: collect/normalize → build trace document → cluster patterns → rank by impact → backward suspect trace.

**Activate with:** many traces, systemic failures, "which problems repeat", "where to fix first", population patterns

**Invoke:** `Skill("evaluation/macro")`

Automated as the ~twice-weekly `/kiro:macro-eval-sweep` routine over Raindrop Workshop traces.

---

### `evaluation/funnel` — Pre-Experiment Filtering & Judge Calibration
`~/.claude/skill-library/evaluation/funnel/SKILL.md`

Extracted from: [Spotify Engineering — LLM Evals: A Funnel, Not a Fork](https://engineering.atspotify.com/2026/5/better-experiments-with-llm-evals-a-funnel-not-a-fork) (2026-05-27)

Three-stage methodology: pre-experiment filtering → A/B decision criteria → post-experiment judge calibration.

Key numbers: 12% of A/B tests ship positive; 42% of launched experiments eventually reverse. Running evals first filters bad candidates before burning experiment bandwidth.

**Activate with:** "eval funnel", "pre-experiment filter", "judge calibration", "A/B test LLM feature", "experiment bandwidth"

**Invoke:** `Skill("evaluation/funnel")`

---

### `evaluation/long-trajectory` — Long-Horizon Agent Evaluation
`~/.claude/skill-library/evaluation/long-trajectory/SKILL.md`

Extracted from: [JudgmentLabs — Agent Judge: Solving Long-Context Evaluations](https://www.judgmentlabs.ai/blogs/agent-judge-solving-long-context-evaluations) (2026-06-01)

Three-phase workflow: Search (slice trajectory into targeted evidence chunks via worker agents) → Verify (cross-check agent claims against external system state) → Adapt (Rubric Builder closed-loop calibration).

Empirical improvement: 0.86 accuracy / 0.79 F1 vs. 0.74 / 0.65 for a standard LLM judge on production traffic.

**Activate with:** trajectory too long for context, agent mutates external state, rubric needs calibration, long-horizon agent evaluation, stateful agent verification

**Invoke:** `Skill("evaluation/long-trajectory")`

---

## Commands

### `/eval-funnel-check` — Pre-Launch Checklist
`~/.claude/commands/eval-funnel-check.md`

Interactive checklist. Run before committing to an A/B test to verify Stage 1–3 are planned. Produces a ready/not-ready verdict with flagged gaps.

**Usage:** `/eval-funnel-check [optional feature description]`

---

## Related Skills (Outside This Family)

### `cma-outcomes` — Automated Grade-and-Revise Loops
`~/.claude/skill-library/cma-outcomes/SKILL.md`

Implements the full grade-and-revise loop using the Claude Managed Agents Outcomes feature. Use alongside `evaluation/micro` when you want the grader to loop automatically — no custom orchestration needed.

**Activate with:** "grade-and-revise loop", "CMA outcomes", "grader agent", "verify agent output automatically", "managed agents rubric"

---

## Layer Model

```
FUNNEL            ← filter candidates before committing to A/B experiments
  │
MICRO             ← grade individual runs (pass/fail + dimension scores)
  │
LONG-TRAJECTORY   ← extend micro to long/stateful runs that exceed its limits
  │
MACRO             ← aggregate micro signal into population-level patterns
```

Data flows bottom-up: micro produces the raw signal that macro aggregates. Long-trajectory extends micro to cover hard cases. Funnel sits above all of them, deciding what to ship.

Multi-skill combos:
- "Evaluate my agent and find patterns" → `micro` + `macro`
- "Set up an eval pipeline before we A/B test" → `micro` + `funnel`
- "My agent does 200-turn runs with DB writes" → `long-trajectory` + `micro`
- "Full eval system from scratch" → `micro` + `long-trajectory` + `macro` + `funnel`

---

## When Evaluation Becomes a Training Signal

To use your evaluation/reward signal to **update model weights** rather than just measure quality, see the `rl-agent-training` skill (`~/.claude/skill-library/rl-agent-training/skill.md`). It covers online RL training with ART (Agent Reinforcement Trainer): run the agent many times, score with a reward function or RULER (comparative LLM judge), and update via GRPO. Typical cost: $15–$200 in GPU time.

**Use `rl-agent-training` when:** you can run the agent and score outcomes, and want to bake the winning behavior into model weights rather than just measure it.
