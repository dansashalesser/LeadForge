---
description: Review recent session, extract patterns, update memory
allowed-tools: Read, Task, Glob, Bash
---

# Kiro Reflect — Session Memory Mining

## Bootstrap Check

**Before invoking Subagent**, check if `.claude/memory/` exists:

Use Glob to check for `.claude/memory/hot-memory.md`:
- **Missing**: Bootstrap memory from templates before reflecting
  - Copy all files from `.claude/kiro/settings/templates/memory/` to `.claude/memory/`
  - Create `.claude/memory/glacier/` directory
  - Create `.claude/memory/glacier/index.md` with empty catalog
- **Exists**: Proceed to reflection

## Invoke Subagent

Delegate reflection to reflect-agent:

Use the Task tool to invoke the Subagent:

```
Task(
  subagent_type="reflect-agent",
  description="Mine session for observations and patterns",
  prompt="""
Review the current session and update memory files.

File patterns to read:
- .claude/memory/*.md
- .claude/memory/meta/*.md
- .claude/kiro/settings/rules/memory-conventions.md

Use `git log --oneline -20` for recent session context.
Use `git diff HEAD~5..HEAD --stat` for change summary.

JIT Strategy: Fetch codebase files when needed, not upfront.
"""
)
```

## Display Result

Show Subagent summary to user:

### Reflection Complete:
- New observations captured
- Patterns promoted (if any)
- Hot memory updated
- Consistency issues flagged (if any)
- Observation count / cap status

## Notes

- Max 5 new observations per reflect pass
- Observations are append-only (never edit past entries)
- Promote to patterns when 3+ observations cluster on same theme
- Hot memory must stay under 50 lines
- Run after significant sessions: spec completion, major implementation, debugging sessions
