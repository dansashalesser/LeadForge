---
description: Fetch a Jira issue and drive the appropriate SDD solve workflow
allowed-tools: Bash, Read, Write, TodoWrite, SlashCommand, Glob, Grep, Agent
argument-hint: <ISSUE-ID> [--dry-run]
---

# Jira Solve

<background_information>
- **Mission**: Given a Jira issue ID, fetch all context from Jira, understand the problem, then drive the right solve workflow (spec, debug, or implementation plan) using the repo and SDD harness.
- **Jira client**: `.claude/scripts/jira_client.py` — run via `uv run python .claude/scripts/jira_client.py <cmd> <args>`
- **Credentials**: `~/.env.jira` must exist with JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN
- **Routing logic**:
  - **Pre-gate (all types):** first apply `Skill("issue-triage-routing")` to the issue — if it
    routes to DEFER (off-roadmap) or CLARIFY (ambiguity blocks a spec), honor that before the
    type-based routing below. Only proceed to type-routing once triage yields SPEC or ONE-SHOT.
  - Bug / Defect → systematic debugging workflow
  - Story / Feature / Epic → `/kiro:spec-quick` seeded from Jira context
  - Task / Sub-task / Improvement / Chore → direct implementation plan
- **--dry-run**: fetch and display the issue, post no comments, trigger no workflows
</background_information>

<instructions>

## Step 1: Parse Arguments

Parse `$ARGUMENTS`:
- Extract `ISSUE-ID` (required): any token matching pattern like `PROJ-123`, `ZORA-456`, etc.
- Check for `--dry-run` flag
- If no issue ID found, output error and exit:
  ```
  Usage: /kiro:jira-solve <ISSUE-ID> [--dry-run]
  Example: /kiro:jira-solve ZORA-123
  ```

Initialize TodoWrite:
```json
[
  {"content": "Fetch Jira issue data", "status": "in_progress"},
  {"content": "Analyze issue and search codebase", "status": "pending"},
  {"content": "Post analysis comment to Jira", "status": "pending"},
  {"content": "Execute solve workflow", "status": "pending"},
  {"content": "Post completion comment to Jira", "status": "pending"}
]
```

## Step 2: Fetch Jira Issue

Run:
```bash
uv run python .claude/scripts/jira_client.py fetch <ISSUE-ID>
```

Parse the JSON output. If the command exits non-zero, display the error and stop.

Display issue summary to user:
```
## Jira Issue: <KEY>
**Type**: <issue_type>  |  **Status**: <status>  |  **Priority**: <priority>
**Summary**: <summary>
**Assignee**: <assignee>  |  **Reporter**: <reporter>

### Description
<description (first 500 chars)>

### Acceptance Criteria
<acceptance_criteria or "(none found)">

### Linked Issues
<linked_issues summary or "(none)">
```

Mark TodoWrite task 1 complete. If `--dry-run`, stop here and display: `[Dry run] Issue fetched successfully. No workflows will be triggered.`

## Step 3: Analyze Issue and Search Codebase

Delegate to the `jira-solve-agent` subagent with the full issue JSON and instructions to:
1. Convert the issue into a structured problem statement
2. Extract keywords from title/description/labels/components
3. Search the repo for relevant files (Glob + Grep on meaningful terms)
4. Identify the issue category (bug/feature/task) and map it to a workflow
5. For features: build the `spec_description` string suitable for `spec-quick`
6. Return a structured analysis report

Mark TodoWrite task 2 complete.

## Step 4: Post Initial Comment to Jira

Unless `--dry-run`, post a comment to inform the team:

```bash
uv run python .claude/scripts/jira_client.py comment <ISSUE-ID> "h3. Claude AI Analysis Started

Claude Code is analyzing this issue and will attempt to solve it.

*Issue type detected*: <issue_type>
*Workflow*: <Bug: Systematic Debug | Feature: SDD Spec Workflow | Task: Direct Implementation>
*Relevant files identified*: <comma-separated list of top files>

_This comment was posted automatically by the SDD harness._"
```

Mark TodoWrite task 3 complete.

## Step 5: Execute Solve Workflow

Branch on `issue_type` (case-insensitive):

---

### Bug / Defect

Display:
```
Detected: BUG — Starting systematic debugging workflow
```

Load the issue context as background for the debugging session. Present the user with:
- Problem statement from Jira
- Relevant files found in codebase
- Known steps to reproduce (from description)

Then instruct Claude to begin systematic root cause analysis:
1. Read relevant files identified in Step 3
2. Search for related error patterns, recent changes (git log on affected files)
3. Hypothesize root causes, verify by reading code
4. Implement fix
5. Run tests: `uv run pytest -x --ignore=tests/integration`

---

### Story / Feature / Epic

Display:
```
Detected: FEATURE — Starting SDD spec workflow (spec-quick)
```

Build the spec description from the analysis:
- Lead with the issue summary
- Include acceptance criteria
- Note the issue key for traceability

Execute:
```
/kiro:spec-quick "<spec_description_from_analysis> [<ISSUE-ID>]"
```

This will run all 4 phases (init → requirements → design → tasks) in interactive mode, allowing the user to review each phase.

---

### Task / Sub-task / Improvement / Chore

Display:
```
Detected: TASK — Creating implementation plan
```

Using the issue context and relevant files found:
1. Read relevant source files
2. Build a concrete implementation plan (what files to change, what functions to add/modify)
3. Present plan to user and ask for approval before executing
4. On approval, implement atomically (one commit per logical unit)
5. Run tests after each change

---

Mark TodoWrite task 4 complete.

## Step 6: Post Completion Comment

After the workflow completes (or user finishes review), post a completion comment:

```bash
uv run python .claude/scripts/jira_client.py comment <ISSUE-ID> "h3. Claude AI Analysis Complete

*Workflow executed*: <workflow name>
*Outcome*: <brief summary — e.g. 'Fix implemented in engine/agents/cfo/analyst.py', 'Spec created at specs/feature-name/', 'Implementation plan created'>
*Files changed*: <list or 'none yet'>

_This comment was posted automatically by the SDD harness._"
```

Mark TodoWrite task 5 complete.

Display final summary to user.

## Error Handling

- If `~/.env.jira` is missing: display setup instructions, exit
- If issue not found (HTTP 404): display clear error, exit
- If network error: display error, suggest `--dry-run` to debug
- If a workflow step fails: post a failure comment to Jira, display error

## Constraints

- Never commit any files — follow atomic commit rules separately
- Never modify `~/.env.jira`
- The `--dry-run` flag means zero writes to Jira and zero workflow execution
- Respect SDD rule: every feature needs an approved spec before implementation

</instructions>
