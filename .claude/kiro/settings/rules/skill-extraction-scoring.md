# Skill Extraction Scoring Rubric

## Overview
When extracting from a repository, score each candidate module against four criteria, then classify it into the most appropriate artifact type. The goal is reusable, non-obvious knowledge in the right form — not everything needs to be a skill.

## Scoring Criteria (0-3 each)

| Criterion | 0 (Skip) | 1 (Low) | 2 (Medium) | 3 (High) |
|-----------|----------|---------|------------|----------|
| **Recurrence** | One-off script or business logic | Used in 2-3 places within the repo | Common cross-project pattern | Universal pattern applicable anywhere |
| **Code Quality** | Undocumented, tangled, no tests | Partially documented, some structure | Well-structured with clear interfaces | Exemplary: documented, tested, idiomatic |
| **Domain Expertise** | Trivial or obvious implementation | Some useful insight | Non-obvious patterns requiring experience | Deep specialist knowledge hard to find elsewhere |
| **Generalizability** | Tightly coupled to this repo only | Reusable within the same domain | Applicable across related domains | Universal utility across any project |

## Threshold
- **Include in plan**: Score >= 6 out of 12
- **High priority**: Score >= 9 out of 12
- **Skip**: Score < 6

## Scoring Modifiers
Apply after base scoring:

| Modifier | Condition | Adjustment |
|----------|-----------|------------|
| Existing overlap | A similar skill already exists in `~/.claude/skills/` | -2 |
| Good documentation | Module has its own README, docstrings, or inline docs | +1 |
| Test coverage | Module has associated test files | +1 |
| Heavy coupling | Module requires 3+ repo-specific dependencies to function | -1 |

## What Makes a Good Skill Candidate

**Extract**:
- Workflow patterns (how to set up, configure, deploy something)
- Domain-specific decision trees (when to use X vs Y)
- Non-obvious integration patterns (gotchas, workarounds, best practices)
- Architecture patterns with concrete implementation guidance
- Error handling strategies specific to a domain or tool

**Skip**:
- Thin wrappers around well-documented APIs
- Business logic specific to the repo's domain model
- Configuration files without explanatory context
- Boilerplate that any scaffold generator produces
- Code that merely follows a framework's tutorial patterns

## Deduplication
Before scoring, cross-reference candidate names and descriptions against existing skills:
1. Glob `~/.claude/skills/*/SKILL.md`
2. Read frontmatter (name + description) of potential matches
3. If an existing skill covers >70% of the candidate's scope, apply the -2 overlap modifier
4. Note the existing skill name in the extraction plan for reference

---

## Artifact Type Classification

After scoring, classify each passing candidate into one of five types. The right type matters as much as the score.

| Type | Output path | When to choose |
|------|------------|----------------|
| **skill** | `~/.claude/skills/<name>/SKILL.md` | Encodes knowledge or workflow for Claude to apply when prompted. Checklists, decision trees, multi-step processes, domain expertise. |
| **hook** | `~/.claude/hooks/<name>.sh` + settings.json entry | Should fire automatically on a Claude Code event (`SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, `UserPromptSubmit`). Safety gates, context injection, post-action cleanup. |
| **script** | `~/.claude/scripts/<name>.sh` | Utility automation meant to be run directly — by a human or called from other scripts. No Claude-specific integration needed. |
| **command** | `~/.claude/commands/<name>.md` | User-invokable interactive workflow best surfaced as a `/slash-command`. Has a clear entry point and argument signature. |
| **routine** | `~/.claude/commands/<name>.md` + schedule note | Recurring/scheduled operation (nightly maintenance, weekly reports, monitoring). Generates a command file plus a recommended cron expression. |

### Classification Heuristics

Apply in order — first match wins:

1. **Fires automatically without user input?**
   - On a Claude Code lifecycle event → **hook**
   - On a schedule or timer → **routine**
   - On a shell trigger or file-system event → **script**

2. **Invoked explicitly by the user?**
   - Best expressed as `/slash-command` with arguments → **command**
   - Run directly from the shell → **script**

3. **Knowledge Claude applies when asked, not invoked as a command?** → **skill**

4. **Default**: If uncertain, prefer **skill**. A skill is the most general form and can always be refined later.

### Multiple Artifacts from One Candidate

A single repo module can produce more than one artifact. If a candidate encodes both a reusable workflow (skill) *and* a natural hook trigger, produce both and note the relationship in the plan.
