You are running the SELF-IMPROVING CODE REVIEW LEARNING sweep for this repo. This
invocation runs LOCALLY (not in Anthropic cloud), so you have full access to `gh`,
the repo working tree, and `.claude/memory/`.

Today's date: TODAY_PLACEHOLDER
PRs to analyze this run (already merged, already logged by pr-babysit, not yet
processed by this sweep): PROMOTABLE_PRS_PLACEHOLDER

For EACH PR number in that list, run Phases 1-2 independently, then write one combined
report in Phase 3.

---

## Phase 1 — Gather

1. Read the babysit-time review log: `.claude/memory/pr-reviews/pr-<n>.md` (written
   when pr-auto-create-hook.sh / gitnexus-pr-review / code-reviewer reviewed the PR
   before merge).
2. Fetch the real human review activity:
   - `gh api repos/{owner}/{repo}/pulls/<n>/comments`
   - `gh api repos/{owner}/{repo}/pulls/<n>/reviews`
   - `gh pr view <n> --json mergedAt,mergeCommit,title,body`
3. Diff what the logged review flagged/said against what a human reviewer actually
   said or changed:
   - **Missed**: a human raised something the logged review didn't mention.
   - **False positive**: the logged review flagged something a human dismissed,
     rejected, or fixed differently without comment.
   - **Convention gap**: a human correction reveals a team/project convention
     (naming, structure, a library preference) not previously known to the reviewer.

If a PR has no human review comments at all (e.g. self-merged, or reviewed only by
this same automation), record "no human signal" for that PR and move on — this is not
a finding, just an empty result.

---

## Phase 2 — Classify each finding

- **Low-risk** (an additive fact — a team convention, a recurring dismissed-flag
  pattern, an "always/never" preference): write it directly to a `.claude/memory/`
  file using the `project` or `feedback` memory type (whichever fits — see the memory
  type definitions in CLAUDE.md). Keep it self-contained with a **Why:** line citing
  the specific PR number as evidence. Add a one-line pointer to
  `.claude/memory/MEMORY.md`.
- **Higher-risk** (a change to the actual `code-reviewer` skill's methodology, a new
  review dimension, or a change to *how* reviews get triaged): do NOT write it
  anywhere — only record it in the report below for human approval. Never edit a
  skill file directly from this sweep.

### Finer tag (apply before the low-risk/higher-risk split above)

- **validated** — logged review's original take held up; human agreed or raised no
  contradiction. Not new information on its own.
- **corrected** — a human explicitly reversed or fixed something the logged review
  flagged or missed. Strong signal, usually low-risk.
- **refined** — logged review was directionally right but a human added nuance
  ("flag this, but only when X"). Usually low-risk; capture the nuance in the
  **Why:** line.
- **ambiguous** — can't tell from PR comments alone whether the human's action
  reflects a real preference or a one-off. Never guess into a memory write.

### Decision tree

1. **validated**, nothing new → `no_changes`.
2. **corrected** / **refined**, and it's only a fact/preference (no methodology
   change) → `update_review_pr_local` — write to `.claude/memory/` per the
   low-risk path above (name aside, this updates memory, not the skill).
3. Changes what gets reviewed or how (**higher-risk**) → `update_review_pr` —
   record in Pending Approval only; never edit the skill directly from this sweep.
4. One PR yields both a low-risk fact and a higher-risk question → `both` — do
   the memory write, and still record the higher-risk part in Pending Approval.
5. **ambiguous** → treat as higher-risk (`update_review_pr`, pending approval).

---

## Phase 3 — Write Report

Append a new dated section to `docs/code-review-learning-report.md` (create the file
with just a top-level `# Code Review Learning Report` heading if it doesn't exist yet
— this is a running log, not a snapshot, so APPEND, never replace prior sections):

```
## Sweep — TODAY_PLACEHOLDER

### PRs analyzed
- #<n>: <one-line outcome — no human signal / nothing new / N findings>

### Findings

#### #<n> — <missed | false-positive | convention-gap>
**What a human said/did:** ...
**What the logged review said:** ...
**Classification:** low-risk (written to memory as `<file>`) | higher-risk (needs approval)

## Pending Approval
[List every higher-risk finding from this sweep awaiting a human decision. If empty,
write "None this sweep."]
```

---

## Output

Emit a single summary line:
`Code-review learning sweep complete: N PRs analyzed, N memory facts added, N pending human approval`
