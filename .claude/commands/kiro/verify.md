---
description: Run verification pipeline (build, types, lint, test, audit, git status)
allowed-tools: Read, Task
argument-hint: [mode:quick|full|pre-commit|pre-pr]
---

# Verification Pipeline

## Parse Arguments
- Mode: `$1` (optional, default: `full`)

## Mode Resolution

**quick** — Build + Test only (fast feedback loop)
**full** — All static/unit stages, plus the conditional system stage (default)
**pre-commit** — Build + Types + Lint + Test (skip audit, git status, and system)
**pre-pr** — All stages + stricter thresholds, plus the conditional system stage

If `$1` is empty, default to `full`.

**System stage (7)** runs only in `full` and `pre-pr`, and only when the diff touches
≥2 components or crosses a process/entrypoint boundary. It starts the app, drives the
critical path, and asserts side effects — the only stage that proves the software runs
rather than inspecting artifacts. `quick` and `pre-commit` never run it, by design:
they exist for tight loops where seconds-to-minutes of startup is the wrong trade.

## Invoke Subagent

Delegate verification to verify-agent:

```
Task(
  subagent_type="verify-agent",
  description="Run verification pipeline",
  prompt="""
Mode: {$1 or 'full'}

Read .claude/steering/tech.md to discover:
- Build command (e.g., npm run build, cargo build, go build ./...)
- Type check command (e.g., npx tsc --noEmit, mypy .)
- Lint command (e.g., ruff check, npx eslint .)
- Test command (e.g., pytest, npm test, go test ./...)

If steering/tech.md is missing or doesn't specify commands, attempt auto-detection from package.json, Cargo.toml, pyproject.toml, Makefile, or go.mod.

Execute the pipeline for the specified mode and return a structured report.
"""
)
```

## Display Result

Show the structured PASS/FAIL report to the user.

### Next Steps Guidance

**If READY**:
- Code is verified and ready for next phase
- Consider `/kiro:validate-impl` for spec alignment check
- **For production deployment**: Run `/kiro:ship` for launch readiness check (staged rollout plan, decision thresholds, rollback procedure)

**If UNPROVEN**:
- Static checks and unit tests pass, but nothing proved the software runs — the system
  stage was triggered and could not find a run command
- Add the run/start command to `.claude/steering/tech.md`, then re-run `/kiro:verify full`
- Do not treat UNPROVEN as READY; it is the honest verdict for "clean, but unexercised"

**If NOT READY**:
- Review failed stages in the report
- For build failures: run `/kiro:fix-build` for automatic resolution
- For test failures: fix failing tests and re-run `/kiro:verify quick`
- For lint issues: most can be auto-fixed by the linter (e.g., `ruff check --fix`)
- For system-stage failures: the defect is at a boundary unit tests do not cross —
  fix, then re-run the whole stage from startup rather than resuming mid-sequence
