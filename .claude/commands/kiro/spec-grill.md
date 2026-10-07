---
description: Run domain grilling session against requirements and design, updating both docs inline
allowed-tools: Read, Write, Edit, Bash, Glob
argument-hint: <feature-name>
---

# Domain Grill Session

## Parse Arguments
- Feature name: `$1`

## Validate

Check that design phase is complete:
- Verify `specs/$1/` exists
- Verify `specs/$1/design.md` exists
- Read `specs/$1/spec.json` and confirm `approvals.design.approved` is true

If validation fails, inform user to complete the design phase first via `/kiro:spec-design $1`.

## Prepare Context

Read the following files in full before asking any questions:
- `specs/$1/requirements.md`
- `specs/$1/design.md`
- `specs/$1/CONTEXT.md` (if it exists — load the existing glossary)
- Any files in `specs/$1/docs/adr/` (if they exist — load prior decisions)

Also explore the codebase for related source files that touch this feature domain, so you can cross-reference terminology against real code.

## Run Grill Session

Read `~/.claude/skill-library/grill-with-docs/SKILL.md` and follow it.

Adapt its scope to this spec context:
- **Domain to challenge**: the requirements and design documents read above
- **CONTEXT.md location**: `specs/$1/CONTEXT.md` (spec-scoped, not project root)
- **ADR location**: `specs/$1/docs/adr/`
- **Files to update inline as decisions crystallise**: `specs/$1/requirements.md` and `specs/$1/design.md`

Ask questions one at a time. Wait for the user's response before continuing. For each question, provide your recommended answer.

Continue until the user signals the grilling is complete (e.g. "done", "looks good", "that's enough", "move on").

## Finalise

After the session ends:

1. Write all resolved terminology to `specs/$1/CONTEXT.md` using the format in the skill's `resources/CONTEXT-FORMAT.md`
2. Write any warranted ADRs to `specs/$1/docs/adr/` using the format in `resources/ADR-FORMAT.md`
3. Update `specs/$1/spec.json`:
   - Set `approvals.grill.approved: true`
   - Update `updated_at` to current ISO 8601 timestamp
4. Confirm to user:
   > ✅ Domain grill complete. Run `/kiro:spec-tasks $1` to generate implementation tasks.
