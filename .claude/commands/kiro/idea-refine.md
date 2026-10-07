---
description: Structured ideation to refine a vague idea into a clear spec-ready brief — or, for program-scale ideas, chart/update a program map and hand off the first decomposed slice
allowed-tools: Read, Write, Glob, Agent
argument-hint: <rough-idea-description>
---

# Idea Refinement

## Parse Arguments
- Rough idea: `$ARGUMENTS`

## Validation
- If `$ARGUMENTS` is empty, ask user: "What's the rough idea or problem you want to explore?"

## Scale Check

Before delegating, check whether this idea is program-scale (see `Skill("issue-triage-routing")`
axis 4): does it span multiple decisions that each deserve their own spec, or does it map to one?

- Generate a kebab-case slug from the idea (same convention as `spec-init`'s feature-name).
- Glob `specs/_maps/*.md` for an existing map matching this slug (exact or close match).

**If a matching map already exists:** load it. Skip straight to "Decompose Next Slice" below —
this is a continuation of a program already charted, not a fresh idea.

**If no map exists and the idea reads as program-scale:** this is a **chart** pass — go to
"Chart the Map".

**Otherwise (feature-scale, no map):** run the normal single-brief flow — go to "Single-Brief
Refinement".

## Chart the Map

1. Read `.claude/steering/product.md` and `.claude/steering/tech.md` for project context.
2. Delegate to idea-refine-agent to breadth-first surface the fog:

```
Agent(
  subagent_type="idea-refine-agent",
  description="Chart program map",
  prompt="""
Rough idea (program-scale): {$ARGUMENTS}

Load `.claude/steering/product.md` and `.claude/steering/tech.md` for project context.

This idea is too large for one spec. Breadth-first brainstorm the distinct decisions/features
it contains — don't go deep on any one of them yet. For each, write one line: what it is and
why it's a separate concern from the others.

Return:
- destination: one-paragraph statement of what "done" looks like for the whole idea
- fog: ordered list of not-yet-decided items (each one line, ticket-sized — i.e. small enough
  to become a single spec)
- out_of_scope: anything explicitly excluded, if mentioned or inferable
"""
)
```

3. Write `specs/_maps/<slug>.md` from `.claude/kiro/settings/templates/specs/map-init.md`:
   - `{{FEATURE_NAME}}` → slug
   - `{{TIMESTAMP}}` → current ISO 8601 timestamp
   - `{{DESTINATION}}` → returned destination
   - Fog list → returned fog items (one bullet each)
   - Out of scope → returned out_of_scope items (or leave the placeholder if none)

4. Tell the user:
   > 🗺️ Program map created at `specs/_maps/<slug>.md` — {{N}} items in the fog.

5. Continue to "Decompose Next Slice" — charting doesn't stop here; the first slice still
   needs to reach `spec-init` in this same pass. That's the automation contract: charting a
   map is not a dead end the user must remember to come back to.

## Decompose Next Slice

1. Read `specs/_maps/<slug>.md`. Take the first item under "Not yet specified".
2. Re-triage that single item with `Skill("issue-triage-routing")` (it is now feature-scale by
   construction — apply rows 3–5 only).
3. Report the chosen slice and its route to the user, then hand off:
   - Route SPEC → suggest `/kiro:spec-quick <slice description>` or `/kiro:spec-init <slice description>`
   - Route ONE-SHOT → suggest implementing directly
   - Route CLARIFY → run "Single-Brief Refinement" below on just this slice
4. Note the remaining fog count: "N more items still in the fog — re-run `/kiro:idea-refine`
   after this slice's spec reaches tasks approval; it picks up the next one automatically
   (see `/kiro:spec-tasks`, which updates the map on approval)."

## Single-Brief Refinement (feature-scale)

Delegate ideation to idea-refine-agent:

```
Agent(
  subagent_type="idea-refine-agent",
  description="Refine idea into spec brief",
  prompt="""
Rough idea: {$ARGUMENTS}

Load `.claude/steering/product.md` and `.claude/steering/tech.md` for project context.
Refine the rough idea into a clear, actionable brief ready for `/kiro:spec-init`.

Return: problem statement, proposed solution brief, key constraints, and suggested spec-init description.
"""
)
```

## Display Result

Show the refined brief (or the decomposed slice) to the user.

### Next Steps Guidance

**If user approves the brief**:
- Start the spec workflow: `/kiro:spec-quick <refined-description>` or `/kiro:spec-init <refined-description>`

**If user wants to iterate**:
- Re-run with updated idea: `/kiro:idea-refine <updated-description>`
