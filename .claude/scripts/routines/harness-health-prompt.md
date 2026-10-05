You are running the bi-weekly HARNESS HEALTH SWEEP. This invocation runs LOCALLY (not in Anthropic cloud), so you have full access to:

- The full file system, including all registered repos listed in `$SDD_HARNESS/projects.txt`
- `~/.claude/skills/` via the Skill tool
- The full repo file tree (you are already in the sdd-harness working directory)

Today's date: TODAY_PLACEHOLDER

Execute Phase 1 and Phase 2 in order. Each is error-isolated — if one phase fails, log the failure and continue.

---

## Phase 1 — CLAUDE.md Review

**Goal:** Audit all CLAUDE.md files across registered repos for stale instructions, over-constraining rules from pre-Claude-4.x habits, and model-assumption drift.

Steps:
1. Read `$SDD_HARNESS/projects.txt` (skip blank lines and lines starting with `#`)
2. For each repo path:
   a. If the path does not exist on disk or has no `.claude/` dir, mark as **inaccessible** and note it
   b. Read `CLAUDE.md` (root) and `.claude/CLAUDE.md` if present
   c. Audit for these failure modes:
      - Instructions that assume Claude < 4.x behavior (e.g., "always use Plan mode for X", overly prescriptive step sequences that modern Claude handles automatically)
      - Stale references to tools, commands, or paths that no longer exist
      - Over-constraining rules that limit Claude's judgment unnecessarily
      - Contradictions between root CLAUDE.md and project-level CLAUDE.md
3. Rate each repo: `clean` | `minor` (cosmetic, low-urgency) | `needs-update` (stale/incorrect instructions)

Write findings to `reports/claudemd-review-report.md`:

```
# CLAUDE.md Review Report — TODAY_PLACEHOLDER

## Summary
- Repos checked: N
- Inaccessible: N
- Clean: N
- Minor issues: N
- Needs update: N

## Findings

### [repo-name] — <clean|minor|needs-update>
[Finding or "No issues found"]

...
```

---

## Phase 2 — Iterative Skill Repair

**Goal:** Apply a Review→Repair→Validate loop to skills flagged as low-quality in the most recent curation report.

Steps:
1. Read `reports/skill-curation-report.md`. Find skills listed under **Low-Quality Candidates**.
2. For each flagged skill (max 3 per run, prioritize lowest score first):
   a. **Review** — read the skill file at `~/.claude/skills/<name>/SKILL.md`
   b. **Repair** — rewrite to improve against the four SkillOS dimensions:
      - Task relevance: clear trigger conditions
      - Operational validity: accurate, executable instructions
      - Content quality: no fluff, no stale references
      - Compression ratio: ≤30% of the context it replaces
   c. **Validate** — re-score the repaired version; only write if the total score improved by ≥ 2 points
   d. If score delta ≤ 1 after repair, mark as **stalled** (don't write)
3. Apply approved repairs with `.claude/scripts/routines/skill-write.sh <skill-name> <new-content-file>`
   — write the repaired body to a temp file first, then pass it to the script.
   Do NOT write SKILL.md directly with the Write/Edit tools: `update.sh`'s scheduled
   sync overwrites `~/.claude/skills/<name>/` from `skills/<name>/` (harness source)
   every 4h regardless of session activity, so a direct write to the installed copy
   alone is erased by the next tick. The script updates source + installed together
   and keeps a timestamped pre-repair backup under `.claude/memory/skill-repair-backups/`
   for rollback — do not skip it even for a "quick" fix.

Append to `reports/skill-curation-report.md`:

```
## Iterative Repair Run — TODAY_PLACEHOLDER

| Skill | Before | After | Status |
|-------|--------|-------|--------|
| skill-name | 5/12 | 9/12 | repaired |
| skill-name | 4/12 | 5/12 | stalled — delta too small |
```

If `reports/skill-curation-report.md` does not exist, note it and skip Phase 2.

---

## Phase 3 — Token Spend Attribution

**Goal:** Notice when harness overhead — routine cadence, agent fan-out width,
an unbounded tool — starts dominating token spend, before a usage limit does it
for you. Nothing else in the harness measures actual spend; every other
token-related skill only gives advice about reducing it.

Below is the output of `scripts/utils/token-forensics.py --days 14`, already run.
Do **not** re-run it.

```
FORENSICS_PLACEHOLDER
```

Steps:

1. **Check the parser first.** If the collapsed-duplicate count is zero across
   many transcripts, the transcript format changed and the dedup is a no-op —
   report exactly that and skip the rest of this phase. Do not quote inflated
   numbers.
2. Read `~/.claude/skill-library/auditing-token-spend/SKILL.md` and apply its Phase 2–3
   to read the four signals and name **one** cause, with the number that supports it.
3. Honour the `method` line on the automation split. When it says
   `proxy (sessions under 5min)`, subagent turns are not tagged in this
   transcript format — call it a proxy, and never restate an unpopulated field
   as a measured 0%.
4. Report only what is anomalous. Compare against the previous run's line in
   `reports/harness-health-report.md` if one exists. **A stable profile is a
   one-line "no change" and nothing more** — a phase that always finds a problem
   stops being read.
5. Append to `reports/harness-health-report.md`:

```
## Token Spend — TODAY_PLACEHOLDER
Total (dedup, 14d): N | peak 5h: N | automated: N% (method)
Top amplified tool: <name> (~N tokens)
Verdict: <no change | one named cause and the number behind it>
Action: <where it was routed, or "none needed">
```

Do not change any code or cadence in this phase. It reports; the human decides.
If the script is missing or exits non-zero, say so and skip — never invent figures.

---

## Output

Emit a single summary line:

```
Harness health sweep complete: N repos reviewed, N skills repaired, N stalled, token spend <ok|flagged>
```
