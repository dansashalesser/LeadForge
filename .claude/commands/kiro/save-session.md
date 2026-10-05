---
description: Save current session state for resumption later
allowed-tools: Read, Task
argument-hint: [session-name]
---

# Save Session

## Parse Arguments
- Session name: `$1` (optional, defaults to auto-generated timestamp)

## Name Resolution

If `$1` is empty, generate name from current date: `session-{YYYY-MM-DD}-{HHmm}`

If `$1` is provided, use it as the session name.

## Invoke Subagent

Delegate session capture to save-session-agent:

```
Task(
  subagent_type="save-session-agent",
  description="Capture session state",
  prompt="""
Session name: {resolved name}
Output path: .claude/memory/sessions/session-{YYYY-MM-DD}-{name}.md

Analyze the current conversation and workspace to capture:

**Progress Tracker** (populate from git log and spec files):
- Feature name and spec path (if exists)
- Git baseline (earliest relevant commit) and HEAD commit hash
- Tasks completed vs. remaining (with spec task IDs if applicable)
- Blocking issues (or "None")
- Next action (specific enough to execute without re-reading context)

**Narrative sections**:
1. What worked (with evidence — test results, build output, successful changes)
2. What did NOT work (with exact error messages and reasons)
3. What has NOT been tried yet (actionable approaches)
4. Current state of modified files (from git diff and git status)
5. The single most important next step

Use the session template at .claude/kiro/settings/templates/memory/sessions/session-template.md.
Write the session file to the output path.
"""
)
```

## Display Result

Confirm session saved and show the file path:

```
Session saved: .claude/memory/sessions/session-{date}-{name}.md
Resume later with: /kiro:resume-session {name}
```
