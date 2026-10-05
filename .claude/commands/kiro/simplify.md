---
description: Behavior-preserving code simplification with Chesterton's Fence principle
allowed-tools: Read, Agent, Glob
argument-hint: <file-path-or-feature-name>
---

# Code Simplification

## Parse Arguments
- Target: `$ARGUMENTS` (file path, glob pattern, or feature name)

## Validation
- If `$ARGUMENTS` is empty, ask user: "Which file(s) or feature do you want to simplify?"

## Invoke Subagent

Delegate simplification to simplify-agent:

```
Agent(
  subagent_type="simplify-agent",
  description="Behavior-preserving simplification",
  prompt="""
Target: {$ARGUMENTS}

If target looks like a feature name, find files via specs/{target}/tasks.md or git diff.
If target is a file path or glob, use directly.

Apply behavior-preserving simplification following Chesterton's Fence principle.
Return: changes made, behavior verification, and any complexity left intentionally.
"""
)
```

## Display Result

Show the simplification report to the user.

### Next Steps Guidance

**If changes made**:
- Run verification: `/kiro:verify quick`
- Review the diff to confirm behavior preservation

**If no changes needed**:
- Code is already clean — no action required

**If intentional complexity flagged**:
- Review flagged sections — they may need documentation rather than simplification
