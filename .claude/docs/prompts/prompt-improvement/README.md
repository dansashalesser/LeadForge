# Prompt Improvement — Systematic Agent Prompt Optimization

> Turn "the agent keeps getting this wrong" into measurable, incremental, testable prompt fixes.

## What It Is

A closed-loop system for improving **any prompt in the harness** — sub-agent prompts, command orchestration prompts, rule files, and even the workflow prompts you write when invoking commands. Inspired by [Dropbox's DSPy-based optimization of their Dash Relevance Judge](https://dropbox.tech/machine-learning/optimizing-dropbox-dash-relevance-judge-with-dspy). Instead of manual prompt tweaking and regression chasing, this system provides:

- **Quantitative alignment scoring** — measure *how far* agent output is from expected, not just pass/fail
- **Structured diagnosis** — pinpoint *why* a prompt fails and *what specific change* to make
- **Incremental instruction changes** — each improvement is a single, testable bullet ("small PRs with tests")
- **Regression testing** — verify prompt changes don't break other scenarios
- **Data-driven model tiering** — promote/demote agents between model tiers based on evidence, not guesses

## What Gets Improved — It's Not Just Sub-Agents

The harness has multiple layers of prompts. This system applies to all of them:

| Layer | Examples | How Alignment Scoring Applies |
|-------|----------|-------------------------------|
| **Sub-agent prompts** | `agents/kiro/spec-impl.md`, `validate-adversarial.md` | Scored automatically via trace.log after every invocation. This is the primary automated loop. |
| **Command prompts** | `commands/kiro/evolve.md`, `spec-quick.md` | Commands orchestrate agents — if the command passes bad context or wrong instructions to the agent, the agent output suffers. Diagnose command-level issues when the agent prompt looks fine but output is still poor. |
| **Rule files** | `rules/design-principles.md`, `rules/ears-format.md` | Rules constrain agent behavior. A vague rule produces vague agent output. The instruction library captures sharpened rules as testable bullets. |
| **Your workflow prompts** | The arguments you pass to `/kiro:spec-init "..."`, feature descriptions, task descriptions | If you notice agents consistently misunderstanding your intent, the issue may be in how you frame requests. Capture effective prompt patterns in the instruction library under "Cross-Cutting." |

### When to Use Each Improvement Path

| Symptom | Where to Look | Action |
|---------|---------------|--------|
| Agent consistently gives wrong conclusions | Sub-agent prompt | Run `/kiro:evolve` → automatic diagnosis + instruction proposals |
| Agent gets wrong context or wrong files | Command prompt | Review the command's `prompt="""..."""` block — it may pass incorrect file patterns or miss steering context |
| Agent ignores a convention | Rule file | Check if the rule is vague; sharpen it or add an instruction library bullet |
| Agent misunderstands your feature description | Your workflow prompt | Capture what works in instruction library "Cross-Cutting" or in `meta/patterns.md` |
| Agent output is structurally wrong (missing sections) | Sub-agent prompt | Flagged as `FIX-FORMAT` by evolve — usually needs explicit format reinforcement in the prompt |
| Agent is fine sometimes, terrible other times | Model tier mismatch | Check alignment variance; high variance at Haiku = promote to Sonnet |

## Why It Matters

Before this system, the harness had a self-tightening loop for code conventions (friction → patterns → linter rules), but no equivalent for agent prompts. When an agent underperformed, the options were:

1. Manually rewrite the prompt (slow, regression-prone)
2. Promote to a more expensive model tier (costly, doesn't fix the root cause)
3. Accept the failure and move on

Now there's a systematic alternative: **score → diagnose → prescribe → test → apply**.

## Architecture

```
Agent produces output
  → Orchestrating command scores alignment (0-5) + structural reliability
  → trace.log records enriched entry
  → /kiro:evolve computes per-agent alignment statistics
  → Underperformers (alignment < 3.0) get automatic prompt diagnosis
  → Diagnosis recommends specific instruction changes (ADD/REMOVE/SHARPEN)
  → /kiro:harness-test regression validates changes don't regress
  → User approves → instructions applied to instruction library
  → Model tier adjustments proposed with evidence
```

### Key Files

| File | Purpose |
|------|---------|
| `kiro/settings/rules/alignment-scoring.md` | The 0-5 scoring rubric and aggregate metrics |
| `kiro/settings/rules/agent-tracing.md` | Trace format with alignment + structural fields |
| `agents/kiro/prompt-diagnosis-agent.md` | GEPA-inspired diagnosis agent |
| `kiro/settings/templates/memory/meta/instruction-library.md` | Template for incremental instruction bullets |
| `kiro/settings/templates/memory/meta/prompt-scenarios.md` | Template for regression test scenarios |
| `kiro/settings/rules/model-tiering.md` | Includes data-driven tiering section |

## How to Use It

### Step 1: Score Agent Output

When orchestrating commands invoke agents, they score the output:

```
trace.log entry:
2026-04-06 14:22 | validate-adversarial | opus | go | fast | alignment:4 | structural:ok
```

The alignment score (0-5) measures how well the agent output matched the expected outcome. The structural field tracks whether the output followed the expected format.

### Step 2: Run Evolve to Analyze

```
/kiro:evolve
```

The evolve agent now includes an **Alignment Scorecard** in its output:

```
| Agent | Invocations | Mean Align | Structural | Trend | Flag |
|-------|-------------|-----------|------------|-------|------|
| validate-adversarial | 8 | 4.2 | 100% | stable | — |
| steering | 12 | 2.8 | 75% | degrading | DIAGNOSE |
```

Agents flagged `DIAGNOSE` (alignment < 3.0) or `FIX-FORMAT` (structural < 90%) automatically receive a prompt diagnosis.

### Step 3: Review Diagnosis and Proposals

The evolve output includes diagnosis summaries and typed proposals:

```
### Proposal: Add evidence grounding to steering agent
**Type**: add-instruction
**Category**: scanning
**Instruction**: "[evidence] When reporting stack components, cite the specific config file where each was detected"
**Addresses**: root cause — steering agent sometimes reports technologies not present in the project
**Generalizes across**: 3 instances where steering output included phantom dependencies
```

### Step 4: Test Before Applying

After approving instruction changes, validate with regression tests:

```
/kiro:harness-test regression
/kiro:harness-test regression steering    # test specific agent
```

This runs scenarios from `.claude/memory/meta/prompt-scenarios.md` at Haiku tier and reports per-scenario alignment scores.

### Step 5: Build Your Scenario Library

Scenarios grow organically from real usage. Add them after:

- **Successful invocations** — capture as a positive scenario (alignment floor 4)
- **Diagnosed failures** — capture as a regression guard (alignment floor 3)
- **Edge cases** — add scenarios for known tricky situations

Format in `.claude/memory/meta/prompt-scenarios.md`:
```markdown
## steering
### Scenario: python-fastapi-project
- Input: Project with pyproject.toml, FastAPI, pytest, no frontend
- Expected: tech.md identifies Python/FastAPI stack, pytest as test framework
- Expected finding: Must not report frontend frameworks
- Alignment floor: 4
- Added: 2026-04-06 — steering agent previously hallucinated React dependency
```

## The Instruction Library

The instruction library at `.claude/memory/meta/instruction-library.md` contains single-line instruction bullets organized by agent category:

```markdown
## Cross-Cutting
- [format] Verify output matches expected structure before returning
- [scope] Never modify files outside the stated task scope

## Validation Agents
- [evidence] Every concern must cite a specific file:line reference

## Scanning Agents
- [precision] When reporting components, cite the config file where each was detected
```

Each instruction is independently testable and removable. This is the "small PRs with tests" pattern — instead of rewriting an entire agent prompt, you add or remove one bullet at a time, test with `/kiro:harness-test regression`, and monitor alignment in subsequent invocations.

## Data-Driven Model Tiering

Instead of guessing which model tier an agent needs, alignment data makes it measurable:

| Signal | Condition | What It Means |
|--------|-----------|---------------|
| `DEMOTE?` | Alignment >= 4.0 at opus/sonnet | Prompt is well-structured; try a cheaper model |
| `PROMOTE?` | Alignment < 3.0 at haiku/sonnet | Task requires more judgment; try a more capable model |
| `FIX-FORMAT` | Structural reliability < 90% | Prompt is vague — fix the prompt, not the tier |

The evolve agent produces `adjust-tier` proposals with evidence. Every tier change is validated with `/kiro:harness-test` before committing.

## Anti-Overfitting Guardrails

The system includes explicit guardrails to prevent prompt changes from overfitting to specific scenarios:

1. **No keyword copying** — instructions must not embed content from specific test cases
2. **No task drift** — changes must not alter the agent's core mission
3. **No scale manipulation** — changes must not redefine what constitutes GO/NO-GO
4. **Generalization required** — every change must address patterns across 2+ instances
5. **Removal path** — every instruction must be independently removable

These guardrails are enforced by the prompt-diagnosis-agent and verified in its output before recommendations are finalized.

## Alignment Scoring Rubric (Quick Reference)

| Score | Label | When to Use |
|-------|-------|-------------|
| 5 | Perfect | Correct outcome, correct format, sound reasoning |
| 4 | Good | Minor omissions, correct conclusion reached |
| 3 | Adequate | Right direction but missing important details |
| 2 | Poor | Partially correct but materially wrong |
| 1 | Failed | Wrong conclusion or misunderstood the task |
| 0 | Broken | No useful output, wrong format, hallucinated |

## Commands Reference

| Command | Purpose |
|---------|---------|
| `/kiro:evolve` | Full audit including alignment analysis, prompt diagnosis, and instruction proposals |
| `/kiro:harness-test` | Smoke test agent prompts at Haiku tier |
| `/kiro:harness-test regression` | Run scenario-based regression tests with alignment scoring |
| `/kiro:harness-test regression {agent}` | Regression tests for a specific agent |

## Background: The Dropbox DSPy Insight

Dropbox used DSPy's GEPA optimizer to systematically improve prompts for their Dash Relevance Judge. Key results:
- 45% reduction in alignment error (NMSE)
- Structural reliability from 58% to 99%
- Model evaluation timelines from weeks to days
- Prompt changes treated as "small PRs with tests" — incremental, testable, diagnosable

Their core insight: **stop manually tweaking prompts and start measuring alignment, diagnosing failures structurally, and making incremental tested changes.** This system brings those principles into the SDD harness without requiring the DSPy library — everything is markdown-native, approval-required, and integrated with the existing self-tightening loop.

## Setup

The prompt improvement system works out of the box after harness installation. To get started:

1. **Existing projects**: Agent invocations will start populating alignment data in `trace.log` as commands adopt the extended format
2. **Run `/kiro:evolve`** periodically — it will produce alignment scorecards once enough trace data exists (minimum 3 scored entries per agent)
3. **Seed scenarios** in `.claude/memory/meta/prompt-scenarios.md` from real usage — start with 2-3 per agent category
4. **Review and approve** instruction changes through the standard evolve approval flow

See `SDD-SETUP-GUIDE.md` for full harness installation instructions.
