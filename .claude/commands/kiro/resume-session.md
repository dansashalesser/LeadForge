---
description: Resume a previously saved session
allowed-tools: Read, Glob, Bash
argument-hint: [session-name]
---

# Resume Session

## Parse Arguments
- Session name: `$1` (optional)

## Session Resolution

**If `$1` is provided**:
- Look for `.claude/memory/sessions/session-*-{$1}.md`
- If not found, try `.claude/memory/sessions/*{$1}*`
- If still not found, report error with available sessions

**If `$1` is empty**:
- Use Glob to find all `.claude/memory/sessions/session-*.md`
- Sort by filename (date-based) and pick the most recent
- If no sessions exist, report: "No saved sessions found. Use `/kiro:save-session` to create one."

## Display Session

Read the session file and display it as a structured briefing:

```
Resuming session: {session name}
Saved: {date from filename}
────────────────────────────────

{contents of session file}

────────────────────────────────
```

## Auto-Orient (if Progress Tracker present)

If the session file contains a `## Progress Tracker` section:

1. Show commits since the saved baseline:
   ```bash
   git log --oneline {git_baseline}..HEAD
   ```
   If the baseline commit doesn't exist (rebased/squashed), skip this step.

2. Check blocking issues: For each listed blocker, run a quick check (e.g., file exists, command succeeds) and report which are resolved vs. still open.

3. Present a **Pickup Briefing**:
   ```
   Pickup Briefing
   ────────────────────────────────
   Feature: {feature}
   Commits since save: {count} ({summary})
   Blockers resolved: {list or "None checked"}
   Blockers remaining: {list or "None"}
   Suggested next action: {next action from tracker}
   ────────────────────────────────
   ```

## Await User Decision

After displaying the session (and pickup briefing if available), ask:
"What would you like to work on from this session?"

Do NOT auto-execute any steps. The user decides what to do next.

## List Mode

If `$1` is `list`:
- Use Glob to find all `.claude/memory/sessions/session-*.md`
- Display each with name and date
- Do not load any session content
