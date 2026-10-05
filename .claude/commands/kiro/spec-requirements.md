---
description: Generate comprehensive requirements for a specification
allowed-tools: Read, Task
argument-hint: <feature-name>
---

# Requirements Generation

## Parse Arguments
- Feature name: `$1`

## Validate
Check that spec has been initialized:
- Verify `specs/$1/` exists
- Verify `specs/$1/spec.json` exists

If validation fails, inform user to run `/kiro:spec-init` first.

## Invoke Subagent

Delegate requirements generation to spec-requirements-agent:

Use the Task tool to invoke the Subagent with file path patterns:

```
Task(
  subagent_type="spec-requirements-agent",
  description="Generate EARS requirements",
  prompt="""
Feature: $1
Spec directory: specs/$1/

File patterns to read:
- specs/$1/spec.json
- specs/$1/requirements.md
- .claude/steering/*.md
- .claude/kiro/settings/rules/ears-format.md
- .claude/kiro/settings/templates/specs/requirements.md

Mode: generate
"""
)
```

## Display Result

Show Subagent summary to user briefly (2-3 lines max).

## Review & Approve via Proof

After the subagent completes:

1. Read `~/.claude/skill-library/proof-collaborative-review/SKILL.md` and follow it with:
   - File: `specs/$1/requirements.md`
   - Title: "$1 — Requirements Review"

2. The skill starts the Proof server (installing once if needed), publishes the requirements document, and presents a review URL. Wait for the user to finish reviewing and signal done.

3. After the Proof review completes, the skill returns the final markdown. Write it back to `specs/$1/requirements.md`.

4. Update `specs/$1/spec.json`:
   - Set `approvals.requirements.approved: true`
   - Update `updated_at` to current timestamp

5. Confirm to user:
   > ✅ Requirements approved. Optionally run `/kiro:validate-gap $1` (brownfield projects) before proceeding. Then run `/kiro:spec-design $1` to generate the design.
