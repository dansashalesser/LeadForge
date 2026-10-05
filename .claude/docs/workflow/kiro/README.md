# Kiro — Spec-Driven Development Engine

> Detailed reference for the Kiro SDD subsystem within the harness.

## What It Is

Kiro is the core spec-driven development (SDD) engine. It provides a structured, phase-gated workflow for turning feature descriptions into implemented, tested code — with human review gates between every phase.

The workflow enforces: **Requirements → Design → Tasks → Implementation**, each phase producing a concrete artifact that the next phase consumes.

## Origin

Based on [cc-sdd](https://www.npmjs.com/package/cc-sdd) (`npx cc-sdd@latest`), adapted with custom path remapping and additional agents (doc-sync, harness-updater, reflect, housekeeping, evolve).

## Components

### Slash Commands (`commands/kiro/`)

| Command | Phase | Description |
|---|---|---|
| `steering` | Setup | Bootstrap/refresh project memory from codebase scan |
| `steering-custom` | Setup | Add domain-specific steering (auth, DB, API, etc.) |
| `idea-refine` | 0. Ideation | Refine vague ideas into spec-ready briefs (wired into spec-quick) |
| `spec-init` | 1. Init | Create spec workspace in `specs/` with metadata |
| `spec-requirements` | 2. Requirements | Generate EARS-format requirements; marks ambiguities as `[NEEDS CLARIFICATION]` for user resolution before approval |
| `spec-design` | 3. Design | Research codebase + produce technical design |
| `spec-grill` | 3.5 Grill | Interactive domain-expert grilling session; aligns terminology, updates requirements + design inline, writes ADRs |
| `spec-tasks` | 4. Tasks | Break design into parallelizable task list; `--sequential` disables `(P)` markers; output includes Parallel Execution Groups section listing safe concurrent invocation groups |
| `spec-impl` | 5. Implement | TDD implementation with self-review after each task; runs Phase -1 Pre-Implementation Gates (simplicity, anti-abstraction, integration-first, **decision-budget**) before delegating, and `audit-choices` after each pass |
| `audit-choices` | 5.5 Audit | Record decisions made where the spec was **silent** into `specs/<f>/choices.md` as sound/unsound/needs-user, each with a reversible provisional call. Changes no code, never blocks. `--close` consolidates at spec close |
| `spec-quick` | 2-5 (fast) | Requirements → Design → Grill → Tasks in one command; grill skipped with `--auto` |
| `spec-status` | Any | Show current phase, approvals, open tasks |
| `validate-gap` | Review | Requirements vs. existing code gap analysis |
| `validate-design` | Review | Design quality review (with remediation on NO-GO) |
| `validate-impl` | Review | Implementation vs. spec validation (with remediation) |
| `validate-adversarial` | Review | Three-pass adversarial review with +1/-2 scoring |
| `converge` | Review | Spec ↔ code reconciliation — bidirectional drift (spec-ahead / code-ahead / contradiction); reuses `validate-impl-agent` |
| `debug` | Debugging | Systematic 6-step bug triage (wired into jira-solve for BUGs) |
| `simplify` | Maintenance | Behavior-preserving code simplification (wired into spec-refactor) |
| `ship` | Deployment | Launch readiness: Step 0 `/simplify` (diff cleanup), then verification + production validation + rollout planning |
| `sync-docs` | Maintenance | Sync `.md` files with code changes |
| `reflect` | Memory | Mine session learnings, update observations/patterns |
| `housekeeping` | Memory | Prune memory, archive old observations |
| `evolve` | Meta | Audit harness rules, propose improvements |
| `harness-fix` | Meta | Encode behavioral prevention rule from a specific mistake |
| `harness-validate` | Meta | Structural integrity check of the harness |
| `harness-test` | Meta | Haiku smoke-test to expose vague prompts |
| `guardrails` | Meta | Audit/scaffold linter complexity rules for deterministic enforcement |
| `ci-scaffold` | Meta | Generate CI configuration mirroring the verify pipeline |

### Subagents (`agents/kiro/`)

Each command delegates to a specialized subagent. Agents are autonomous — they receive a prompt with file patterns, execute their protocol, and return a summary.

Key agents beyond the spec pipeline:
- **idea-refine-agent** — Pre-spec ideation with divergent/convergent thinking. Produces structured briefs for `spec-init`.
- **debug-agent** — Systematic 6-step debugging: reproduce → localize → reduce → fix → guard → verify. Classifies recovery strategy (transient/LLM-recoverable/user-fixable/unexpected). Won't fix without reproduction.
- **simplify-agent** — Behavior-preserving simplification with Chesterton's Fence. Runs tests before AND after. Documents intentional complexity.
- **ship-agent** — Generates staged rollout plans with decision thresholds, rollback procedures, and feature flag recommendations.
- **doc-sync** — Triggered by post-commit hook or `/kiro:sync-docs`. Diffs recent commits against `.md` files, updates stale docs, and detects stale doc-to-code references (reverse validation). From the hook, its `.md` scan excludes `.claude/` (regenerated install mirror) alongside `.venv/`, `.git/`, and `__pycache__/`; steering updates come from `/kiro:sync-docs`, not the hook.
- **harness-updater** — Triggered by the post-commit hook when harness **source** files change (`agents/`, `commands/`, `hooks/`, `kiro/`, `scripts/`, `rules/`, `templates/`, `skills/`, `CLAUDE.md`); `.claude/` is generated output and never a trigger. Runs after doc-sync inside the hook's single detached background job. That job is serialized on `.git/post-commit-docsync.lock` (atomic `mkdir`) — a commit made while a previous run is still in flight is skipped (`=== skipped <date>: another doc-sync run is active ===` in the log) rather than run in parallel, so concurrent runs cannot race on the git index. Locks older than 30 minutes are stolen. Updates `SDD-SETUP-GUIDE.md`.
- **reflect-agent** — Mines `git log` for observations, promotes patterns (3+ distinct observations, falsifiable), updates hot-memory; Step 6 runs a five-dimension session clean-state check (build/tests/progress/artifacts/startup path) and adds corrective action items for any unmet dimension.
- **housekeeping-agent** — Archives observations to glacier, enforces memory caps.
- **evolve-agent** — Measures memory health metrics, analyzes agent trace logs, detects friction patterns, proposes rule changes and linter rule graduations; Step 1d audits instruction architecture (entry file bloat, SNR, middle-placement, topic docs); Step 1e checks session clean-state discipline; output includes "Harness Architecture Health" scorecard.
- **guardrails-agent** — Audits project linter configs for complexity rules; scaffolds missing guardrails per ecosystem (ESLint, ruff, clippy, golangci-lint).
- **ci-scaffold-agent** — Generates CI configs (GitHub Actions, GitLab CI, Azure Pipelines) mirroring the verify pipeline stages.
- **validate-adversarial** — Three-pass adversarial review (neutral → refutation → judge synthesis with +1/-2 scoring).
- **harness-validate-agent** — Checks structural integrity: command→agent references, template existence, memory caps, L0 headers; generates component relationship index; Step 8 audits instruction architecture (line count, constraint count, topic doc adoption, middle-placement); Step 9 audits feature list primitive compliance (triple structure, WIP=1, pass-state).
- **spec-refactor** — Auto-spawned after each impl task's VERIFY step. Reviews touched files for reuse/quality/efficiency.

### Rules (`kiro/settings/rules/`)

Rules control agent behavior:
- `ears-format.md` — EARS requirement syntax
- `design-principles.md` — Design doc structure and quality criteria
- `design-discovery-light.md` / `design-discovery-full.md` — Codebase research depth
- `design-review.md` — Review checklist for designs
- `tasks-generation.md` — Task breakdown rules
- `tasks-parallel-analysis.md` — Parallelism detection (P-wave)
- `gap-analysis.md` — Gap analysis methodology
- `steering-principles.md` — Steering doc conventions (with L0 header requirement)
- `memory-conventions.md` — Memory format, caps, and lifecycle rules
- `agent-output-format.md` — Standardized agent return format with output recovery tiers
- `agent-tracing.md` — Invocation trace log for observability
- `test-backlinks.md` — Spec traceability convention in test files
- `model-tiering.md` — Cost optimization tiers (Opus/Sonnet/Haiku) with smoke testing
- `context-hygiene.md` — Context rot prevention with degradation detection
- `skill-extraction-scoring.md` — 4-criteria rubric for skill extraction
- `deterministic-enforcement.md` — Prefer linters over documentation for enforceable conventions
- `property-testing.md` — When and how to use property-based tests in TDD
- `self-tightening.md` — Self-improving feedback loop formalization (L0-L3 maturity)
- `anti-rationalization.md` — Phase-specific rationalization tables with factual counterarguments
- `red-flags.md` — Observable violation indicators by workflow phase (STOP/WARN/NOTE severity)
- `context-engineering.md` — 5-level information hierarchy, context budget guidance, and ACON-based priority ordering for token reduction under context pressure
- `agent-swarm-topologies.md` — 4 coordination topologies (hierarchical/mesh/ring/star) with decision logic, model-tier integration, and anti-patterns. Applied automatically when orchestrating multi-agent tasks. Source: Ruflo.

### Templates (`kiro/settings/templates/`)

Starting-point files for specs, steering docs, and memory files. Used by `spec-init`, `steering`, and `reflect` to bootstrap new artifacts.

## Use Cases

1. **New feature development** — Full SDD pipeline from idea to implementation
2. **Brownfield features** — Use `validate-gap` to assess existing code before spec'ing the delta
3. **Bug fixes** — Use `spec-quick` for a lightweight spec, then `spec-impl` for TDD
4. **Codebase onboarding** — Run `steering` to generate project knowledge docs
5. **Quality audits** — Run `validate-impl` against existing specs to find drift

## How to Use

### Full workflow (large features)
```
/kiro:steering                          # once per project
/kiro:spec-init "Add feature X"        # create spec workspace
/kiro:spec-requirements feature-x       # generate requirements → Proof review → approve
/kiro:spec-design feature-x             # generate design → Proof review → approve
/kiro:spec-grill feature-x              # domain grilling session → CONTEXT.md + ADRs
/kiro:spec-tasks feature-x              # generate tasks → Proof review → approve
/kiro:spec-tasks feature-x --sequential  # same, but tasks strictly ordered (no P markers)
/kiro:spec-impl feature-x              # implement via TDD
/kiro:reflect                           # capture session learnings
```

### Fast path (small features / bug fixes)
```
/kiro:spec-quick "Fix the broken auth redirect"
/kiro:spec-impl fix-auth-redirect
```

### Maintenance
```
/kiro:sync-docs                         # manual doc sync
/kiro:housekeeping                      # prune memory
/kiro:evolve                            # audit harness effectiveness
```

## Setup

Installed via `npx cc-sdd@latest --claude-agent --lang en`, then path-remapped with `scripts/remap-ccsdd-paths.sh`. See `SDD-SETUP-GUIDE.md` Steps 3–4 for details.

_Last synced: 2026-07-28_
