---
description: Run autonomous ML research loop — reads program.md, iterates on train.py experiments
allowed-tools: Read, Bash, Task, Glob
argument-hint: [N iterations | "until <condition>"]
---

# AutoResearch — Autonomous ML Experiment Loop

## Pre-checks

1. Verify `program.md` exists in the project root. If missing, abort with:
   > `program.md` not found. AutoResearch requires a `program.md` describing the research goal and constraints. See https://github.com/karpathy/autoresearch for setup instructions.

2. Verify `train.py` exists. If missing, abort with same setup message.

3. Check `uv` is available: `Bash("uv --version")`. If not found, abort with:
   > `uv` is required. Install with: `curl -LsSf https://astral.sh/uv/install.sh | sh`

4. Check if data has been prepared. If `prepare.py` exists and no data artifacts are present (check common paths like `data/`, `*.bin`, `*.npy`), suggest running:
   > Run `uv run prepare.py` first (one-time data preparation, ~2 minutes).

## Parse Arguments

From `$ARGUMENTS`:
- If a number is provided (e.g., `5`), pass `max_iterations=5` to the agent.
- If empty, pass `max_iterations=null` (agent loops until user stops it).
- Otherwise pass the raw argument as a `goal_override` string.

## Invoke Agent

Use the Task tool to delegate the research loop:

```
Task(
  subagent_type="autoresearch-agent",
  description="Run autonomous ML experiment loop",
  prompt="""
Run the autoresearch experiment loop in this project.

Configuration:
- max_iterations: <parsed from $ARGUMENTS, or null for continuous>
- goal_override: <only if user passed a string goal, otherwise null>

File context:
- program.md: research goal and constraints
- train.py: the training script to modify and iterate on
- prepare.py: data prep script (should already be run)

Run the research loop autonomously. Each iteration:
1. Read program.md for context and constraints
2. Propose a concrete experiment modification to train.py
3. Apply the change
4. Run: uv run train.py
5. Parse the validation metric (val_bpb — bits per byte, lower is better)
6. Keep change if improved, revert via git checkout if not
7. Log the result
8. Repeat

Stop when max_iterations is reached (if set) or when interrupted.
"""
)
```

## Display Result

Show the agent's final summary including:
- Iterations run
- Best val_bpb achieved
- Changes kept vs reverted
- Suggested next experiment

## Agent Recipe (Optional)

If `recipe.md` exists in the project root, pass it to the agent as additional context alongside `program.md`. An Agent Recipe is a versioned artifact that tracks the evolution of the research loop — not just the current state but *why* decisions were made.

**recipe.md format:**
```markdown
# Experiment Recipe

## What we've tried
- [date] tried X → result: Y → kept/reverted because Z

## Signal filtering policy
Only act on signals that: [specific criteria]. Ignore: [noise patterns].
Currently filtered out: [list of discard patterns discovered empirically]

## Staged autonomy level
Current stage: [1=human approves every change | 2=human reviews batches | 3=agent autonomous with daily review]
Move to next stage when: [specific metric or duration]

## What we've learned
- [non-obvious finding from iteration N]
```

**First run:** create a skeleton `recipe.md` with the initial signal filtering policy and current autonomy stage. The agent updates it after each batch of experiments.

**Why this matters:** The inner loop (this command) optimizes train.py. The outer loop (recipe.md evolution) optimizes how the inner loop operates. Signal filtering prevents "slop generation" where the agent chases metrics that don't represent real improvement. Staged autonomy prevents overcommitting to full automation before trust is established.

Source: Gavrilescu (2025) via Latent Space — "Autoresearch: The Feedback Loop Behind Self-Improving Agents"
