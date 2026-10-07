# Memory Conventions

Rules governing `.claude/memory/` — the persistent cross-session memory system.

## File Formats

### Observations (append-only)
```
- YYYY-MM-DD [tag1, tag2]: observation text
```

**Tags** (SDD-scoped):
- `spec` — requirements, design, or task-related learnings
- `impl` — implementation surprises, gotchas, workarounds
- `design` — architectural decisions or trade-offs
- `debug` — debugging insights, root causes found
- `decision` — choices made and their rationale
- `friction` — workflow pain points, tooling issues
- `insight` — cross-cutting realizations
- `pattern` — recurring theme worth distilling

**Rules**: Observations are append-only; new entries go at the bottom. Max 5 new observations per `/kiro:reflect` pass.

### Action Items
```
- [ ] task description | due:YYYY-MM-DD | pri:high/medium/low | added:YYYY-MM-DD
- [x] completed task | done:YYYY-MM-DD | added:YYYY-MM-DD
```

**Sections**: Active (open items), Completed (done items, archived by housekeeping).

### Learnings (append-only JSONL)
```json
{"date": "YYYY-MM-DD", "situation": "[tag] excerpt of the triggering observation", "insight": "excerpt", "applies_when": "condition describing when this learning is relevant"}
```
One JSON object per line. Populated by `reflect-agent` Step 6 (curated) or, as a deterministic fallback when reflect-agent didn't run that day, by `stop-hook.sh`'s learnings promoter (ranks that day's `observations.md` entries by tag priority and appends the top one). At most one entry per calendar day from either source — the hook checks for an existing `"date": "YYYY-MM-DD"` line first and skips if reflect-agent already wrote one.

### Entities (compact registry)
```
### Entity Name
Key facts on one line. Role, purpose, or relationship.
status: active | last: YYYY-MM-DD
```

**Rules**: 3-line max per entry. Use strikethrough for deprecated/removed entities.

### Hot Memory (<50 lines)
Freely rewritten each session. Contains:
- Current priorities and active specs
- Key recent decisions
- System notes (blockers, context)

**Rules**: Must stay under 50 lines. Prune aggressively. This is a summary, not a log.

### Patterns (<70 lines)
Distilled rules from repeated observations. Edited in-place.
- Core patterns: ≤70 lines in `meta/patterns.md`
- Promote when 3+ observations cluster on the same theme

## Structural Rules

### Single Source of Truth (SSOT)
Each fact lives in ONE canonical file. Other files reference via file paths; all cross-references use paths instead of duplicating content.

### Edit Discipline
| File | Edit Mode |
|------|-----------|
| `observations.md` | Append-only |
| `learnings.jsonl` | Append-only, ≤1 entry/day |
| `action-items.md` | Add/complete/archive |
| `entities.md` | Add/update/strikethrough |
| `hot-memory.md` | Freely rewritten |
| `meta/patterns.md` | Edited in-place |
| `meta/self-observations.md` | Append-only |
| `daily/YYYY-MM-DD-brief.md` | Written once by daily-maintenance Step D; never edited after creation |

### L0 Headers
Every memory file starts with an L0 summary comment for fast scanning:
```
<!-- L0: summary of what this file contains (max 80 chars) -->
```

### Progressive Loading Protocol
1. **L0**: Read one-line `<!-- L0: ... -->` headers to assess relevance
2. **L1**: Scan section headers (`##`) for navigation
3. **L2**: Read full content only when needed

At session start, always read L2 of `hot-memory.md` and `meta/patterns.md` (they are small by design).

## Caps and Thresholds

| File | Cap | Action when exceeded |
|------|-----|---------------------|
| `hot-memory.md` | 50 lines | Prune during reflect or housekeeping |
| `meta/patterns.md` | 70 lines | Condense during housekeeping |
| `observations.md` | 50 entries | Archive oldest to `glacier/` during housekeeping |
| `action-items.md` completed | 10 items | Archive to `glacier/` during housekeeping |
| `learnings.jsonl` | 100 lines | Archive oldest to `glacier/` during housekeeping |

### Daily Brief Format (`daily/YYYY-MM-DD-brief.md`)
Structured morning context assembled from `action-items.md`, `hot-memory.md`, and `entities.md`.
```markdown
<!-- L0: Morning brief for YYYY-MM-DD — open loops, attention items, blockers -->

## Morning Brief — YYYY-MM-DD

### To-do (open loops on me)
- [item] — [why it matters or what's blocked]

### Needs attention
- [item] — [what changed]

### Blocked on me
- [project or decision] — [what unblocks it]
```
**Rules**: Draft only — never triggers external actions. Max 150 words. Written by daily-maintenance Step D; housekeeping archives briefs >30 days old to `glacier/`.

## Glacier Archival Format

Archived files in `.claude/memory/glacier/` use YAML frontmatter:
```yaml
---
archived: YYYY-MM-DD
source: observations.md
entries: 25
date_range: YYYY-MM-DD to YYYY-MM-DD
tags: [most frequent tags]
---
```
