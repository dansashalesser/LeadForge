# Quality Gates — Pre-Completion Verification Sequence

Before marking a feature or spec implementation as complete, run these gates in order. Each gate must pass before proceeding to the next.

## Gate Sequence

### Gate 1: Code Verification (`/kiro:verify`)
Confirms the code compiles, tests pass, and no debug artifacts remain.
- **Required for**: All features and bug fixes
- **Pass criteria**: All stages PASS or WARN (no FAIL)
- **On failure**: Fix issues directly or use `/kiro:fix-build` for build errors

### Gate 2: Spec Alignment (`/kiro:validate-impl`)
Confirms implementation matches approved requirements, design, and tasks.
- **Required for**: All spec-driven features
- **Pass criteria**: GO decision from validation agent
- **On failure**: Follow remediation plan, then re-validate

### Gate 3: Adversarial Review (`/kiro:validate-adversarial`)
Three-pass adversarial review for high-confidence validation.
- **Required for**: Critical features, security-sensitive code, public-facing APIs
- **Optional for**: Internal utilities, minor enhancements
- **Pass criteria**: Positive net score from adversarial passes

### Gate 4: Performance Review (`/kiro:validate-perf`)
Checks for N+1 queries, unbounded operations, missing indexes, blocking I/O.
- **Required for**: Data-heavy features, API endpoints, database operations
- **Optional for**: UI-only changes, configuration updates
- **Pass criteria**: No critical performance issues detected

### Gate 5: Production Readiness (`validate-production-agent`)
Scans for deployment gaps: env config, error handling resilience, monitoring, process management, backups, staging. Generates a human attestation checklist for items only people can verify.
- **Auto-triggered**: Runs automatically when spec-impl completes all tasks for a feature
- **Required for**: Any deployment to staging or production
- **Optional for**: Local-only prototypes, libraries, internal tools
- **Pass criteria**: No critical automated findings + human attestation items listed for acknowledgment

## When to Apply

- **After completing all tasks in a spec**: Gates 1-2 minimum, Gate 3 for critical features
- **Before creating a PR**: Gates 1-2 minimum
- **Before deployment**: Gates 1-5 required. Gate 5 auto-triggers after spec-impl completes all tasks.

## Shorthand

For quick reference during implementation:
1. `/kiro:verify` — Does it build and pass?
2. `/kiro:validate-impl` — Does it match the spec?
3. `/kiro:validate-adversarial` — Can we poke holes in it?
4. `/kiro:validate-perf` — Will it perform at scale?
5. `validate-production-agent` — Is it production-ready? (auto-triggered)
