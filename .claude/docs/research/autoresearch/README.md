# AutoResearch — Autonomous ML Experiment Loop

> Detailed reference for the autoresearch subsystem, adapted from [karpathy/autoresearch](https://github.com/karpathy/autoresearch).

## What It Is

An autonomous ML research agent that iterates on training code overnight. It reads a research brief (`program.md`), modifies a training script (`train.py`), runs 5-minute experiments, evaluates the validation metric, keeps improvements, reverts failures, and loops.

The harness wraps this into two commands:
- `/kiro:autoresearch-init` — Interactive setup that generates all required files from a user interview
- `/kiro:autoresearch` — Runs the experiment loop

## How It Works

### The Loop

```
┌─────────────────────────────────────────────────┐
│  1. Read program.md (research goal, constraints) │
│  2. Propose hypothesis (one change to train.py)  │
│  3. Edit train.py                                │
│  4. Run: uv run train.py (~5 min)               │
│  5. Parse validation metric (e.g., val_bpb)      │
│  6. Improved? → keep. Worse? → git checkout.     │
│  7. Log result. Adapt strategy.                  │
│  8. Repeat.                                      │
└─────────────────────────────────────────────────┘
```

Each iteration is atomic: one hypothesis, one code change, one experiment. Compound changes are forbidden because they make attribution impossible.

### Key Files

| File | Role | Modified by agent? |
|---|---|---|
| `program.md` | Research brief — goal, metric, constraints, directions | Never |
| `train.py` | Training script — the "canvas" the agent iterates on | Yes (every iteration) |
| `prepare.py` | Data preparation — downloads, preprocesses, splits data | Never |

### The 5-Minute Bound

Training runs are wall-clock bounded (default 5 minutes), not epoch-bounded. This ensures:
- Experiments are always comparable (same time budget)
- The agent can iterate many times overnight
- No single failed experiment wastes hours

## Components

### Commands

| Command | Purpose |
|---|---|
| `/kiro:autoresearch-init` | Interactive interview → generates `program.md`, `train.py`, `prepare.py` |
| `/kiro:autoresearch [N]` | Runs the experiment loop (N iterations, or continuous until stopped) |

### Agents

| Agent | Triggered By | Purpose |
|---|---|---|
| `autoresearch-init-agent` | `/kiro:autoresearch-init` | Asks 8 leading questions, scans codebase, generates all 3 files |
| `autoresearch-agent` | `/kiro:autoresearch` | Runs the experiment loop autonomously |

## Use Cases

1. **Overnight ML experimentation** — Start the loop before leaving, review results in the morning
2. **Hyperparameter search** — Let the agent systematically explore learning rates, dropout, batch sizes
3. **Architecture search** — Agent tries different layer configurations, attention patterns, etc.
4. **Quick prototyping** — Use `autoresearch-init` to scaffold a training pipeline from a description, then iterate

## How to Use

### First-time setup (interactive)

```
/kiro:autoresearch-init
```

The agent asks 8 questions across 3 phases:

**Phase 1 — Research Goal:**
1. What are you trying to train? (plain language description)
2. What data will it train on? (source, format, size)
3. What model architecture? (existing model or scaffold a default)

**Phase 2 — Optimization Target:**
4. What metric measures improvement? (val_bpb, accuracy, F1, custom)
5. Do you have a baseline to beat?

**Phase 3 — Constraints:**
6. What's off-limits? (parts of code the agent must not touch)
7. Specific directions to explore or avoid?
8. Time budget per experiment? (default: 5 minutes)

After confirmation, it generates all 3 files.

### Prepare data (one-time)

```bash
uv run prepare.py
```

### Run the loop

```
/kiro:autoresearch          # continuous until you stop it
/kiro:autoresearch 10       # stop after 10 iterations
```

### Review results

The agent produces a summary at the end:
- Iterations completed
- Best metric achieved vs. baseline
- Changes kept vs. reverted
- Suggested next experiment

## Prerequisites

- **uv** — Python package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Python 3.10+**
- **GPU recommended** — CPU works but experiments will be slower
- **git** — Required for reverting failed experiments (`git checkout -- train.py`)

## Example `program.md`

```markdown
# Research Program

## Goal
Train a character-level language model on Shakespeare to minimize validation bits-per-byte.

## Metric
- **Primary metric**: val_bpb (lower is better)
- **Baseline**: to be established on first run

## Constraints
- Training runs must complete within 5 minutes wall-clock time
- Do not modify the tokenizer or data loading pipeline
- Keep model under 20M parameters

## Suggested Research Directions
- Learning rate schedules (cosine, warmup+decay)
- Layer normalization placement (pre-norm vs post-norm)
- Dropout rates and regularization
- Attention head count and embedding dimensions
- Activation functions (GELU, SwiGLU)
```

## Agent Behavior

### Experiment strategy
- **After accepted change**: Build on it — probe same direction or generalize
- **After 2+ consecutive reverts**: Pivot — try a different aspect of the model
- **After crash**: Re-read `program.md` constraints, propose safer change

### Safety
- Never modifies `prepare.py` or `program.md`
- Always reverts failed experiments (via `git checkout -- train.py`)
- One change per iteration — no compound experiments
- Honors all constraints in `program.md`

## Setup (from harness)

The autoresearch commands and agents are included in the harness. No additional installation needed — just run `/kiro:autoresearch-init` in any project.

For manual setup without the init command, see [karpathy/autoresearch](https://github.com/karpathy/autoresearch).
