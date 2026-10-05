# CLAUDE.md Review Report — 2026-10-04

## Summary
- Files checked: 1
- Clean: 0 | Minor: 1 | Needs update: 0
- Largest file: CLAUDE.md (53 lines)

Note: no `AGENTS.md`, no nested instruction files, no `package.json`/`pyproject.toml`/`Cargo.toml` in repo.

## Findings

### CLAUDE.md — minor (53 lines)

- **Stale model-assumption:** `- **Context rot**: AI coherence degrades past ~300k tokens — keep functions and PRs small` — the ~300k figure predates the 1M-context models now driving this repo. The *advice* (small functions, small PRs) is load-bearing and should stay; the hard number is the stale part and invites the agent to reason about a budget it no longer has.
- **Signal test / misplacement:** the `lean-ctx enforces a shell allowlist: ...` bullet sits under `## Quality Gates (automated)`, but it is not a gate — it is a tool-failure remedy. Same content already belongs with the lean-ctx rules in `.claude/rules/lean-ctx.md`. Costs a slot in the always-loaded file for something only relevant at the moment a shell call is refused.

Everything else passes: size budget is comfortable (53/200), no stack restatement, no intra-file duplication, the plan/spec gates are already narrowed by explicit exceptions ("when the design is genuinely open", "Bugfixes, perf work, and tooling do not need a spec"), and no two lines conflict. The Serena block, the Rule of Three, the 3rd-patch rule, and the boundary-tests rule are all project-specific signal — not cut candidates.

## Proposed Changes

Proposals only — not applied (hook-invoked run).

### CLAUDE.md

```diff
- - **Context rot**: AI coherence degrades past ~300k tokens — keep functions and PRs small
+ - **Context rot**: coherence degrades as context fills, well before the window is full — keep functions and PRs small
```

```diff
- - lean-ctx enforces a shell allowlist: `bash`, `sh`, `zsh`, `uvx`, `claude` and `python3 -c` are refused by default. The "permanent restriction" wording is not a policy refusal — run `lean-ctx allow <cmd>` rather than abandoning the check
```
Move verbatim into `.claude/rules/lean-ctx.md` (which already loads as project instructions) rather than deleting the content.
