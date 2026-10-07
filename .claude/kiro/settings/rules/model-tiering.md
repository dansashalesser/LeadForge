# Model Tiering — Cost Optimization for Sub-Agents

Use expensive models (Opus) for orchestration and decision-making. Use cheaper models (Sonnet/Haiku) for mechanical, research, and scanning work. This can reduce cost significantly without quality loss.

## Tier 1: Opus (decision-heavy, design judgment)
These agents make architectural decisions or evaluate quality:
- `spec-design` — architectural decisions
- `spec-refactor` — code quality evaluation (skeptical evaluator)
- `evolve-agent` — harness rule design
- `prompt-diagnosis-agent` — analyzing prompt quality and recommending targeted improvements
- `spec-requirements` — requirement analysis
- `validate-adversarial` — three-pass adversarial review with judgment-heavy refutation
- `validate-perf-agent` — performance analysis requiring deep judgment about scalability

## Tier 2: Sonnet (structured execution, moderate judgment)
These agents follow clear protocols with some judgment:
- `spec-impl` — TDD execution (protocol-driven)
- `spec-tasks` — task breakdown from design
- `validate-gap` — gap analysis (structured comparison)
- `validate-design` — design checklist review
- `validate-impl` — implementation vs spec comparison
- `jira-solve-agent` — ticket classification and routing
- `harness-fix-agent` — rule encoding from mistake description
- `skill-extract-agent` — scoring and extraction
- `fix-build-agent` — surgical build error resolution (diagnostic + fix loop)
- `learn-eval-agent` — pattern quality evaluation and deduplication

## Tier 3: Haiku (mechanical, scanning, low-judgment)
These agents do mostly mechanical work:
- `steering` — codebase scanning and summarization
- `steering-custom` — document generation from templates
- `doc-sync` — diffing code changes against docs
- `housekeeping-agent` — archival and pruning
- `reflect-agent` — log mining and pattern extraction
- `autoresearch-agent` — experiment loop execution
- `harness-updater` — file copying and sync
- `verify-agent` — mechanical command execution and exit code reporting
- `save-session-agent` — session state capture to markdown

## How to Apply

When spawning a sub-agent via the Agent tool, set the `model` parameter:
- Tier 1: omit (inherits parent Opus)
- Tier 2: `model: "sonnet"`
- Tier 3: `model: "haiku"`

This is guidance, not enforcement. If a Tier 3 agent produces poor results, promote it to Tier 2.

## Data-Driven Tiering

The initial tier assignments above are heuristic starting points. Over time, alignment data from `trace.log` should inform tiering decisions — replacing debate with measurement.

### How It Works

The evolve agent (Step 1c) computes alignment scores per agent at their current tier and flags candidates:

| Signal | Condition | Action |
|--------|-----------|--------|
| `DEMOTE?` | Mean alignment >= 4.0 at opus/sonnet | Try cheaper tier — prompt is well-structured enough |
| `PROMOTE?` | Mean alignment < 3.0 at haiku/sonnet | Try more capable tier — task may require more judgment |
| `FIX-FORMAT` | Structural reliability < 90% | Fix the prompt, not the tier — format issues are prompt quality problems |

### Validation Before Committing

Every tier change must be validated:
1. Run `/kiro:harness-test {agent-name}` at the proposed tier
2. If regression scenarios exist (`/kiro:harness-test regression {agent-name}`), run those too
3. Monitor alignment in the next 3-5 invocations after the change
4. Revert if alignment drops >1.0 from the pre-change mean

### Key Insight

From Dropbox's DSPy work: "Model swaps went from prolonged debate to quick measurement-based decisions." With alignment scores in trace.log, tiering is an evidence-based decision, not a guess.

## Smoke Testing with Haiku

Use `/kiro:harness-test` to run key workflows at Haiku tier. Haiku failures expose vague prompts that rely on model intelligence instead of clear structure. If a prompt works with Haiku, it is well-structured. Use cheap models during development to stress-test prompt quality before committing changes to agent or rule files.
