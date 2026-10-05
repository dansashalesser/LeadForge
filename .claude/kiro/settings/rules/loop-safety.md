# Loop Safety — Preventing Repetitive Agent Cycles

This is a cross-cutting rule that applies to ALL agents. When agents get stuck in loops, they waste tokens and produce no forward progress. These detection patterns and responses prevent runaway cycles.

## Detection Patterns

### Pattern 1: Repeated File Edits
The same file is edited 3+ times within a single task without introducing new information or making measurably different changes.

**Signal**: Edit tool called on the same file path 3+ times, with the changes addressing the same issue.

### Pattern 2: Repeated Command Execution
The same command (or substantially similar command) is run 3+ times with similar arguments and similar results.

**Signal**: Bash tool called with the same command pattern 3+ times, producing the same or similar output.

### Pattern 3: Revert-Edit Cycles
An agent makes a change, observes a failure, reverts the change, then re-applies a similar change.

**Signal**: Edit followed by Edit that restores previous content, followed by another Edit attempt.

### Pattern 4: Expanding Scope Creep
An agent starts fixing one thing, discovers another issue, fixes that, discovers another, in an ever-widening circle.

**Signal**: Each successive fix touches a different file or module than the original task scope.

## Response Protocol

When any detection pattern is triggered:

1. **Pause**: Stop the current action immediately
2. **Report**: State what is looping and how many iterations have occurred
3. **Diagnose**: Explain why the loop is happening (if determinable)
4. **Reduce scope**: Propose a smaller, achievable goal that breaks the loop
5. **Escalate**: If scope reduction isn't possible, ask for human guidance

**Template response**:
```
Loop detected: {pattern description}
  Iterations: {count}
  Cause: {diagnosis}
  Recommendation: {reduced scope action OR "manual intervention needed"}
```

## Prevention Strategies

- Before attempting a fix, verify the diagnosis is correct (read the error, don't guess)
- After a failed fix attempt, gather NEW information before trying again
- If a fix doesn't work on the first try, consider that the diagnosis may be wrong
- Keep a mental count of attempts — if approaching 3, switch strategies

## Relationship to Attempt Caps

Some agents (like fix-build-agent) have explicit attempt caps. This rule supplements those caps by detecting subtler loops that don't trigger attempt counters (e.g., editing different files for the same root cause).
