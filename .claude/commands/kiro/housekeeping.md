---
description: Prune memory, archive old observations, audit consistency
allowed-tools: Read, Task, Glob, Bash
---

# Kiro Housekeeping — Memory Maintenance

## Pre-check

Use Glob to verify `.claude/memory/` exists. If missing, tell user to run `/kiro:reflect` first to bootstrap.

## Invoke Subagent

Delegate maintenance to housekeeping-agent:

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

JIT Strategy: Fetch codebase files when needed, not upfront.
"""
)
```

## Display Result

Show Subagent summary to user:

### Housekeeping Complete:
- Observations archived (count)
- Hot memory pruned (line count before/after)
- Stale action items flagged
- Entity format validated
- L0 headers verified
- Glacier index rebuilt

## Notes

- Run when observations.md exceeds 50 entries (stop-hook will nudge)
- Run periodically to keep memory lean and consistent
- Archived observations go to `.claude/memory/glacier/` with YAML frontmatter
- Completed action items >10 are also archived
