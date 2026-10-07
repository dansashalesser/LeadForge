---
description: Analyze a repository and identify extractable skills, hooks, scripts, commands, and routines
allowed-tools: Read, Task, Glob, Bash
argument-hint: <repo-url-or-local-path>
---

# Skill Extraction — Scan

Analyze a repository to identify modules containing reusable knowledge or automation, score them against the extraction rubric, classify each into the right artifact type (skill, hook, script, command, or routine), and produce a reviewable extraction plan.

## Input Parsing

Parse `$ARGUMENTS` to determine the source:

1. **GitHub URL** (contains `github.com` or matches `owner/repo`): Will be cloned by the agent
2. **Git URL** (ends with `.git` or starts with `git@`): Will be cloned by the agent
3. **Local path** (starts with `/` or `~` or `.`): Validate the directory exists using Bash `test -d`

If no argument provided, show usage:
```
Usage: /kiro:skill-extract-scan <repo-url-or-local-path>

Examples:
  /kiro:skill-extract-scan https://github.com/org/repo
  /kiro:skill-extract-scan org/repo
  /kiro:skill-extract-scan /path/to/local/repo
```

## Prepare Working Directory

Determine the repo name from the URL or path (last path segment, without `.git`).

Create the working directory:
```
.claude/skill-extraction/<repo-name>/
```

## Invoke Subagent

Delegate to the skill-extract-agent in **scan mode**:

```
Task(
  subagent_type="skill-extract-agent",
  description="Scan repository for extractable skills",
  prompt="""
Mode: scan

Source: <the repo URL or local path from $ARGUMENTS>
Working directory: .claude/skill-extraction/<repo-name>/
Plan output path: .claude/skill-extraction/<repo-name>/plan.md

File patterns to read:
- .claude/kiro/settings/rules/skill-extraction-scoring.md
- .claude/kiro/settings/templates/skill-extraction-plan.md

Cross-reference existing skills at: ~/.claude/skills/*/SKILL.md
"""
)
```

## Display Result

Show the agent's summary to the user, then add:

```
Plan saved to: .claude/skill-extraction/<repo-name>/plan.md

Next steps:
1. Review the plan — check artifact types, edit or remove candidates you don't want
2. Run: /kiro:skill-extract .claude/skill-extraction/<repo-name>/plan.md
   Or for the fast path: /kiro:skill-extract <repo> -y
```
