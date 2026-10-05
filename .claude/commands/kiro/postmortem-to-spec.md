---
description: Turn an incident postmortem into a draft spec — closes the deploy-to-plan loop
allowed-tools: Bash, Read, Write, Glob, SlashCommand
argument-hint: <postmortem text, or path to a postmortem file/incident doc>
---

# Postmortem → Spec

<background_information>
- **Mission**: Most postmortems end at "here's what happened" and the corrective action
  lives only in a doc nobody schedules. This command drafts the corrective action
  straight into `specs/` as a normal spec-init call, so it enters the same pipeline as
  any other planned work instead of dying in an incident channel.
- **Not a substitute for the postmortem itself** — this runs *after* root cause is known.
  If root cause isn't established yet, do the postmortem first.
</background_information>

<instructions>
## Core Task

$ARGUMENTS is either raw postmortem text, or a path to a file containing it. Read the
file if it's a path (`Read`); otherwise treat $ARGUMENTS as the text directly.

## Execution Steps

1. **Extract from the postmortem:**
   - What broke (user-visible symptom)
   - Root cause (the actual mechanism, not the symptom)
   - Why it wasn't caught earlier (missing test, missing check, missing monitor)
   - The corrective action already proposed, if any — do not invent one the postmortem
     doesn't state; if none is stated, note that explicitly rather than guessing

2. **Compose the spec-init project description** from the corrective action (not the
   incident narrative) — spec-init wants "build X", not "X broke because Y":
   ```
   [Corrective action as a feature/fix description]. Prevents recurrence of: [one-line
   symptom]. Root cause: [one-line mechanism].
   ```

3. **Call spec-init:**
   ```
   /kiro:spec-init {composed description}
   ```

4. **After spec-init creates the directory**, append an "## Incident Provenance" section
   to the generated `requirements.md` with: postmortem source (file path or "pasted
   text"), date, the extracted root cause, and the detection gap — so a later
   `decision-archaeology` dig on the resulting code finds the incident, not just a commit
   message.

## Important Constraints
- Do not fabricate a root cause or corrective action absent from the input — if the
  postmortem is incomplete, say which piece is missing and stop before calling spec-init
  rather than filling the gap with a guess
- One postmortem → one spec. If the postmortem describes multiple independent failures,
  ask the user whether to split into separate spec-init calls before proceeding
</instructions>

## Output Description
1. **Extracted Summary**: symptom / root cause / detection gap / corrective action (2-3
   lines each, or "not stated" if the postmortem omits it)
2. **Composed Description**: the exact string passed to `/kiro:spec-init`
3. **Created Spec**: path, plus confirmation the Incident Provenance section was appended
4. **Next Step**: `/kiro:spec-requirements <feature-name>` to continue the normal pipeline

## Safety & Fallback
- **No root cause in input**: stop, report what's missing, do not call spec-init on a
  guess
- **Multiple failures in one postmortem**: ask the user to confirm split vs. single spec
  before proceeding
- **spec-init naming conflict**: handled by spec-init itself (numeric suffix)
