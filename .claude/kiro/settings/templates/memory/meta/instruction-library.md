# Instruction Library — Incremental Prompt Optimization

Each instruction is a single, testable bullet. Add or remove instructions one at a time — every change is a "small PR with tests" that can be independently verified and rolled back.

## How Agents Consume This

Agent prompt files reference their category with: `## Instruction Library: see .claude/memory/meta/instruction-library.md [{category}]`. When an agent is invoked, the orchestrating command loads applicable instructions as additional constraints.

## How Instructions Evolve

1. The prompt-diagnosis-agent recommends ADD/REMOVE/SHARPEN changes
2. The evolve-agent converts these into instruction proposals (type: `add-instruction`, `remove-instruction`, `modify-instruction`)
3. User approves each change individually
4. `/kiro:harness-test regression` validates no regressions after changes

## Tag Convention

Each instruction is tagged with its concern: `[format]`, `[scope]`, `[evidence]`, `[reuse]`, `[tdd]`, `[precision]`, `[safety]`.

---

## Cross-Cutting
<!-- Apply to all agents regardless of category -->

## Validation Agents
<!-- validate-design, validate-impl, validate-adversarial, validate-gap, validate-perf -->

## Implementation Agents
<!-- spec-impl, fix-build, spec-refactor -->

## Scanning Agents
<!-- steering, doc-sync, reflect-agent, housekeeping -->

## Design Agents
<!-- spec-design, spec-requirements, spec-tasks -->
