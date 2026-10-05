# Deterministic Enforcement — Prefer Linters Over Documentation

Conventions that can be mechanically checked should be enforced by linter rules, not just documented in steering or markdown rules. Documentation is probabilistic (a suggestion agents may ignore); linter rules are deterministic (violations block progress).

## Principle

**If a convention can be expressed as a linter rule, the linter rule is the source of truth.** The markdown documentation becomes the rationale — explaining *why* the rule exists — while the linter prevents violations regardless of whether the agent reads the docs.

## When Writing Code

Before implementing, check whether the project has linter complexity rules configured:
- If complexity rules exist: respect them as hard constraints
- If no complexity rules exist: note this as a `[friction][enforceable]` observation

## Recommended Complexity Baselines

These are the minimum complexity guardrails every project should have:

| Ecosystem | Rules | Tool |
|-----------|-------|------|
| JS/TS | `max-lines-per-function: 40`, `complexity: 10`, `max-depth: 3`, `max-params: 4`, `max-statements: 15`, `@sonarjs/cognitive-complexity` | ESLint |
| Python | `max-complexity = 10`, `max-args = 5`, `C901` (McCabe), line length | ruff |
| Rust | `cognitive_complexity` threshold, `too_many_arguments` | clippy |
| Go | `gocyclo` threshold, `funlen` limits | golangci-lint |

All linters should run with **zero-warning tolerance** (`--max-warnings=0` or equivalent).

## Structural Baselines — the checks that look across functions

Every rule above looks inside a single function. None of them can see that a block
also exists in another file, that a helper has no callers left, or that a module
imported something it was never allowed to. Those are the failures agent-written
code accumulates, because the reviewer who used to catch them by reading the diff
is now outpaced by how fast diffs arrive.

| Concern | Tool |
|---|---|
| Duplicated code | `pyscn` (Python), `jscpd` (JS/TS, polyglot) |
| Dead code | `pyscn`, `vulture` (Python), `knip`, `ts-prune` (JS/TS) |
| Dependency direction | `import-linter` (Python), `eslint-plugin-boundaries` (JS/TS), `go-arch-lint` (Go) |

Two constraints on how these are wired, both load-bearing:

- **Gate on the delta, not the whole repository.** Fail when a change *adds* a clone
  group or violates a contract. A whole-repo gate on existing code reports hundreds
  of findings on its first run and gets disabled the same day, which is worse than
  never adding it — the config is present, so the repo reads as covered while nothing
  runs.
- **Make it agent-callable, not CI-only.** The benefit comes from the agent running
  the checker in the same session it wrote the code, while it still knows why two
  copies exist. By the time a clone group reaches a human reviewer, that context is
  gone and collapsing it is a chore.

**Track the average, not the grade.** Structural tools report a summary grade that
stays flat while the underlying numbers drift: in the source measurement, average
cyclomatic complexity moved from 6.9 to 8.5 across five snapshots without the grade
leaving A, because no single function crossed the high band. A grade is a reason to
look, not a verdict.

Source: codescan, "Ruff, mypy, pytest, and then what?" — `docs/sources/articles/README.md`.

## Frontend Design-Token Baselines (Tailwind, gated on detection)

For any project with a `tailwind.config.{js,ts,mjs,cjs}` present, add these to the
JS/TS baseline (source: shadcn-ui/lint):

| Rule | Catches | Why |
|------|---------|-----|
| `no-raw-colors` | `className="text-[#ff0000]"`, `rgb(...)` literals | Bypasses the design system's token palette — colors drift from the rest of the UI one component at a time |
| `no-arbitrary-values` | Tailwind arbitrary-value syntax, e.g. `w-[13px]` | A one-off magic number instead of a spacing/sizing token — same drift risk as `no-raw-colors`, for layout instead of color |
| `require-static-classes` | `className={\`p-2 ${dynamic}\`}` | Tailwind's JIT compiler statically scans source for class name strings; an interpolated template literal is invisible to it and the class silently never ships in the built CSS |

**Enforcement path:**
- **Minimal (built-in, zero extra deps):** `js-quality-gate-hook.sh`'s
  `tailwind_design_token_check()` runs a regex-only version of these three checks,
  gated on a `tailwind.config.*` file existing — advisory, no linter plugin required.
- **Full (opt-in upgrade):** `eslint-plugin-tailwindcss` for AST-accurate detection
  (handles conditional class construction, `clsx`/`cva` call sites, etc. that regex
  can't safely parse) once a project wants harder enforcement than the built-in check.

`guardrails-agent` detects Tailwind the same way (`tailwind.config.*` present, or
`tailwindcss` in `package.json` dependencies) and includes these three rules in its
JS/TS audit/scaffold report when detected.

## Graduation Path

Conventions follow a maturity path from probabilistic to deterministic:

1. **Observation**: A convention violation is noticed and tagged `[friction]`
2. **Pattern**: The violation recurs (3+ observations on the same theme)
3. **Documentation**: The convention is written into steering or a markdown rule
4. **Graduation**: The convention is encoded as a linter rule (deterministic enforcement)
5. **Rationale**: The markdown doc remains as the *why*; the linter is the *what*

Architectural rules follow the same path and are the most common thing to leave stuck
at step 3. A rule like "features do not import each other" or "the tool layer may not
reach past the service layer" is stated in a markdown file, agents mostly comply, and
the import graph drifts anyway. Graduate it into a dependency contract (`import-linter`,
boundary rules) and the drift stops being possible.

The `/kiro:evolve` command identifies graduation candidates. The `/kiro:guardrails` command applies them.

## Relationship to Other Rules

- **steering-principles.md**: Steering documents conventions; this rule says enforceable conventions should also become linter rules
- **quality-gates.md**: Gate 1 (`/kiro:verify`) runs linters; this rule ensures linter configs are comprehensive
- **self-tightening.md**: Formalizes the loop that feeds friction into deterministic enforcement
