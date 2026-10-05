# Self-Tightening Loop — Every Failure Strengthens the System

The harness should function as a self-improving organism: every bug, friction point, and convention violation feeds back into stronger guardrails. The goal is to make mistakes impossible, not just unlikely.

## The Core Loop

```
Agent produces code
  → Verify catches issues (or issues escape to CI/production)
  → Observation recorded with [friction] or [enforceable] tag
  → Reflect promotes recurring friction to patterns
  → Evolve proposes rule changes or linter graduation
  → Guardrails applies deterministic enforcement
  → Agent cannot repeat the same mistake
```

Every iteration of this loop makes the system stricter. The organism feeds on failures.

## Tagging Convention

When recording observations, use these tags to feed the self-tightening loop:

| Tag | Meaning | Consumed By |
|-----|---------|-------------|
| `[friction]` | Something was harder than it should be | evolve-agent |
| `[enforceable]` | This convention could be a linter rule | evolve-agent (graduation pipeline) |
| `[escaped]` | Bug that passed validation but failed in CI/prod | evolve-agent (highest priority) |

An observation can have multiple tags: `[friction][enforceable]`

## Maturity Levels

Projects progress through enforcement maturity:

### L0 — Vibes
- Manual review only
- Agent output trusted at face value
- "My eyes are the only thing between agent and production"

### L1 — Guardrails
- Standard linters + CI configured
- `/kiro:verify` runs as quality gate
- Architecturally, agents can still drift

### L2 — Architecture as Code
- Custom lint rules encode team conventions
- Steering docs backed by deterministic enforcement
- `/kiro:guardrails` has been run and complexity rules are active
- Graduation pipeline converts friction into linter rules

### L3 — Organism
- Self-tightening loop is active and continuous
- Schedule agents for safe tasks, review diffs next morning
- Every escaped bug becomes a new lint rule or validation check
- Friction trends toward zero over time

## Rules of the Loop

1. **Every repeated PR comment should become a lint rule** — if you tell the agent the same thing twice, encode it
2. **Every escaped bug should become a prevention rule** — tag it `[escaped]`, then graduate to enforcement
3. **Every CI failure that recurs should become a pre-commit check** — move the gate earlier
4. **Documentation without enforcement is a suggestion** — respect it, but strive to graduate it
5. **The evolve command is the loop's engine** — run it periodically to process accumulated friction

## Relationship to Other Rules

- **deterministic-enforcement.md**: Defines the graduation path from docs to linters
- **quality-gates.md**: The verification stages where issues are caught
- **loop-safety.md**: Prevents agents from looping; this rule ensures the *system* loops productively
