---
description: Generate skills, hooks, scripts, commands, or routines from a repository or extraction plan
allowed-tools: Read, Task, Glob, Bash, Write
argument-hint: <repo-url-or-path-or-plan> [-y]
---

# Skill Extraction — Generate

Generate artifacts (skills, hooks, scripts, commands, or routines) from an approved extraction plan, or run the full pipeline (scan + generate) in one step.

## Input Parsing

Parse `$ARGUMENTS` for:

1. **Plan file path** (ends with `.md` or `plan.md`): Read and validate it contains a skill extraction plan (has "Skill Extraction Plan" header and candidate table)
2. **Repository URL or path** (same rules as skill-extract-scan): Requires `-y` flag or an existing plan
3. **`-y` flag**: Auto-approve — run full pipeline without review gate

Determine the mode:

| Input | Flag | Mode |
|-------|------|------|
| Plan file | — | Generate from plan |
| Repo URL/path | `-y` | Full pipeline (scan → auto-approve → generate) |
| Repo URL/path | none | Check for existing plan at `.claude/skill-extraction/<repo-name>/plan.md` |

If repo provided without `-y` and no existing plan found:
```
No extraction plan found. Run one of:
  /kiro:skill-extract-scan <repo>     ← scan first, review, then extract
  /kiro:skill-extract <repo> -y       ← full pipeline, no review gate
```

If no argument provided, show usage:
```
Usage: /kiro:skill-extract <plan-file>
       /kiro:skill-extract <repo-url-or-path> -y

Examples:
  /kiro:skill-extract .claude/skill-extraction/my-repo/plan.md
  /kiro:skill-extract https://github.com/org/repo -y
  /kiro:skill-extract /path/to/local/repo -y
```

## Full Pipeline Mode (`-y`)

If running the full pipeline, first invoke the scan:

```
Task(
  subagent_type="skill-extract-agent",
  description="Scan repository for extractable skills",
  prompt="""
Mode: scan

Source: <repo URL or path>
Working directory: .claude/skill-extraction/<repo-name>/
Plan output path: .claude/skill-extraction/<repo-name>/plan.md

File patterns to read:
- .claude/kiro/settings/rules/skill-extraction-scoring.md
- .claude/kiro/settings/templates/skill-extraction-plan.md

Cross-reference existing skills at: ~/.claude/skills/*/SKILL.md
"""
)
```

Then proceed to generation with the produced plan.

## Generate Mode

Invoke the skill-extract-agent in **generate mode**:

```
Task(
  subagent_type="skill-extract-agent",
  description="Generate SKILL.md files from extraction plan",
  prompt="""
Mode: generate

Plan file: <path to the extraction plan>
Source repository: <original repo URL or path from the plan>

Skills output directory: ~/.claude/skills/

Read the plan, then for each candidate marked for extraction,
generate a SKILL.md following the standard format.
"""
)
```

## Display Result

Show the agent's summary to the user:

```
Extraction Complete

Artifacts created:
1. [skill]   ~/.claude/skills/<name>/SKILL.md — <description>
2. [hook]    ~/.claude/hooks/<name>.sh — <description>  (register in settings.json — see file)
3. [script]  ~/.claude/scripts/<name>.sh — <description>
4. [command] ~/.claude/commands/<name>.md — <description>
5. [routine] ~/.claude/commands/<name>.md — <description> (schedule: <cron>)

To verify:
- Read any generated artifact to check quality
- For skills: test activation by mentioning trigger keywords in a new conversation
- For hooks: check the REGISTRATION comment at the bottom of the .sh file
- For routines: use /schedule to register the suggested cron
```

If any candidates were skipped (score too low or marked skip in plan), note them:
```
Skipped (below threshold or marked skip):
- <name> (score: N/12) — <reason>
```
