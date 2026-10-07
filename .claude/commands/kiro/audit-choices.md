---
description: Audit decisions made where the spec was silent and record them in specs/<feature>/choices.md
allowed-tools: Read, Grep, Glob, Bash, Edit, Write, Skill
argument-hint: <feature-name> [--close]
---

# Choices Ledger Audit

Reconstructs the decisions this implementation made where the spec said nothing,
verdicts each one, and appends them to the feature's choices ledger.

Runs automatically as the last step of `/kiro:spec-impl`. Invoke it directly to
re-audit a pass, to audit work done outside the spec pipeline, or to consolidate
the ledger at spec close.

## Parse Arguments

- Feature name: `$1` (required)
- `--close` in `$2`: run Phase 7 consolidation instead of a normal pass audit

## Validate

1. `specs/$1/` exists. If not: list available features under `specs/` and stop.
2. `specs/$1/requirements.md` or `specs/$1/design.md` exists — without a spec
   there is no silence to audit against. If neither: say so and stop.
3. If `specs/$1/choices.md` does not exist, create it with the header:
   ```markdown
   # Choices Ledger — $1

   Decisions made where the spec was silent. Appended per implementation pass.
   Verdicts: `sound` (any reasonable implementer would agree) / `unsound` (needs
   rework) / `needs-user` (a preference the agent does not own).
   ```

## Execute

Invoke `Skill("auditing-spec-choices")` and follow its workflow.

- Normal run → Phases 1–6.
- `--close` → Phase 7 only (resolve provisionals, drop reverted, dedupe, promote).

Three constraints from the skill, restated here because they are the ones most
likely to be dropped under time pressure:

- **Change no code.** This command is read-and-record. Findings route to the next
  pass; they are not fixed here.
- **Never block.** Every `needs-user` entry carries a reversible provisional call
  so an unattended run completes. An irreversible provisional is reported as such,
  not dressed up as reversible.
- **Least-confident first.** Order the ledger so the entries most needing
  attention are read before attention runs out.

## Display Result

```
## Choices Ledger — $1 (pass N)

  sound        <n>
  unsound      <n>   ← rework candidates
  needs-user   <n>   ← awaiting your call, running on provisionals

<the needs-user entries, in full>

Ledger: specs/$1/choices.md
```

Then, only if a signal actually fired (Phase 6):

- Entries clustering on one slice → name the slice and recommend reslicing it.
- Many `needs-user` in one pass → the Decision-Budget Gate in `/kiro:spec-impl`
  Phase -1 should have caught this; say so.
- Repeated `unsound` on one theme → propose the missing convention rather than
  per-instance fixes.

Say nothing when no signal fired. An audit that always reports a problem stops
being read.

## Next Steps

- Resolve open calls: answer the `needs-user` entries, then re-run to record them
- Continue implementing: `/kiro:spec-impl $1`
- Close the feature: `/kiro:audit-choices $1 --close`
