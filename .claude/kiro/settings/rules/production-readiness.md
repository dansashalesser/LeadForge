# Production Readiness — Checklist Categories

Defines the production readiness criteria used by `validate-production-agent`. This gate catches deployment gaps that code-level reviews miss: missing infrastructure, operational blind spots, and untested production scenarios.

**Scope boundary**: This rule covers project-level readiness. Code-level security (injection, XSS) is covered by `spec-refactor`. Debug artifacts (`console.log`) are covered by `verify-agent`. Performance anti-patterns (N+1, unbounded ops) are covered by `validate-perf-agent`.

## Automated Scan Categories

### A. Environment Configuration
- `.env.example` or `.env.template` must exist if env vars are used in source
- No hardcoded secrets in source files (API keys, passwords, tokens, connection strings)
- All env vars referenced in code should be documented in the env template

### B. Deployment & Process Management
- Containerization present (Dockerfile or docker-compose) OR documented deployment method
- Process manager configured (pm2, supervisor, systemd, Procfile) OR container orchestration
- Health check endpoint exposed (`/health`, `/healthz`, `/readyz`)

### C. Resilience Patterns
- HTTP clients configured with timeouts (not relying on defaults)
- Retry or circuit-breaker patterns for external service calls
- Graceful shutdown handlers for SIGTERM/SIGINT

### D. Observability
- Structured logging library (not bare console.log/print as primary logging)
- Error tracking service configured (Sentry, Datadog, etc.)
- Alerting rules for critical failures

### E. Data Safety
- Database backup strategy present (scripts, cron, managed service config)
- Destructive migrations have rollback plans

### F. Security Posture
- CORS configured for API endpoints
- Rate limiting on public-facing endpoints
- Security headers middleware (helmet, secure-headers, etc.)

### G. Staging & CI
- Multi-environment configuration (staging + production at minimum)
- CI pipeline configured and running

## Human Attestation Items

These cannot be automated — developer must acknowledge before production deployment:

| ID | Item | Why It Matters |
|----|------|---------------|
| H1 | Tested on another device/environment | "Works on my machine" is not a green light |
| H2 | Load tested with concurrent traffic | First real spike reveals all bottlenecks |
| H3 | Tested on mobile devices | 60%+ of users may be on phones |
| H4 | Critical flows tested E2E with real credentials | Payment/signup failures are launch-day killers |
| H5 | Error logs reviewed recently | Silent failures are already happening |
| H6 | Scaling plan beyond free tier | First traffic spike hits resource limits |
| H7 | Secrets in vault/CI, not manual SSH | Server rebuild = hunting env vars from memory |
| H8 | Failover/redundancy tested | Single point of failure is not an architecture |
| H9 | AI-generated code audited | The 40% you don't understand is a time bomb |

## Severity Rules

**Critical** (blocks deployment):
- Hardcoded secrets in source
- Missing env template with env vars in code

**Warning** (deployment risk):
- No containerization or process manager
- No health endpoint
- No logging framework or error tracking
- No CORS or rate limiting on public endpoints
- No CI pipeline or staging environment
- No backup strategy
- Missing HTTP client timeouts
- Dangerous migrations without rollback

**Info** (recommended):
- No retry/circuit-breaker
- No alerting config
- No security headers
- No graceful shutdown

## When to Apply

- **Auto-triggered**: Runs after spec-impl completes all tasks for a feature
- **Required for**: Any code destined for staging or production
- **Optional for**: Local-only prototypes, libraries, internal tools with no external users
