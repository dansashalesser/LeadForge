# Session: {{SESSION_NAME}}

**Saved**: {{YYYY-MM-DD HH:MM}}
**Feature/Spec**: {{active spec or feature name}}
**Branch**: {{current git branch}}

## Progress Tracker
- Feature: {{feature name or "exploratory"}}
- Spec: {{path to spec if exists, or "N/A"}}
- Git baseline: {{commit hash at session start — from earliest relevant commit}}
- Git head: {{commit hash at session end — from HEAD}}
- Tasks completed: {{list with spec task IDs, or bullet summary}}
- Tasks remaining: {{list with spec task IDs, or bullet summary}}
- Blocking issues: {{list or "None"}}
- Next action: {{single most important next step, specific enough to execute without re-reading context}}

## What WORKED (with evidence)
- {{specific thing}}: {{evidence — test output, build success, etc.}}

## What Did NOT Work (exact reasons)
- Attempted {{X}} because {{assumption}} — failed because {{exact error or reason}}

## What Has NOT Been Tried Yet
- {{Approach A}}: {{specific steps to try}}
- {{Approach B}}: {{specific steps to try}}

## Current State of Files
{{from git status/diff — list modified files with brief description of changes}}

## Exact Next Step
- Run: {{specific command or action}}
- Expected: {{what should happen}}
- Then: {{what to do after that}}
