---
description: Generate CI configuration that mirrors the verify pipeline
allowed-tools: Read, Task
argument-hint: [platform:github|gitlab|azure]
---

# CI Scaffold — Extend Verification Beyond Sessions

## Parse Arguments
- Platform: `$1` (optional, auto-detected if not specified)

## Platform Resolution

If `$1` is provided, use it directly. Otherwise, auto-detect:
- `.github/workflows/` exists → `github`
- `.gitlab-ci.yml` exists → `gitlab`
- `azure-pipelines.yml` exists → `azure`
- None found → ask user which platform to target

## Invoke Subagent

Delegate to ci-scaffold-agent:

```
Task(
  subagent_type="ci-scaffold-agent",
  description="Generate CI configuration",
  prompt="""
Platform: {$1 or 'auto-detect'}

Generate a CI configuration file that mirrors the /kiro:verify pipeline stages.

Read .claude/steering/tech.md to discover build, type-check, lint, and test commands.
If not found, auto-detect from package.json, pyproject.toml, Cargo.toml, go.mod, or Makefile.

The CI pipeline should enforce:
1. Build verification
2. Type checking
3. Lint with --max-warnings=0 (or equivalent zero-tolerance flag)
4. Test suite execution
5. Debug artifact audit (grep for console.log, debugger, etc.)

Reference .claude/kiro/settings/rules/deterministic-enforcement.md for the zero-warning principle.
"""
)
```

## Display Result

Show the generated CI configuration to the user.

### Next Steps Guidance

- Review the generated configuration before committing
- Ensure CI environment has all required tooling installed
- Consider adding the generated file to version control
- After CI is running, escaped bugs can be tagged `[escaped]` in observations to feed the self-tightening loop
