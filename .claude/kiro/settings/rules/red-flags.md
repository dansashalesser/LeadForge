# Red Flags Framework

## Purpose

Observable indicators that a process or skill is being violated. These are early warning patterns — detecting them before they compound prevents costly rework.

## How to Use

Agents should watch for these signals during execution. When a red flag is observed, pause and correct course before continuing. Red flags are grouped by the workflow phase where they're most likely to appear.

## Workflow Red Flags

### Requirements Phase
- **No acceptance criteria**: Requirements exist but lack Verify: statements
- **Solution masquerading as requirement**: "Use Redis for caching" instead of "Response time under 200ms"
- **Missing stakeholder perspective**: Only happy-path scenarios covered, no error or edge cases
- **Vague quantifiers**: "Fast", "scalable", "user-friendly" without measurable thresholds

### Design Phase
- **Implementation leaking into design**: Code snippets, function bodies, or algorithm details in design.md
- **Missing error paths**: Only success flows diagrammed, no failure/degradation scenarios
- **Orphan components**: Components defined but not connected to any system flow
- **Assumption-heavy design**: Multiple "we assume..." statements without validation

### Task Breakdown Phase
- **Compound task titles**: Task name contains "and" (e.g., "Build API and write tests") — split it
- **No requirements mapping**: Tasks exist without `_Requirements: X.X_` traceability
- **All tasks sequential**: No `(P)` markers when tasks clearly could parallelize
- **Giant tasks**: Sub-task estimated at more than 3 hours — needs further decomposition

### Implementation Phase
- **Large uncommitted changes**: More than ~300 lines changed without a commit or checkpoint
- **Tests written after code**: Test files created/modified after implementation files (TDD violation)
- **Console.log / print debugging left in**: Debug statements in committed code
- **Hardcoded values**: Magic numbers, hardcoded URLs, embedded credentials
- **No test backlinks**: Tests without `# Verifies: specs/{feature}/requirements.md#N.M` comments

### Code Review Phase
- **Rubber-stamp review**: "Looks good" with no specific observations
- **Style-only feedback**: All comments about formatting, none about logic or architecture
- **Skipped security check**: No mention of injection, auth, or data validation in review

### General (Any Phase)
- **Context rot**: Agent repeating itself, contradicting earlier statements, or losing track of decisions
- **Scope creep**: Work expanding beyond what the current task specifies
- **Silent failures**: Operations that can fail but return no error indication
- **Skipped phase**: Moving to implementation without approved design, or design without approved requirements

## Severity Levels

- **STOP**: Must fix before continuing (e.g., TDD violation, missing requirements approval)
- **WARN**: Should fix soon, but current task can complete (e.g., missing test backlinks)
- **NOTE**: Track for later improvement (e.g., minor scope creep, missing `(P)` markers)
