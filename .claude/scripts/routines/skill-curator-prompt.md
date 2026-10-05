You are running the weekly SKILL-CURATOR SWEEP for the sdd-harness. This invocation runs LOCALLY (not in Anthropic cloud), so you have full access to:

- `~/.claude/skills/` via the Skill tool
- The full repo file tree (you are already in the repo's working directory)
- `.claude/memory/` and all gitignored files

Today's date: TODAY_PLACEHOLDER

Execute the three phases below in order. Each is error-isolated — if one phase fails, log and continue.

---

## Phase 1 — Skill Quality Audit

**Run this first, before reading any individual SKILL.md:**

```bash
python3 $SDD_HARNESS/scripts/skill-quality-scan.py --json
```

This mechanically scores every skill against the four SkillOS dimensions below —
do NOT re-derive these scores by reading ~1000 files one at a time. That is
enough tool calls to exhaust this session's turn/time budget before Phase 4
(writing the report) ever runs, which is why the report's body froze at
2026-08-06 for six weeks straight while later runs kept exiting 0 with real
duration but nothing written: the per-file read approach was eating the whole
budget on Phase 1 alone. The script is deterministic and reproducible — run it
again next week and get comparable numbers, closing the "discrepancy vs. last
week" methodology-drift gap prior reports had to caveat around.

The four dimensions it computes (0–3 each, 12 total):
- **Task relevance**: Is the trigger clear? (description present + explicit trigger cue)
- **Operational validity**: body substance as a proxy for executable instructions
- **Content quality**: body substance, stricter buckets (penalizes thin bodies)
- **Compression ratio**: body words vs description token cost

It also reports `yaml_defect_count` — skills whose `description:` is the
literal two-char string `">"` (quoted) instead of a bare `>` fold indicator,
which silently empties the description at parse time.

Low-quality candidates (`total <= 6`) come straight from its JSON output —
use them as-is. **Duplicate pairs are the one thing this script cannot do**
(semantic overlap needs judgment, not a heuristic): identify skills with
substantially overlapping scope by name/description clustering (e.g. a
product-family cluster like `fal-audio`/`fal-generate`/`fal-image-edit`), then
skim only those candidates' bodies to confirm overlap — a targeted read of a
handful of files, not all ~1000.

Compression heuristic: skill content should be ≤30% of the context it would replace manually.

---

## Phase 1.5 — Usage Evidence Audit

The `skill-usage-tracker.sh` PostToolUse hook logs every real skill invocation to
`logs/skill-usage.jsonl` (one `{"ts","skill"}` line per fire). This is the evidence
layer for deprecation — use it instead of guessing from file mtime.

1. If `logs/skill-usage.jsonl` does not exist or is empty, record "no usage data yet"
   and skip the rest of this phase (the tracker hook may be freshly installed).
2. Parse the log. For each skill compute: total fires, fires in the last 30 days,
   and last-seen date.
3. Cross-reference against the globbed skill list from Phase 1:
   - **Deprecate candidate**: zero invocations in the last 30 days.
   - **Archive candidate**: zero invocations in the last 90 days.
   - A skill with `pinned: true` in its frontmatter is protected — never flag it.
4. This phase is purely mechanical (no judgment) — it produces a candidate list only.
   Combine it with the Phase 1 quality score: a skill that is BOTH low-quality (≤6)
   AND cold (no 30d use) is the strongest deletion candidate.

Do NOT delete anything here — the weekly sweep only reports. Deletion happens through
the human-invoked `/skill-curator` skill.

---

## Phase 1.6 — Dependency Cross-Reference

The map below was generated deterministically (grep, word-boundary) BEFORE this
prompt ran, over other skills' SKILL.md bodies, hooks, agents, commands, CLAUDE.md,
kiro rules, and routine scripts. Treat it as ground truth — do not re-derive it or
second-guess it with your own search.

```
DEPENDENCY_MAP_PLACEHOLDER
```

A skill listed here has live referrers: other skills, hooks, or agents depend on it
by name. Cross-reference this map against the Phase 1 low-quality list and the Phase
1.5 cold/archive candidates:

- A skill that is BOTH (low-quality or cold) AND in this map is a **dependency flag**
  — it must be reported separately, never folded silently into a plain deletion
  candidate. The human-invoked `/skill-curator` skill must see this flag and address
  the referrers explicitly (update them or fold their logic in) before deleting or
  merging — never a bare delete.

---

## Phase 2 — Description Budget Audit

For each skill found in Phase 1, check the `description:` frontmatter value:
- Compute character count; estimate token cost: `ceil(chars / 4)`
- Flag: > 150 chars ⚠️ consider compression; > 200 chars 🔴 measurable system-reminder pressure

Grammar compression heuristics (apply when proposing shorter text):
- Drop articles where meaning is unambiguous
- Use imperative verb form, cut filler phrases ("Use when you need to" → implied)
- Replace "or"-separated noun lists with comma lists
- Preserve trigger nouns — these are load-bearing for skill routing

Build the budget table but do NOT apply changes — leave that for human-invoked `/skill-curator`.

---

## Phase 3 — Memory Governance Health Audit

Check the five compaction-discipline hook failure modes:

1. `.claude/hooks/stop-hook.sh` — verify it still writes `observations.md` entries; check that the memory-conventions rule reference is intact
2. `.claude/hooks/session-start-hook.sh` — verify catch-up trigger logic is present and reads `.last-routine-run`
3. `kiro/settings/rules/memory-conventions.md` — verify the file exists and has not been emptied or truncated
4. `.claude/memory/observations.md` — check recency: last entry should be within 7 days (flag if stale)
5. `.claude/memory/hot-memory.md` — check recency: flag if last-modified > 14 days

For each failure mode, record: `ok`, `warn` (degraded but recoverable), or `critical` (broken).

---

## Phase 4 — Write Report

Write `reports/skill-curation-report.md` with this structure:

```
# Skill Curation Report — TODAY_PLACEHOLDER

## Summary
- Skills audited: N
- Low-quality flags: N
- Duplicate pairs: N
- Description flags (>150 chars): N
- Cold skills (no use in 30d): N | Archive candidates (90d): N
- Dependency flags: N
- Memory governance: <ok|warn|critical>

## Usage Evidence
[From logs/skill-usage.jsonl. If no data yet, write "No usage data yet — tracker hook freshly installed."]

### Deprecate Candidates (no invocation in 30d)
[skill name] — last seen DATE (or "never") — quality N/12

### Archive Candidates (no invocation in 90d)
[skill name] — last seen DATE (or "never")

## Dependency Flags

MANDATORY — this heading and its content must appear in the written report exactly
as produced by this phase, even when empty. Do not omit this section.

[Skills that are BOTH (low-quality or cold/archive candidate) AND cross-referenced
per Phase 1.6's map. If none, write "No dependency flags — no cross-referenced
skill is currently a deletion/archive candidate."]

⚠️ [skill name] — referenced by: [referrer list from the map] — do not delete/merge
without updating these referrers first.

## Description Budget

Total: N skills | X chars | ~Y tokens

| Skill | Chars | Status |
|-------|-------|--------|
| ...   | ...   | ...    |

## Quality Findings

### Low-Quality Candidates
[skill name] — score N/12 — [reason]

### Duplicate Pairs
[skill A] ↔ [skill B] — [overlapping scope summary]

## Memory Governance Health

| Check | Status | Notes |
|-------|--------|-------|
| stop-hook writes observations | ok | |
| session-start catch-up logic | ok | |
| memory-conventions.md intact | ok | |
| observations.md recency | ok | last entry: DATE |
| hot-memory.md recency | ok | last modified: DATE |
```

If `reports/skill-curation-report.md` already exists, read it first. Other routines
(the iterative repair loop, the interactive `/skill-curator` skill) append their own
dated sections after this phase's structure — `## Iterative Repair Run — DATE`,
`## Local Curation — DATE`, `## Skill-Eval-Gate Override — DATE`, or any other
`## ... — DATE` heading not listed in the structure above. Find the FIRST such
heading in the existing file and keep everything from that line onward verbatim —
it is history, not this phase's output. REPLACE everything before that boundary
(or the whole file, if no such heading exists) with the fresh structure below.
Never delete another routine's appended section to make the file shorter.

---

## Output

Emit a single summary line:

```
Skill curator sweep complete: N skills audited, N flags, governance=<ok|warn|critical>
```
