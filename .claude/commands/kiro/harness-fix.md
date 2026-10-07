---
name: harness-fix
description: Capture an agent behavioral mistake and encode a prevention rule so it never happens again
---

# /kiro:harness-fix

## Usage
```
/kiro:harness-fix [description of what went wrong]
```

## What This Does

When you observe the agent making a **repeatable behavioral mistake** (not a one-off coding bug), this command analyzes the failure and proposes a targeted rule to prevent it from recurring.

**Examples of harness-addressable mistakes:**
- "Agent keeps creating new utility files instead of reusing existing ones"
- "Agent runs the full test suite instead of targeted tests"
- "Agent modifies files outside the spec scope"
- "Agent doesn't check for existing implementations before writing new code"

**NOT for this command** (normal coding errors that tests catch):
- Wrong API usage, typos, logic bugs — these are one-off, not behavioral patterns

## Execution

Spawn `harness-fix-agent` with:
- The user's description of the mistake
- The current conversation context (what was the agent doing when it made the mistake)
