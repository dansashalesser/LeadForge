---
description: Create comprehensive technical design for a specification
allowed-tools: Read, Task
argument-hint: <feature-name> [-y]
---

# Technical Design Generator

## Parse Arguments
- Feature name: `$1`
- Auto-approve flag: `$2` (optional, "-y")

## Validate
Check that requirements have been completed:
- Verify `specs/$1/` exists
- Verify `specs/$1/requirements.md` exists

If validation fails, inform user to complete requirements phase first.

## Invoke Subagent

Delegate design generation to spec-design-agent:

Use the Task tool to invoke the Subagent with file path patterns:

```
Task(
  subagent_type="spec-design-agent",
  description="Generate technical design and update research log",
  prompt="""
Feature: $1
Spec directory: specs/$1/
Auto-approve: {true if $2 == "-y", else false}

File patterns to read:
- specs/$1/*.{json,md}
- .claude/steering/*.md
- .claude/kiro/settings/rules/design-*.md
- .claude/kiro/settings/templates/specs/design.md
- .claude/kiro/settings/templates/specs/research.md

Discovery: auto-detect based on requirements
Mode: {generate or merge based on design.md existence}
Language: respect spec.json language for design.md/research.md outputs
"""
)
```

## Display Result

Show Subagent summary to user briefly (2-3 lines max).

## Review & Approve via Proof

After the subagent completes:

1. Read `~/.claude/skill-library/proof-collaborative-review/SKILL.md` and follow it with:
   - File: `specs/$1/design.md`
   - Title: "$1 — Design Review"

2. The skill starts the Proof server (installing once if needed), publishes the design document, and presents a review URL. Wait for the user to finish reviewing and signal done.

3. After the Proof review completes, the skill returns the final markdown. Write it back to `specs/$1/design.md`.

4. Update `specs/$1/spec.json`:
   - Set `approvals.design.approved: true`
   - Update `updated_at` to current timestamp

5. Confirm to user:
   > ✅ Design approved. Optionally run `/kiro:validate-design $1` for quality review. Then run `/kiro:spec-grill $1` to align domain language before tasks.