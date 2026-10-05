---
description: Attempt automatic resolution of build errors with surgical fixes
allowed-tools: Read, Task
argument-hint: [max-attempts]
---

# Build Error Resolver

## Parse Arguments
- Max attempts: `$1` (optional, default: 3, hard cap: 3)

## Invoke Subagent

Delegate build resolution to fix-build-agent:

```
Task(
  subagent_type="fix-build-agent",
  description="Resolve build errors",
  prompt="""
Max attempts: {$1 or 3}

Read .claude/steering/tech.md to discover build and type-check commands.
If not available, auto-detect from package.json, pyproject.toml, Cargo.toml, go.mod, or Makefile.

Run diagnostics, categorize errors, and apply minimal surgical fixes.
Hard cap: {max attempts} attempts. Stop and report if still failing.
"""
)
```

## Display Result

Show the resolution report to the user.

### Next Steps Guidance

**If RESOLVED**:
- Build errors fixed
- Run `/kiro:verify quick` to confirm full pipeline passes

**If UNRESOLVED**:
- Agent hit the attempt cap or encountered errors it cannot fix
- Review the remaining errors listed in the report
- Fix manually and re-run `/kiro:verify`
