---
description: Pre-spec preference elicitation — Socratic session that surfaces declarative vs. imperative and strategic vs. tactical preferences before spec-init runs. Writes prefs.md to the spec directory.
allowed-tools: Read, Write, Bash, Glob
argument-hint: <feature-description-or-name>
---

# Preference Elicitation

<background_information>
- **Mission**: Surface what the user actually wants before the agent assumes defaults — run a structured 5-question Socratic session that produces a `prefs.md` preference brief
- **Success Criteria**:
  - User has articulated at least one imperative preference (non-negotiable) and one declarative preference (outcome-only)
  - Strategic preferences (firm-wide) are flagged separately from tactical ones (this project only)
  - Output written to `specs/{feature}/prefs.md` if a matching spec directory exists, otherwise `./prefs.md`
</background_information>

<instructions>

## Background

The bottleneck in agentic engineering is not model capability — it is human preference-articulation. When you don't state what you value, the agent picks defaults that may diverge from your actual requirements. This session surfaces where your preferences fall before implementation assumes any of them.

Two dimensions matter:

|  | **Declarative** (outcome, not path) | **Imperative** (exact path specified) |
|---|---|---|
| **Strategic** (firm-wide) | "Prioritize safety over throughput across all projects" | "Always spawn auditor sub-agent before push" |
| **Tactical** (this project) | "Make this endpoint fast — you choose how" | "Use this exact payment queue flow with these steps" |

## Execution Steps

### Step 1: Locate output file

Use Glob to check `specs/*/` for a directory matching the feature from `$ARGUMENTS`. If found, set output path to `specs/{feature}/prefs.md`. Otherwise set output path to `./prefs.md`.

Announce:
```
Starting preference elicitation for: {feature or description}.
I'll ask 5 questions — answer freely, there are no wrong answers.
Type "skip" to pass on any question.
```

### Step 1.5: Confirm This Is Worth Building

Before the 5-question session, ask once:

> Quick check before we go deeper — what's the cost of NOT building this? If nothing breaks, no one waits, and it's not required by something else, is it still worth the spec/implementation effort?

This is advisory, not a gate — do not block on the answer. If the user says "yes, proceed" or equivalent, continue directly to Step 2. If they hesitate or push back, record their answer verbatim; it becomes the "Why This Exists" line in Step 3.

### Step 2: Run the 5-question Socratic session

Ask **ONE question at a time**. Wait for the user's full response before asking the next. Do not batch or preview all questions upfront.

**Question 1 — Outcome priority**
> What are you optimizing for with this feature? Pick your top 1–2:
> (a) correctness / reliability  (b) performance / speed  (c) maintainability / readability
> (d) speed-to-ship  (e) security  (f) cost  (g) something else?

**Question 2 — Irreversible decisions (imperative candidates)**
> What decisions, if made wrongly by the agent, would be hardest to undo or most costly to reverse?
> Think about: data schemas, external API choices, security boundaries, architectural patterns, payment flows.

**Question 3 — Agent latitude (declarative candidates)**
> What aspects of this implementation are you happy to leave entirely to the agent's judgment — as long as the outcome matches what you described in Q1?
> What don't you care how it's done?

**Question 4 — Scope (strategic vs tactical)**
> Of the constraints you've described so far — which apply ONLY to this feature, and which should hold for everything you build?
> (Firm-wide preferences may belong in permanent harness rules rather than just this spec.)

**Question 5 — Hard constraints**
> Is there anything non-negotiable in this feature — a specific technology, sequence, external service, or line you will not cross regardless of other trade-offs?

### Step 3: Synthesize and write prefs.md

After receiving all 5 answers (or skips), produce a structured file:

```markdown
# Preference Brief — {feature or description}

## Why This Exists

[Answer from Step 1.5, or "Not challenged — proceeded directly" if the user skipped straight to yes]

## Imperative Preferences (non-negotiable — agent must follow exactly)

[From Q2 and Q5 responses — list each as a concrete constraint]

## Declarative Preferences (outcome-focused — agent chooses how)

[From Q1 and Q3 responses — list the desired outcome, not the path]

## Strategic Preferences (firm-wide — flag for harness promotion)

[From Q4 responses — preferences that should hold across ALL projects]
- ...
  ⚑ Consider encoding as a MUST rule in CLAUDE.md or as a harness skill via /kiro:steering

## Tactical Preferences (this feature only)

[From Q4 responses — project-specific constraints that don't generalize]

## Agent Latitude

[From Q3 — explicit list of implementation decisions left to the agent]
```

Write the file, then display its content to the user for review.

### Step 4: Offer harness promotion (if strategic preferences identified)

If the strategic preferences section is non-empty, offer:

```
Some preferences appear firm-wide. Would you like to promote them to permanent harness rules?
If yes, I'll add them via /kiro:steering conventions.
```

Wait for response. If yes, follow the steering skill to create or update the appropriate rule.

</instructions>

## Output Description

After writing `prefs.md`:

```
✅ Preference brief saved to {path}/prefs.md

Summary:
- Imperative: {N} non-negotiable constraints
- Declarative: {N} outcome-focused preferences
- Strategic (firm-wide): {list first 1-2, or "none"}
- Tactical (this feature): {list first 1-2}

The agent will use prefs.md as context during requirements and design generation.
```

## Safety & Fallback

- If `$ARGUMENTS` is empty, open with: "What feature or project are we about to spec?"
- If the user types "skip" on a question, record "No constraint stated" for that dimension — do not press
- If a question elicits "I don't know", record the answer as is and move on — uncertainty is valid input
- `prefs.md` is advisory context, not a gate — spec phases proceed regardless of its content, including the Step 1.5 existence check
- Do not re-ask questions if the user has already answered them in a prior turn
