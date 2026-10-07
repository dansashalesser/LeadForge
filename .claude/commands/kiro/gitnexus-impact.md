---
description: Query GitNexus for blast radius of current changes
allowed-tools: Read, Bash, Grep, Glob
argument-hint: [--from COMMIT] [--to COMMIT]
---

# GitNexus Impact Analysis

Query the GitNexus knowledge graph to map current code changes to affected execution flows, processes, and downstream dependencies.

## Prerequisites Check

1. Check if GitNexus is available:
   ```bash
   command -v gitnexus >/dev/null 2>&1 || npx gitnexus --version >/dev/null 2>&1
   ```
   If not: "GitNexus is not installed. Run `/kiro:gitnexus-setup` first." Then stop.

2. Check if the repo is indexed:
   ```bash
   test -d .gitnexus/
   ```
   If not: "This repo isn't indexed. Run `/kiro:gitnexus-setup` first." Then stop.

## Parse Arguments

- `--from COMMIT`: Base commit for diff (default: HEAD)
- `--to COMMIT`: Target commit (default: working tree)
- No arguments: analyze uncommitted changes (`git diff HEAD`)

## Step 1: Capture the Diff

```bash
git diff --name-only [FROM..TO or HEAD]
```

If no files changed, report "No changes detected." and stop.

Show the user which files are changed.

## Step 2: Query GitNexus MCP

For each changed file, use the GitNexus MCP `detect_changes` tool to map changes to affected processes.

If the MCP server is not running or not configured, fall back to a manual approach:
1. Run `git diff --unified=0 HEAD` to get changed line ranges
2. Use Grep to find functions/classes in those ranges
3. Use Grep to find callers of those functions
4. Report the manual dependency trace

## Step 3: Classify Risk

For each affected process, classify risk:

| Risk | Criteria |
|---|---|
| **HIGH** | Depth 1 — direct dependents that will break |
| **MEDIUM** | Depth 2 — likely affected, may need testing |
| **LOW** | Depth 3+ — indirect, monitor but unlikely to break |

## Step 4: Report

```
Impact Analysis
═══════════════

Changes: N files modified

HIGH RISK (will break):
  - ProcessName → affected via FunctionName (depth 1)
  - ProcessName → affected via ClassName.method (depth 1)

MEDIUM RISK (likely affected):
  - ProcessName → indirect via CallChain (depth 2)

LOW RISK (monitor):
  - ProcessName → transitive dependency (depth 3)

═══════════════
Summary: X high, Y medium, Z low risk impacts
Changed files: [list]

Suggested actions:
  - Run tests covering HIGH risk processes
  - Review MEDIUM risk call chains
  - /kiro:verify full — run full verification pipeline
  - /kiro:gitnexus-explore — visualize the affected graph
```

## Fallback Behavior

If GitNexus MCP is unavailable, the command still works using grep-based dependency tracing:
1. Extract changed function/class names from the diff
2. Grep for callers across the codebase
3. Report a simplified dependency trace (without process grouping or confidence scores)

Report that this is a "grep-based approximation" and suggest `/kiro:gitnexus-setup` for full graph intelligence.
