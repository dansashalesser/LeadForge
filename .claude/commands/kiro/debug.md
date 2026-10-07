---
description: Systematic debugging using 6-step triage methodology
allowed-tools: Read, Agent, Glob
argument-hint: <bug-description-or-error-message>
---

# Systematic Debug

## Parse Arguments
- Bug description: `$ARGUMENTS`

## Validation
- If `$ARGUMENTS` is empty, ask user: "Describe the bug, error message, or unexpected behavior you're seeing."

## Invoke Subagent

Delegate debugging to debug-agent:

```
Agent(
  subagent_type="debug-agent",
  description="Systematic bug triage",
  prompt="""
Bug description: {$ARGUMENTS}

Load `.claude/steering/tech.md` for tech stack context.
Follow the 6-step debug methodology: Reproduce → Localize → Reduce → Fix → Guard → Verify.

Return: root cause analysis, fix applied, and regression guard added.
"""
)
```

## Display Result

Show the debug report to the user.

### Next Steps Guidance

**If fix applied successfully**:
- Run verification: `/kiro:verify quick`
- If part of a spec feature: update the relevant tasks.md

**If root cause unclear**:
- Consider `/kiro:validate-adversarial` for deeper analysis
- Check if the issue is in an external dependency

**If reproduction failed**:
- Ask user for more context: environment, data, sequence of actions
