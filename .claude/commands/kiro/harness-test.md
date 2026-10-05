---
description: Smoke-test harness prompts using Haiku to expose vague instructions and implicit assumptions
allowed-tools: Read, Task, Glob, Bash
---

# Kiro Harness Test — Haiku Smoke Testing

## Purpose

Run key harness workflows at Haiku tier (cheapest model) to stress-test prompt quality. If prompts produce correct results with Haiku, they are well-structured. Failures at Haiku tier expose:
- Vague or ambiguous instructions
- Implicit assumptions not captured in prompts
- Over-reliance on model intelligence instead of clear structure

## Usage

```
/kiro:harness-test [agent-name]
/kiro:harness-test regression [agent-name]
```

- No arguments: runs the standard smoke-test suite
- `agent-name`: test a specific agent only
- `regression`: runs scenario-based regression tests against expected outcomes
- `regression agent-name`: regression tests for a specific agent only

## Standard Smoke-Test Suite

### Test 1: Steering Generation

Invoke the steering agent at Haiku tier on the current project:

```
Task(
  subagent_type="steering-agent",
  model="haiku",
  description="Smoke-test: steering generation",
  prompt="""
Generate steering documentation for this project.
Read the codebase structure and produce product.md, tech.md, and structure.md.
Follow .claude/kiro/settings/rules/steering-principles.md exactly.

File patterns:
- .claude/kiro/settings/rules/steering-principles.md
- .claude/kiro/settings/templates/steering/*.md
"""
)
```

**Pass criteria**: Output includes all 3 steering file types with relevant content. Reject if output is generic boilerplate unrelated to the actual project.

### Test 2: Harness Validation

Invoke harness-validate at Haiku tier:

```
Task(
  subagent_type="harness-validate-agent",
  model="haiku",
  description="Smoke-test: harness validation",
  prompt="""
Check the SDD harness installation for structural integrity.
Validate command → agent references, template existence, memory caps, and L0 headers.
"""
)
```

**Pass criteria**: Produces a structured report with component counts and issue list. Reject if format is wrong or categories are missed.

### Test 3: Design Review (if spec exists)

Find any existing spec:
```bash
ls specs/*/design.md 2>/dev/null | head -1
```

If a spec exists, invoke validate-design at Haiku tier:

```
Task(
  subagent_type="validate-design-agent",
  model="haiku",
  description="Smoke-test: design review",
  prompt="""
Feature: {detected-feature}
Spec directory: specs/{detected-feature}/
File patterns: specs/{detected-feature}/*.md, .claude/steering/*.md, .claude/kiro/settings/rules/design-review.md
"""
)
```

**Pass criteria**: Produces structured output with examination summary, observations, and GO/NO-GO decision.

## Reporting

```
## Harness Smoke-Test Results

| Test | Agent | Result | Notes |
|------|-------|--------|-------|
| Steering | steering | PASS/FAIL | [brief note] |
| Validation | harness-validate | PASS/FAIL | [brief note] |
| Design Review | validate-design | PASS/FAIL or SKIPPED | [brief note] |

### Failures
[For each failure: what went wrong, which prompt needs improvement]

### Recommendations
[Specific prompt improvements based on failures]
```

## Regression Test Mode

When invoked with `regression`, runs scenario-based tests to verify prompt changes don't degrade agent quality.

### Step 1: Load Scenarios

Read `.claude/memory/meta/prompt-scenarios.md`. If the file doesn't exist or has no scenarios, tell the user to add scenarios first (scenarios are populated from real usage and prompt diagnoses).

If `agent-name` is specified, filter to scenarios for that agent only.

### Step 2: Run Each Scenario

For each scenario:

1. Invoke the target agent at **Haiku tier** with the scenario's input context
2. Compare the output against:
   - **Expected outcome**: Does the conclusion match? (GO/NO-GO, PASS/FAIL, key content)
   - **Expected finding**: Does the specific required finding appear?
   - **Output format**: Is the structure correct? (structural reliability)
3. Score alignment using the rubric from `.claude/kiro/settings/rules/alignment-scoring.md`
4. Compare against the scenario's alignment floor

### Step 3: Report

```
## Regression Test Results

| Agent | Scenario | Alignment | Floor | Structural | Result |
|-------|----------|-----------|-------|------------|--------|
| validate-adversarial | obvious-flaw | 4 | 3 | ok | PASS |
| validate-adversarial | solid-design | 2 | 4 | ok | FAIL |
| steering | python-fastapi | 5 | 4 | ok | PASS |

### Regressions
[For each FAIL: which scenario, what was expected vs actual, suggested investigation]

### Summary
- Scenarios run: N
- Passed: M
- Failed: K
- Verdict: {ALL CLEAR | REGRESSIONS DETECTED}
```

### Step 4: Trace Entry

For each scenario run, append a trace entry:
```
YYYY-MM-DD HH:MM | {agent-name} | haiku | {pass|fail} | fast | alignment:{N} | structural:{ok|malformed}
```

## Notes

- Haiku failures are a QA signal about harness quality, not Haiku quality
- Run this after making changes to agent prompts or rules
- If an agent consistently fails at Haiku, its prompt needs clarification — add structure, examples, or constraints
- Results are informational only — they don't block any workflow
- Run `regression` mode after approving instruction library changes from `/kiro:evolve`
- Scenarios grow organically from real usage — add them after successful invocations or diagnosed failures
