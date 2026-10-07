---
description: Review implementation for performance issues (N+1 queries, unbounded ops, missing indexes)
allowed-tools: Read, Task
argument-hint: [feature-name] [file-paths]
---

# Performance Validation

## Parse Arguments
- Feature name: `$1` (optional)
- File paths: `$2` (optional, comma-separated)

## Scope Resolution

**If `$1` is provided (feature name)**:
- Read `specs/$1/design.md` for component list
- Read `specs/$1/tasks.md` for implementation files
- Scan those files for performance patterns

**If `$2` is provided (explicit file paths)**:
- Validate only the specified files

**If no arguments**:
- Use `git diff --name-only origin/main..HEAD` to find changed files
- Filter to source files only (exclude tests, docs, configs)

## Invoke Subagent

Delegate performance review to validate-perf-agent:

```
Task(
  subagent_type="validate-perf-agent",
  description="Review for performance issues",
  prompt="""
Feature: {$1 or 'auto-detect from git diff'}
Target files: {$2 or auto-detected}

Read .claude/steering/tech.md for framework and ORM context.
Review target files for performance anti-patterns.
"""
)
```

## Display Result

Show the performance review report.

### Next Steps Guidance

**If PASS**:
- No critical performance issues detected
- Implementation is ready for the next quality gate

**If issues found**:
- Review each issue with its severity and suggested fix
- Address Critical issues before proceeding
- Warning-level issues can be addressed later if not on a hot path
