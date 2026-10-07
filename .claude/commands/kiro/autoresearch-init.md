---
description: Interactive setup for autoresearch — asks leading questions, then generates program.md, train.py, and prepare.py
allowed-tools: Task, Read, Glob, Bash
argument-hint: [optional one-line research goal]
---

# AutoResearch Init — Interactive Project Setup

## Pre-checks

1. Check if `program.md` already exists. If so, warn:
   > `program.md` already exists. Re-running will overwrite it. Continue? (y/n)

2. Check `uv` is available: `Bash("uv --version")`. If not, warn but continue — files can be generated without it.

## Invoke Agent

Use the Task tool to delegate the interactive setup:

```
Task(
  subagent_type="autoresearch-init-agent",
  description="Interactive autoresearch project setup",
  prompt="""
Set up an autoresearch project in this repository.

User's initial description (if provided): $ARGUMENTS

Run the interactive interview, then generate:
1. program.md — the research brief
2. prepare.py — data preparation script
3. train.py — starter training script

See agent instructions for the full interview protocol and file generation templates.
"""
)
```

## Display Result

Show the agent's summary:
- Files created
- Research goal captured
- Next steps: `uv sync && uv run prepare.py`, then `/kiro:autoresearch`
