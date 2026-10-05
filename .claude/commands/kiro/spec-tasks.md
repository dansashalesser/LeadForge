---
description: Generate implementation tasks for a specification
allowed-tools: Read, Task
argument-hint: <feature-name> [-y] [--sequential]
---

# Implementation Tasks Generator

## Parse Arguments
- Feature name: `$1`
- Auto-approve flag: `$2` (optional, "-y")
- Sequential mode flag: `$3` (optional, "--sequential")

## Validate
Check that design has been completed:
- Verify `specs/$1/` exists
- Verify `specs/$1/design.md` exists
- Determine `sequential = ($3 == "--sequential")`
- Check `specs/$1/spec.json` for `approvals.grill.approved`: if false, warn the user:
  > ⚠️ Domain grill phase was skipped — terminology and decisions in requirements/design may be unvalidated. Run `/kiro:spec-grill $1` first, or proceed anyway? (yes/no)

If validation fails, inform user to complete design phase first.

## Invoke Subagent

Delegate task generation to spec-tasks-agent:

Use the Task tool to invoke the Subagent with file path patterns:

```
Task(
  subagent_type="spec-tasks-agent",
  description="Generate implementation tasks",
  prompt="""
Feature: $1
Spec directory: specs/$1/
Auto-approve: {true if $2 == "-y", else false}
Sequential mode: {true if sequential else false}

File patterns to read:
- specs/$1/*.{json,md}
- .claude/steering/*.md
- .claude/kiro/settings/rules/tasks-generation.md
- .claude/kiro/settings/rules/tasks-parallel-analysis.md (include only when sequential mode is false)
- .claude/kiro/settings/templates/specs/tasks.md

Mode: {generate or merge based on tasks.md existence}
Instruction highlights:
- Map all requirements to tasks and list requirement IDs only (comma-separated) without extra narration
- Promote single actionable sub-tasks to major tasks and keep container summaries concise
- Apply `(P)` markers only when parallel criteria met (omit in sequential mode)
- Mark optional acceptance-criteria-focused test coverage subtasks with `- [ ]*` only when deferrable post-MVP
"""
)
```

## Display Result

Show Subagent summary to user briefly (2-3 lines max).

## Review & Approve via Proof

After the subagent completes:

1. Read `~/.claude/skill-library/proof-collaborative-review/SKILL.md` and follow it with:
   - File: `specs/$1/tasks.md`
   - Title: "$1 — Tasks Review"

2. The skill starts the Proof server (installing once if needed), publishes the task list, and presents a review URL. Wait for the user to finish reviewing and signal done.

3. After the Proof review completes, the skill returns the final markdown. Write it back to `specs/$1/tasks.md`.

4. Update `specs/$1/spec.json`:
   - Set `approvals.tasks.approved: true`
   - Update `updated_at` to current timestamp

5. **Update parent program map (if one exists):**
   - Glob `specs/_maps/*.md`. Grep each for a "Not yet specified" line matching feature `$1`
     (by slug or by a close text match to `$1`'s description).
   - If found in some `specs/_maps/<name>.md`:
     - Move that line from "Not yet specified" to "Decisions so far" as:
       `- [<slice title>](specs/$1/) — <one-line gist from requirements.md's summary>`
     - If "Not yet specified" is now empty, note in the map: `_(fog clear — all slices spec'd)_`
   - If no map references `$1`, skip silently — this spec wasn't part of a charted program.

6. Confirm to user:
   > ✅ Tasks approved. **Important**: clear conversation context before implementation. Then run `/kiro:spec-impl $1 1.1` to start the first task (clear context between each task for clean state).
   >
   > (if step 5 updated a map) 🗺️ Program map `specs/_maps/<name>.md` updated — N items left in the fog.
