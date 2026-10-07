---
description: Reconcile the live codebase against an approved spec — detect and classify drift (spec-ahead / code-ahead / contradiction)
allowed-tools: Read, Task, Glob, Grep, Bash
argument-hint: [feature-name]
---

# Kiro Converge — Spec ↔ Code Reconciliation

Detects where the **live implementation has drifted from its approved spec**: features built that no spec item asked for, spec items never implemented, and design decisions the code contradicts. Where `validate-impl` asks "does the code satisfy the spec?", `converge` asks "have the spec and the code diverged, and in which direction?" — then recommends how to bring them back together.

## Parse Arguments
- Feature name: `$1` (optional)

## Auto-Detection Logic

**Perform detection before invoking the Subagent**:

**If no argument** (`$1` empty):
- Scan `specs/*/` for directories containing an approved `spec.json` (status past requirements/design approval)
- If exactly one active spec, use it
- If multiple, list them and ask the user which feature to converge

**If feature provided** (`$1` present):
- Use `specs/$1/` directly
- If the directory or its `spec.json` is missing, stop and report "No approved spec found for `$1`."

## Invoke Subagent

Reuse `validate-impl-agent` (no new agent needed) — it already reads spec/requirements/design/tasks and compares them against the implementation. Direct it here to produce a **bidirectional drift reconciliation** instead of a one-directional pass/fail:

```
Task(
  subagent_type="validate-impl-agent",
  description="Reconcile spec against live code",
  prompt="""
Mode: CONVERGE (bidirectional drift reconciliation — not GO/NO-GO)

Feature: {$1 or auto-detected}

Read these and treat the spec as the source of intent, the code as the source of truth:
- specs/{feature}/spec.json
- specs/{feature}/requirements.md
- specs/{feature}/design.md
- specs/{feature}/tasks.md
- .claude/steering/*.md
- the live implementation files these specs describe (Glob/Grep the codebase)

Compare the approved spec against the LIVE codebase in BOTH directions and emit a
reconciliation report. Classify every divergence as exactly one of:

- spec-ahead    — spec/requirements/design specifies something the code does NOT implement
                  (feature described but missing, requirement with no code, design element absent)
- code-ahead    — code implements behavior the spec does NOT cover
                  (feature/endpoint/flag/branch present with no requirement or design driver)
- contradiction — code implements the item but in a way that conflicts with the spec
                  (different approach than design mandates, requirement satisfied differently,
                   a decision the design explicitly ruled out)

For each drift item report:
- classification {spec-ahead | code-ahead | contradiction}
- what the spec says (with requirements.md#/design.md# anchor where possible)
- what the code does (with filepath:line)
- severity {Critical | Warning}
- recommended action (see the action guidance below)

Recommended-action guidance:
- spec-ahead    → implement the missing item, OR remove it from the spec if now obsolete
- code-ahead    → back-fill the spec (requirements/design/tasks), OR remove the unspecced code
- contradiction → decide which is authoritative, then update the other so they agree

Do NOT modify any files — this is a read-only reconciliation. Report only.
"""
)
```

## Display Result

Show the Subagent's reconciliation report to the user in this shape:

### Reconciliation Report — {feature}

| Drift item | Class | Severity | Spec says | Code does | Recommended action |
|------------|-------|----------|-----------|-----------|--------------------|
| ...        | spec-ahead / code-ahead / contradiction | Critical/Warning | requirements.md#... | filepath:line | ... |

**Summary**: N spec-ahead · N code-ahead · N contradictions

**If no drift**: "Spec and code are converged — no drift detected."

### Next Steps Guidance

- **Contradictions first** — these mean spec and code actively disagree; resolve authority before anything else.
- **spec-ahead** items → open/resume implementation via `/kiro:spec-impl {feature}`, or prune the obsolete spec item.
- **code-ahead** items → back-fill the spec (`/kiro:spec-requirements` / `/kiro:spec-design` / `/kiro:spec-tasks`), or delete the unspecced code.
- Re-run `/kiro:converge {feature}` after reconciling to confirm convergence.

## Notes

- Read-only: this command reports drift and recommends actions; it does not edit spec files or code.
- Reuses `validate-impl-agent` — no dedicated converge agent, keeping the harness lean.
- Complements `/kiro:validate-impl` (satisfaction check) and `/kiro:sync-docs` (doc drift). Converge covers spec↔code *intent* drift that neither of those catches.
