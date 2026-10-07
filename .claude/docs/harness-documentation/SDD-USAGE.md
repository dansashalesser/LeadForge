<!-- L0: Quick reference — all SDD commands with usage examples -->

_Last synced: 2026-10-01_

# SDD Usage Guide

How to use the Spec-Driven Development harness day-to-day.

---

## Project Memory

### `/kiro:steering` — Bootstrap or refresh project knowledge
Scans the codebase and generates `.claude/steering/` files (product, tech, structure).

```
/kiro:steering
```
Run once on a new project, or after major architectural changes.

### `/kiro:steering-custom` — Add domain-specific steering
Creates a focused steering doc for a specific domain.

```
/kiro:steering-custom database
/kiro:steering-custom authentication
/kiro:steering-custom api-standards
```

---

## Ideation & Debugging

### `/kiro:idea-refine` — Refine a vague idea into a spec-ready brief (or chart a program map)
Takes a rough idea through structured divergent/convergent thinking to produce a clear problem statement, proposed solution, constraints, and a paste-ready description for `/kiro:spec-init`.

For **program-scale** ideas — too big for one spec (`Skill("issue-triage-routing")` axis 4: "build the whole X system") — it charts `specs/_maps/<name>.md` instead: a Destination/Decisions-so-far/Fog/Out-of-scope index, then automatically decomposes the first fog item into a slice and hands it to the normal spec flow. No separate command to remember — `/kiro:spec-tasks` auto-updates the map's Decisions-so-far when each slice's tasks are approved, and re-running `/kiro:idea-refine` on the same idea picks up the next fog item.

```
/kiro:idea-refine "something to help users track their spending"
/kiro:idea-refine "we need better error handling"
/kiro:idea-refine "rebuild our whole reporting platform"   → charts specs/_maps/, decomposes first slice
```

Wired into `/kiro:spec-quick` — in interactive mode, if the description is vague, you'll be prompted to refine first.

### `/kiro:debug` — Systematic 6-step bug triage
Follows: Reproduce → Localize → Reduce → Fix → Guard → Verify. Won't guess at a fix without reproduction first. Adds a regression test automatically.

```
/kiro:debug "TypeError: Cannot read property 'id' of undefined in user profile endpoint"
/kiro:debug "intermittent 500 errors on /api/tasks when payload is empty"
```

Wired into `/kiro:jira-solve` — BUG/DEFECT tickets route through the debug methodology automatically.

### `/kiro:simplify` — Behavior-preserving code simplification
Reduces complexity while preserving identical behavior. Follows Chesterton's Fence: understands why code exists before removing it. Runs tests before AND after.

```
/kiro:simplify src/services/auth.ts
/kiro:simplify revenue-trend-chart        # simplify files from a feature's tasks
```

Wired into spec-refactor — if 3+ complexity findings detected during self-review, you'll be prompted to run simplify.

### `/kiro:ship` — Launch readiness check
Coordinates verification → production validation → rollout planning. Generates staged rollout plan with decision thresholds and rollback procedure.

```
/kiro:ship revenue-trend-chart
/kiro:ship                                 # general project readiness
```

Wired into `/kiro:verify` — after successful pre-pr verification, suggested as next step for production deployment.

---

## Spec Workflow

### `/kiro:spec-init` — Start a new feature
Creates a spec workspace in `specs/` with metadata tracking.

```
/kiro:spec-init "Add revenue trend chart to dashboard"
```

### `/kiro:spec-requirements` — Generate requirements
Produces EARS-format requirements for an initialized spec. After generation, opens a **[Proof](https://github.com/anthropics/proof) collaborative review session** — a shared URL where you annotate, comment, and approve the requirements document. The approved version is written back before proceeding.

```
/kiro:spec-requirements revenue-trend-chart
```

### `/kiro:spec-design` — Generate technical design
Researches the codebase and produces a design doc with architecture decisions. After generation, opens a **Proof collaborative review session** for annotation and approval before proceeding to tasks.

```
/kiro:spec-design revenue-trend-chart
```

### `/kiro:spec-grill` — Domain grilling session
Runs an interactive domain-expert questioning session against the approved requirements and design. Asks one question at a time, waits for your answer, and updates `requirements.md`, `design.md`, and `CONTEXT.md` inline as decisions crystallise. Writes warranted Architecture Decision Records (ADRs) to `specs/\<feature\>/docs/adr/`. Requires design phase to be approved first.

```
/kiro:spec-grill revenue-trend-chart
```

Signal completion with "done", "looks good", or "move on". Also available as phase 3.5 in `/kiro:spec-quick` (interactive mode); skipped in `--auto` mode since it requires user interaction.

### `/kiro:spec-tasks` — Generate implementation tasks
Breaks the design into parallelizable tasks with dependencies. After generation, opens a **Proof collaborative review session** for approval before implementation begins. Pass `--sequential` to suppress parallel `(P)` markers when you want strictly ordered tasks. On approval, also checks `specs/_maps/*.md` for a parent program map referencing this feature and auto-moves it from fog to Decisions-so-far — no manual map upkeep.

```
/kiro:spec-tasks revenue-trend-chart
/kiro:spec-tasks revenue-trend-chart --sequential   # disable parallel task markers
```

### `/kiro:spec-quick` — Fast path (requirements → design → grill → tasks)
Runs all spec phases in one command: requirements → design → grill → tasks. Good for small features. In interactive mode, prompts at each phase and runs the grill session. Pass `--auto` to skip prompts and grill (which requires user interaction).

```
/kiro:spec-quick "Add retry logic to SQL query execution"
```

### `/kiro:spec-impl` — Implement from approved spec
Executes tasks via TDD (test first, then code, then verify). After each task's VERIFY step passes, a self-review agent automatically inspects the touched files for code reuse, quality, and efficiency issues, fixes confirmed issues (skipping false positives), and re-runs the tests — stopping and surfacing any failures before marking the task complete. The self-review findings appear in the final summary.

```
/kiro:spec-impl revenue-trend-chart
```

Two gates bracket the delegation. **Before**: Phase -1 adds a Decision-Budget check to the existing simplicity / anti-abstraction / integration-first trio — the first three catch *too much* (over-engineering), this one catches *too little* (under-specification), asking whether every task leaves the implementer inheriting decisions rather than making them, and whether every deliberately-open freedom is named as delegated. **After**: `/kiro:audit-choices` runs automatically on each pass.

### `/kiro:audit-choices` — Record decisions the spec never made
Reconstructs the choices the implementation made where the spec said nothing, verdicts each as `sound` / `unsound` / `needs-user`, and appends them to `specs/<feature>/choices.md`.

This closes a gap the other reviews leave open. `validate-impl` and `validate-adversarial` check the artifact; `session-quality` and `.claude/behaviors/` check conduct. Neither catches an implementation that is correct, tested, and passes every gate while quietly embedding an architecture nobody chose — because a diff shows what was built and says nothing about what was silently discarded.

It **changes no code** and **never blocks**: every `needs-user` entry carries a reversible provisional call, so an unattended run finishes with an open ledger rather than stalling. An irreversible provisional is reported as irreversible, not dressed up.

Runs per pass, not once at the end — waiting means auditing a session trace that has been compacted away and subagent reports that no longer exist.

```
/kiro:audit-choices revenue-trend-chart           # audit the pass just finished
/kiro:audit-choices revenue-trend-chart --close   # resolve open calls, consolidate
```

Read the ledger as a signal, not just a record: entries clustering on one slice mean that slice was under-specified and should be resliced; a pass heavy with `needs-user` means the Decision-Budget Gate should have failed first.

### `/kiro:spec-status` — Check spec progress
Shows current phase, approvals, and open tasks.

```
/kiro:spec-status revenue-trend-chart
```

---

## Verification & Error Recovery

### `/kiro:verify` — Run verification pipeline
Runs a multi-stage pipeline: build, type-check, lint, test, debug artifact audit, and git status. Reports structured PASS/FAIL per stage.

```
/kiro:verify              # full (all 6 stages)
/kiro:verify quick        # build + test only
/kiro:verify pre-commit   # build + types + lint + test
/kiro:verify pre-pr       # all stages with stricter thresholds
```

### `/kiro:fix-build` — Resolve build errors automatically
Runs diagnostics, categorizes errors, and applies minimal surgical fixes. Hard cap of 3 attempts.

```
/kiro:fix-build
```

### `/kiro:checkpoint` — Named workflow checkpoints
Create, compare, or restore named save points during long implementation sessions.

```
/kiro:checkpoint save task-1.2-done     # create checkpoint
/kiro:checkpoint compare task-1.2-done  # show changes since
/kiro:checkpoint list                   # show all checkpoints
/kiro:checkpoint restore task-1.2-done  # soft reset (with confirmation)
```

---

## Validation

### `/kiro:validate-gap` — Requirements vs. code gap analysis
Checks what's been implemented vs. what's required.

```
/kiro:validate-gap revenue-trend-chart
```

### `/kiro:validate-design` — Design quality review
Reviews the design doc for completeness and consistency.

```
/kiro:validate-design revenue-trend-chart
```

### `/kiro:validate-impl` — Implementation validation
Verifies code matches the spec (requirements + design + tasks). Also checks spec integrity — flags requirements.md/design.md edits made after approval that quietly weaken acceptance criteria, so implementation can't rewrite the spec it's being judged against. On NO-GO, provides a structured remediation plan with `filepath:line` references.

```
/kiro:validate-impl revenue-trend-chart
```

### `/kiro:converge` — Spec ↔ code reconciliation
Detects bidirectional drift between an approved spec and the live code — spec items never implemented, features built that no spec item asked for, and design decisions the code contradicts. Where `validate-impl` asks "does the code satisfy the spec?", `converge` asks "have the spec and the code diverged, and in which direction?" Reuses `validate-impl-agent` in a bidirectional reconciliation mode.

```
/kiro:converge revenue-trend-chart
/kiro:converge                                # auto-detect active spec
```

### `/kiro:validate-adversarial` — High-confidence adversarial review
Three-pass review: (1) neutral assessment, (2) adversarial refutation, (3) judge synthesis with asymmetric +1/-2 scoring. Use for high-stakes validations.

```
/kiro:validate-adversarial revenue-trend-chart design
/kiro:validate-adversarial revenue-trend-chart impl
```

### `/kiro:validate-perf` — Performance anti-pattern review
Checks for N+1 queries, unbounded operations, blocking I/O, missing indexes, and caching opportunities.

```
/kiro:validate-perf revenue-trend-chart
/kiro:validate-perf                           # auto-detect from git diff
```

### Production Readiness (Gate 5) — Auto-triggered
Scans for deployment gaps (env config, containerization, resilience, observability, data safety, security posture, staging/CI) and generates a human attestation checklist. **Auto-triggered** when `/kiro:spec-impl` completes all tasks for a feature — no manual invocation needed.

---

## Documentation Sync

### `/kiro:sync-docs` — Sync docs with code changes
Finds all `.md` files referencing changed code and updates them. Runs automatically at session end, but can be triggered manually.

```
/kiro:sync-docs
```

---

## Memory (Cog)

### `/kiro:reflect` — Mine session learnings
Reviews recent work, extracts observations, promotes patterns, updates hot-memory.

```
/kiro:reflect
```
Run after completing a spec, finishing a debugging session, or at end of a productive session.

### `/kiro:learn-eval` — Quality-gated pattern evaluation
Evaluates session patterns with quality scoring (specificity, actionability, evidence). Deduplicates against existing knowledge and produces save/absorb/route/drop verdicts (route = skill-tied lesson pushed into its skill instead of memory). Deeper than `/kiro:reflect` — use periodically.

```
/kiro:learn-eval              # evaluate current session
/kiro:learn-eval sprint       # evaluate since last learn-eval
/kiro:learn-eval feature      # evaluate patterns from a specific spec
```

### `/kiro:save-session` — Save session for later
Captures a structured Progress Tracker (feature, git baseline/head, tasks completed/remaining, blockers, next action) plus narrative sections: what worked, what didn't, untried approaches, file states, and the exact next step.

```
/kiro:save-session bug-fix-auth
/kiro:save-session                # auto-named with timestamp
```

### `/kiro:resume-session` — Resume a saved session
Loads and displays a session snapshot. If the session contains a Progress Tracker, auto-orients with a Pickup Briefing (commits since save, blocker status, suggested next action). You decide what to do next.

```
/kiro:resume-session bug-fix-auth
/kiro:resume-session              # most recent session
/kiro:resume-session list         # show all saved sessions
```

### `/kiro:context-budget` — Analyze context token usage
Measures token footprint of steering, memory, rules, and CLAUDE.md. Recommends optimizations.

```
/kiro:context-budget
```

### `/kiro:housekeeping` — Prune and archive memory
Archives old observations to glacier, enforces caps, validates formats.

```
/kiro:housekeeping
```
Run when the stop-hook nudges you, or periodically.

### `/kiro:evolve` — Audit harness rules and agent prompt quality
Measures memory health, detects friction patterns, proposes rule improvements. Includes:
- **Graduation pipeline** — identifies conventions suitable for promotion from docs to linter enforcement
- **Alignment analysis** — computes per-agent alignment scores from trace.log, flags underperformers
- **Prompt diagnosis** — for flagged agents, produces structured root cause analysis with specific instruction changes (ADD/REMOVE/SHARPEN)
- **Data-driven tiering** — recommends model tier promotions/demotions based on alignment evidence
- **Instruction architecture health** (Step 1d) — audits entry file for bloat (>200 lines), low SNR (<60%), hard constraints after line 50 (lost-in-middle), missing topic documents
- **Session clean state health** (Step 1e) — checks PROGRESS.md freshness, debug artifact presence, verify path documentation; output includes "Harness Architecture Health" scorecard

```
/kiro:evolve
```
Run on demand when something feels off about the workflow, or periodically to audit prompt quality. After approving proposals:
- `graduate-to-linter` → run `/kiro:guardrails scaffold`
- `add/remove/modify-instruction` → run `/kiro:harness-test regression`
- `adjust-tier` → run `/kiro:harness-test {agent-name}` at the new tier

### `/kiro:guardrails` — Audit and scaffold enforcement guardrails
Checks your project's configuration across four independent dimensions and scaffolds what is missing. Supports ESLint (JS/TS), ruff (Python), clippy (Rust), and golangci-lint (Go).

```
/kiro:guardrails              # audit: check existing config across all four dimensions
/kiro:guardrails scaffold     # create or enhance config with recommended baselines
/kiro:guardrails report       # show enforcement maturity level (L0-L3)
```

The four dimensions are reported separately, never summed — a project can score full marks on the first and zero on the rest, which is the normal case:

| Dimension | Caps | Typical tool |
|---|---|---|
| Complexity | how tangled one function is | ESLint, ruff, clippy, golangci-lint |
| Type evidence (JS/TS) | how much type information it threw away | oxlint + anti-slop |
| Assertion strength | whether the tests prove anything | mutmut, Stryker |
| Structure | duplication, dead code, import direction — **across** functions | pyscn, jscpd, knip, import-linter |

Recommended complexity baselines: `max-lines-per-function: 40`, `complexity: 10`, `max-depth: 3`, `max-params: 4`, with zero-warning tolerance (`--max-warnings=0`).

The structural dimension is the one a standard pipeline cannot reach: no linter has a rule for "this block also exists in another file", nothing knows which modules may import which, and a helper with no callers left lints and type-checks clean because no call site disagrees with it. Those failures used to be caught by a human reading the diff, which stopped scaling once agents started producing diffs faster than they get read. Two constraints on how it is scaffolded, both load-bearing: **gate on the delta**, never on whole-repo state (a first run reporting hundreds of findings gets the check disabled the same day, leaving the repo worse off than never adding it), and make the checker **agent-callable**, not CI-only — the benefit comes from the agent running it in the session it wrote the code, while it still knows why two copies exist.

### `/kiro:ci-scaffold` — Generate CI configuration
Generates a CI configuration that mirrors the `/kiro:verify` pipeline stages. Auto-detects platform or accepts an explicit argument.

```
/kiro:ci-scaffold             # auto-detect platform from existing config
/kiro:ci-scaffold github      # generate GitHub Actions workflow
/kiro:ci-scaffold gitlab      # generate GitLab CI config
/kiro:ci-scaffold azure       # generate Azure Pipelines config
```

The generated pipeline enforces: build, type-check, lint (with zero-warning tolerance), tests, and debug artifact audit.

### `/kiro:harness-validate` — Check harness structural integrity
Validates command→agent references, template existence, memory caps, L0 headers, and generates a component relationship index. Also includes:
- **Settings JSON check** (part of Step 3) — runs `scripts/setup/check-settings-json.sh` over the settings templates and the live `.claude/settings.json`. Non-zero exit is a blocker: Claude Code silently drops every permission rule and hook in a malformed settings file, and a broken template propagates that to every project installed from it. Notes belong in `settings.notes.md`, not in the JSON
- **Step 8: Instruction architecture audit** — entry file line count vs. 50–200 target, hard constraint count vs. 15 max, topic document adoption, hard-constraint phrases after line 50 (lost-in-middle risk)
- **Step 9: Feature list primitive audit** — triple structure compliance (behavior+verification+state), WIP=1 discipline, pass-state gating evidence

```
/kiro:harness-validate
```
Run after `update.sh` or when something feels broken.

### `/kiro:harness-test` — Smoke-test and regression-test prompts
Runs key workflows at Haiku tier to expose vague instructions. Failures indicate prompt quality issues, not model issues.

```
/kiro:harness-test                        # smoke-test the standard suite
/kiro:harness-test steering               # smoke-test a specific agent
/kiro:harness-test regression             # run scenario-based regression tests
/kiro:harness-test regression steering    # regression test a specific agent
```

**Smoke mode** (default): Runs agents at Haiku tier and checks for structural correctness. Use after editing any agent or rule file.

**Regression mode**: Runs scenarios from `.claude/memory/meta/prompt-scenarios.md`, scores alignment against expected outcomes, and flags regressions. Use after approving instruction library changes from `/kiro:evolve`.

See `docs/prompt-improvement/README.md` for the full prompt optimization workflow.

### `/kiro:harness-fix` — Fix a specific agent mistake
When you observe the agent making a repeatable behavioral mistake, this command encodes a targeted prevention rule so it never happens again. Lighter than `/kiro:evolve` — fixes one thing immediately.

```
/kiro:harness-fix "agent keeps creating new utility files instead of reusing existing ones"
/kiro:harness-fix "agent runs the full test suite instead of targeted tests"
```
The rule is added to the appropriate agent file or rule file and distributed via `update.sh`.

---

## Daily Maintenance (Automated)

### `/kiro:daily-maintenance` — Nightly orchestrator

Runs the full maintenance cycle end-to-end: **Judge → Reflect → Housekeeping → Session Quality → Keep Rate → Trust Score → Augment Skills → Adversarial Check → Eval Staleness**. Designed to run on a nightly schedule (18:00 local) with a SessionStart hook as catch-up. The scheduler is registered automatically by `install.sh` / `update.sh`: Windows Task Scheduler on WSL (`setup-global-orchestrator.sh`) fires at 18:00 and repeats every 4h for the rest of the day (6x/day total — each sub-routine self-gates on its own last-run state, so 5 of 6 fires are cheap no-ops), cron on Linux (`setup-linux-orchestrator.sh`), and launchd on macOS (`setup-mac-orchestrator.sh`) still fire once daily.

Before it visits any repo, `daily-orchestrator.sh` runs one harness-level task: once per calendar day it executes `update.sh` so every registered project picks up harness changes with no human step. Nothing else ever did — `stop-hook.sh` only prints a `Run: update.sh` nudge and waits — so a harness fix could sit unapplied in an installed project indefinitely. It runs `bash -n update.sh` first so a half-written `update.sh` is never run across the fleet, and it writes its state file only on success, so a failed sync retries tomorrow rather than being skipped. Opt out with `SDD_SKIP_HARNESS_SYNC=1`.

After the repo loop (full-fleet runs only), it runs `scripts/utils/check-fleet-registration.sh` to find repos that carry a harness install but are missing from `projects.txt` — those receive zero routines and appear on no dashboard, so their absence is invisible unless something looks for it. Findings are logged to `logs/orchestrator.log` / `logs/orchestrator-errors.log`; it is a report, never a gate.

Each setup script also **preflights** that the orchestrator can actually run under its scheduler, instead of trusting that registration succeeded — macOS via a throwaway probe LaunchAgent and Linux via an approximated cron environment, both fatal on failure; WSL/Windows warns only. A registered-but-unrunnable job (e.g. launchd refused by macOS TCC when the harness sits under `~/Documents`) is otherwise indistinguishable from a healthy one. See `docs/scheduled-tasks/README.md` → "Preflight — registration is not execution".

```
/kiro:daily-maintenance
```

Pipeline:

1. **`session-judge`, run 3×** — independent adversarial scorer. Reads the last 24h of `observations.md` + trace log, applies the rubric in `kiro/settings/rules/session-quality-rubric.md`, emits a JSON verdict (±1 charges, -2 drains, ±4.5%/day cap). **Proposes no fixes** — if the same agent scored and improved, it would optimize for score, not work.
   The judge is an LLM at temperature > 0 and its verdict lands in a cumulative score nothing ever revisits, so a single draw is not a measurement — until this ran three times, "the score fell 4 points" and "the judge sampled differently" were the same observation. It is spawned **three times in a single message** (the runs are independent and read-only, so they run concurrently), with no run told about the others and no variation between prompts. Only the *caller* appends the `[judge]` observation, one line covering all three — three subagents each appending their own would triple-count every tag into `trust_score.py auto-score`, which counts tag occurrences. A run that fails is **missing**, not a vote for zero; substituting `0` for a crashed judge pulls the median toward no-change and manufactures agreement out of a failure.
2. **`/kiro:reflect`** — consumes the Judge's drains as priority signals, converts them into new memory entries or pattern promotions.
3. **`/kiro:housekeeping`** — prunes/archives observations, enforces memory caps.
4. **Memory-gap alert** — if any `[memory-gap]` observations remain unresolved after reflection, appends a `[routine-alert]` observation so the user sees it next session.
5. **Session quality** — scores the session via the `session-quality` rubric, writes a `[session-quality]` observation.
6. **Keep rate** — `keep-rate` skill evaluates pattern retention, writes a `[keep-rate]` observation.
7. **Trust Score update** — `scripts/session/trust_score.py` runs after session quality and keep rate are written so all signals (`[session-charge]`, `[memory-gap]`, `[session-quality]`, `[keep-rate]`) are visible. Rewrites the `## Harness Trust Score:` line in `hot-memory.md`, appends to `.claude/memory/trust-score.jsonl`. `apply` takes **one `--delta` per judge run** and reconciles them: it applies the **median**, so a single outlier draw cannot move the score, and records the day as **inconclusive** with a delta of `0.0` when the samples spread by more than `JUDGE_SPREAD_LIMIT` (2.0 on the ±4.5 scale) — a reading that depends on which draw you looked at is not a reading. The raw `samples`, the `spread` and the `inconclusive` flag are persisted to `trust-score.jsonl`, so an inconclusive day is distinguishable from a day nothing ran. A single `--delta` is still accepted and skips the spread gate, reporting `"spread": null` rather than `0.0` — one sample has no spread, and claiming otherwise would read as perfect agreement. An inconclusive result is not a problem to fix; it is the helper declining to commit a number it cannot stand behind.
8. **Skill augmentation** — `skill-augment-agent` reviews today's observations and judge drains, encodes up to 5 evidence-backed improvements (circuit breaker cap) into relevant `SKILL.md` files (append-only, ≤150 chars each). Logs each change as a `[skill-update]` observation. Also processes any `[seed-target:]` observations written by the action-capture hook during the session, and today's `type: feedback` memories (user corrections), which auto-qualify and are drafted before judge drains — human ground-truth outranks the LLM grader.
8b. **Behavior spec mining** — `behavior-spec-agent` (process-focused sibling of step 8) reviews the same evidence for *recurring agent conduct* rather than skill-content gaps, and drafts/revises up to 3 durable `BEHAVIOR.md` specs under `.claude/behaviors/<name>/` — answer-key material for grading future trajectories, deliberately never shown to the agent being graded (unlike `SKILL.md`/`CLAUDE.md`). Requires ≥2 recurring occurrences of the same conduct class, except `type: feedback` memories which auto-qualify at 1. Every spec is validated with `scripts/validate-behavior-spec.py` before being left in place. Logs each change as a `[behavior-update]` observation. See the `writing-behavior-specs` skill for the format and calibration methodology.
9. **Adversarial check** — a separate verification agent (no loyalty to step 8's output) reviews each `[skill-update]` written today: does it address the stated gap? does it contradict existing guidance? Flags failures as `[skill-update-flagged]`, confirms passes as `[skill-update-verified]`. Skipped if step 8 wrote nothing.
10. **Skill eval staleness** — `scripts/skill-eval-staleness.py` checks every `eval-verdict.json` against the model now running. A verdict is a joint fact about a skill's instructions and the model that read them; `skill-validate-hook.sh` catches the first half on write, but a model change invalidates every verdict at once with no write to fire on, so it needs a scan on the tick. Flags `stale-model`, `unknown-model` and `hash-mismatch`, appends one `[routine-alert]`, and **reports only** — re-measuring one skill costs 12 agent spawns, so an unattended fleet-wide re-run on a model-change day would be the most expensive thing the routine has ever done. The model ID is passed explicitly with no default and no auto-detection: a wrong guess marks every stale verdict as current, which is the failure the step exists to prevent. Skills with no verdict file are counted but never flagged.

Idempotent per calendar day (uses today's `[judge]` observation as the sentinel). Each step is error-isolated: a bad Judge pass does not block housekeeping.

### Trust Score — observability only

The `## Harness Trust Score:` line at the top of `hot-memory.md` (e.g. `42.3% (▲ +0.8 today, 7d: ▼ -1.1)`) is a single-user health signal. **It never gates harness behavior** — spec phase gates still require explicit human approval regardless of score. Adapted from @nityeshaga's "trust battery" design (April 2026) but scoped down: one battery per project (single developer), informational only, no autonomy tiers.

Starts at 20% on fresh install. Daily cap ±4.5%. History lives in `.claude/memory/trust-score.jsonl` (one record per nightly run). The `auto-score` command incorporates a **session success ratio**: uncorrected sessions (no `session-quality ≤ 2/5` or `memory-gap` on that day) act as a multiplier on existing signals and contribute a ±1.0 baseline — so uneventful sessions now passively charge the battery rather than contributing zero.

### Opt out

```
SDD_SKIP_ROUTINE=1 $SDD_HARNESS/install.sh /path/to/project
```

Or after install: `schtasks.exe /Delete /TN "SDD Daily Orchestrator"` (global) or `rm .claude/scripts/orchestration/daily-runner.sh` (per-repo). To keep daily maintenance but stop the once-a-day fleet-wide `update.sh` sync, set `SDD_SKIP_HARNESS_SYNC=1` instead.

### `scripts/session/detect_reexplanation.py` — session signal detector

Runs from `stop-hook.sh` after each session. Uses Claude Haiku to analyse user turns for two signal types:

- **Drain signals** — user had to re-explain context the AI should have saved (explicit: "I already told you"; implicit: "you're still doing that thing I asked you to stop"). Each drain → `[memory-gap]` observation. The Judge treats these as flagship drains.
- **Charge signals** — user gave unambiguous approval ("that's perfect", "exactly what I needed", "great work"). Each charge → `[session-charge]` observation. The rubric auto-scores these as +1 each.

Both types are written at most once per calendar day. The auto-scoring table in `kiro/settings/rules/session-quality-rubric.md` applies these mechanically — no Judge pass needed.

When drain signals are found, `scripts/session/micro_reflect.py` can be called to extract a durable, generalizable fact from each drain and append it to `hot-memory.md` under an `## Auto-learned` section, tagged `[auto-learn, YYYY-MM-DD]`. These are probationary entries — the housekeeping agent promotes them to `meta/patterns.md` after 7 days if reinforced, or removes them if not.

### Full reference: [`docs/trust-battery/`](../evaluation/trust-battery/)

Complete documentation of the trust-battery loop — origin, architecture diagram, rubric details, troubleshooting, and explicit non-goals. Start here if you are modifying any of the battery components.

### `/kiro:macro-eval-sweep` — Population-scale agent evaluation
Clusters recurring failure patterns across Raindrop Workshop traces, ranks by impact, backward-traces the suspect step per pattern, writes a dated report, and posts annotations back to Workshop. The **macro** layer above per-run grading.

```
/kiro:macro-eval-sweep              # last 4 days, all runs
/kiro:macro-eval-sweep 7            # last 7 days
/kiro:macro-eval-sweep 4 zora       # last 4 days, runs matching "zora"
```

Runs automatically twice weekly via `scripts/routines/macro-eval-runner.sh` (MIN_GAP_DAYS=3) inside the daily orchestrator. In headless or scheduler contexts, preflight confirms the Raindrop MCP server is reachable — fails loudly with a `*-SKIPPED.md` report rather than pretending success.

Output: `.claude/reports/macro-evals/YYYY-MM-DD.md` with a pattern leaderboard, top-3 diagnoses (focus event + suspect step), and a delta vs. previous sweep. Span-level and run-level Workshop annotations are posted for confirmed recurring failure patterns (cap: ~5 runs per pattern).

Skill: `evaluation/macro` (part of the `evaluation` skill family). Opt-out: `SDD_SKIP_MACRO_EVAL=1`.

---

### `/kiro:tool-failure-review` — Learn from failing tool calls
Reviews the per-repo tool-failure ledger and promotes recurring failures into memory: diagnoses *why* a command shape keeps failing and writes the cause + remedy as a durable memory entry (and `ERRORS.md`), so it stops happening. The **review** stage of the tool-failure-memory loop.

```
/kiro:tool-failure-review            # review signatures that failed >= 3x
/kiro:tool-failure-review 5          # only signatures that failed >= 5x
```

The loop runs continuously without you: two hooks capture every failing Bash/MCP call (`tool-failure-capture.sh`, PostToolUseFailure) and warn before a known-failing shape is repeated (`tool-failure-recall.sh`, PreToolUse). The review stage runs automatically ~twice weekly via `scripts/routines/tool-failure-review-runner.sh` (MIN_GAP_DAYS=3) inside the daily orchestrator — it no-ops unless the ledger has a signature that failed ≥3× and is still open, so calling it daily is cheap.

Ledger: `.claude/memory/tool-failures.jsonl` (local, per-repo). Report: `.claude/reports/tool-failures/YYYY-MM-DD.md`. Skill: `tool-failure-memory`. Opt-out: `SDD_SKIP_TOOL_FAILURE_REVIEW=1`. Source: ReMe (agentscope-ai/ReMe).

---

### Daily Security Scan (`security-report-runner.sh`)

Runs automatically every day via the daily orchestrator. Performs a static security scan of recent git changes using the `ai-security-workflow` skill: checks for OWASP patterns (injection sinks, XSS vectors, broken auth), exposed secrets, and unsafe patterns introduced in the last commit window. Writes a dated report to `.claude/reports/security/<date>-security-report.md`.

Visible in the dashboard **Scheduled Tasks** section (row 6) with last-run status, artifact diff, and any findings headline.

Self-paces to daily (`MIN_GAP_DAYS=1`). Applies to every repo. Opt-out: `SDD_SKIP_SECURITY_REPORT=1`.

---

### RTK Net-Effect (`rtk-net-effect-runner.sh`)

Runs automatically every day via the daily orchestrator. Deterministic (no LLM) wrapper around `scripts/utils/rtk-net-effect.py`, which measures RTK's **global** effect from `~/.claude/projects/**/*.jsonl` rather than its local savings alone: exact-match Bash rerun rate and Read reread rate within the same session — the recovery-cost signal RTK's own per-command byte-savings figure cannot see. Writes `.claude/memory/rtk-net-effect.json`, read by the dashboard's RTK layer note (Headroom tab), which now shows both rates alongside its savings figure instead of savings alone.

Self-paces to daily (own state-file guard). Applies to every repo. Opt-out: `SDD_SKIP_RTK_NET_EFFECT=1`. Lookback window: `SDD_RTK_NET_EFFECT_DAYS` (default 30).

---

## Jira Integration

### `/kiro:jira-solve` — Work on a Jira ticket with auto-commenting
Start a session tied to a Jira ticket. When you push code, a comment is automatically posted to the ticket describing what was done, why, and which files changed.

```
/kiro:jira-solve ZORAAI-1234
```

The ticket ID is captured at prompt time and stored in `~/.claude/state/active_jira_ticket`. After `git push`, a comment is posted automatically containing:
- Branch name and commit count
- Approach summary (extracted from `docs/` markdown if present)
- Files changed (from `git diff --name-only`)

The state is single-fire — subsequent pushes in the same session don't double-post.

**Prerequisites**: `~/.env.jira` with `JIRA_URL` and `JIRA_PAT` (or `JIRA_USERNAME` + `JIRA_API_TOKEN`).
See `.claude/docs/harness-documentation/SDD-SETUP-GUIDE.md` → "Jira Integration" for full setup.

---

## AutoResearch (ML Experiments)

### `/kiro:autoresearch-init` — Interactive ML project setup
Asks leading questions about your research goal, data, model, metric, and constraints, then generates `program.md`, `train.py`, and `prepare.py`.

```
/kiro:autoresearch-init
/kiro:autoresearch-init "optimize a small transformer on our Python codebase"
```

### `/kiro:autoresearch` — Run autonomous experiment loop
Reads `program.md`, iterates on `train.py` (~5 min per experiment), keeps improvements, reverts failures.

```
/kiro:autoresearch          # run until stopped
/kiro:autoresearch 10       # run 10 iterations
```

**Prerequisites**: `uv` installed, `program.md` + `train.py` + `prepare.py` in project root. Run `uv run prepare.py` once before starting the loop.

See `docs/research/autoresearch/README.md` for full details.

---

## Skill Extraction (from Repositories)

### `/kiro:skill-extract-scan` — Analyze a repo for extractable skills
Scans a repository, scores candidate modules against the extraction rubric, and produces a reviewable plan.

```
/kiro:skill-extract-scan https://github.com/org/repo
/kiro:skill-extract-scan /path/to/local/repo
```

Output: `.claude/skill-extraction/<repo-name>/plan.md` with ranked candidates, scores, and rationale.

### `/kiro:skill-extract` — Generate SKILL.md files
Generates skills from an approved extraction plan, or runs the full pipeline with `-y`.

```
/kiro:skill-extract .claude/skill-extraction/repo/plan.md
/kiro:skill-extract https://github.com/org/repo -y
```

Output: `~/.claude/skill-library/<name>/SKILL.md` for each extracted skill (Library tier; a row is added to the owning domain master — see `docs/skills/SKILL-HIERARCHY.md`).

Every new skill passes quality gates and a companion check before it is logged to the sources index:
- **Phase 5b — SkillOS Quality Gate**: task relevance, operational validity, content quality, compression (≤5,000 words). Failures block completion.
- **Phase 5c — Identity Alignment Check**: invokes `agent-identity` Mode B — validates description specificity, trigger sharpness, behavioral concreteness, and explicit exclusions. Vague skill identities cause the wrong skill to fire; this gate prevents them from entering the harness.
- **Phase 5d — Verification Companion Check**: asks whether the skill's domain involves manual checks a human would run after Claude's work (visual inspection, sampling output, checking logs). If yes, invokes `verification-skill-authoring` to create a companion `<domain>-verify` skill before proceeding.

See `docs/skills/skill-extraction/README.md` for full details on scoring, workflow, and security.

### `/kiro:gitnexus-setup` — Install and configure GitNexus code intelligence

```
/kiro:gitnexus-setup                    # install, index, configure MCP
/kiro:gitnexus-setup --skip-embeddings  # faster indexing (no vectors)
/kiro:gitnexus-setup --force            # force re-index
```

### `/kiro:gitnexus-explore` — Launch visual code explorer

```
/kiro:gitnexus-explore                  # opens Web UI at localhost:4567
/kiro:gitnexus-explore --port 8080      # custom port
```

Browse symbols, call chains, process flows, and community clusters in a WebGL graph.

### `/kiro:gitnexus-impact` — Query blast radius for current changes

```
/kiro:gitnexus-impact                   # analyze uncommitted changes
/kiro:gitnexus-impact --from HEAD~3     # analyze last 3 commits
```

Maps changed code to affected execution flows with HIGH/MEDIUM/LOW risk classification. Falls back to grep-based tracing if GitNexus is not installed.

See `docs/integrations/gitnexus/README.md` for full details.

---

## Local Dashboard

A browser-based dashboard (`scripts/utils/dashboard.py`, stdlib only) that surfaces harness telemetry for all registered repos.

```bash
python3 $SDD_HARNESS/scripts/utils/dashboard.py
```

Starts a local HTTP server at `http://localhost:4569` and opens the browser automatically. Use `--repo <name|path>` to pre-select a repo, `--no-open` to suppress browser launch, `--port <PORT>` to set a custom port, or `--static` to write a static `.dashboard/index.html` instead.

**Sections:**

| # | Section | What it shows |
|---|---|---|
| 1 | ⚡ Trust Battery | Arc gauge + 30-day bar chart of daily trust deltas |
| 2 | 🕸 GitNexus | Stats strip + embedded visual explorer (localhost:4567) |
| 3 | 🔬 Workshop | Raindrop Workshop trace browser; filter by repo, run eval loop, view agent traces |
| 4 | 🗜 Headroom | Compression savings totals for RTK + lean-ctx + Caveman (response-style, sampled once/day via `caveman-savings-hook.sh`) + headroom proxy, folded into a combined-savings total; per-session block history with checkpoint-level token savings |
| 5 | 🪝 Hooks History | Hook name, event type, last activity, active/inactive badge |
| 6 | 📅 Scheduled Tasks | OS scheduler health card — plus, when the scheduler's last launch failed, a full-width red banner **above** the routine cards naming TCC explicitly on exit 126, since a dead scheduler invalidates every calm `PENDING` badge below it — + per-routine cards (schedule, last run + exit code, artifact, diff vs. previous run, reasoning excerpt), scoped to whichever repo's dashboard is open — per-repo routines (including Tool-Failure Review and Code-Review Learning) show that repo's own state and log entries, harness-only routines always show the harness's. Includes the Daily Security Scan routine (`security-report-runner.sh`) which scans recent git changes for OWASP patterns, secrets, and injection sinks. |
| 7 | 🧠 Memory Changes | Per-file cards for hot-memory, observations, and meta/patterns with day-over-day diffs ("since yesterday") computed from dated snapshots; full content expanded when a file is unchanged |
| 8 | 🎯 Skill Changes | Skill usage stats (hot/cold from `skill-usage-tracker.sh` log — total/30d invocations, skills used, cold-skill count, top-skills bars, deprecate candidates) above the rendered skill-curation-report with audit age; in companion mode, adds "🔍 Analyze & Propose" and "✅ Apply Approved" buttons — propose spawns a headless `claude --print` session that writes a terse numbered proposal to `.claude/memory/.skill-curator-proposal.md` (`/api/skill-curator-propose`); apply backs up `~/.claude/skills/` to `.dashboard/skill-backups/`, then spawns a headless session to execute the approved subset and log it to `reports/skill-curation-report.md` (`/api/skill-curator-apply?instruction=...`) |
| 9 | 📊 Session Quality | Score/keep-rate/memory-gap summary + 30-day chart; ✨ **Prompt Quality** sub-tab — per-dimension PQ trends (7-day avg, weakest dimension, rolling score chart); AI-adoption stat card (latest reading, a volume signal distinct from Keep Rate's durability signal) |
| 10 | 💰 Model Cost | All-time and 30-day spend; 90-day daily cost bar chart; sessions table with model/tokens/cost; cross-provider "What if?" cost switcher; cache-cost stat card showing what share of session spend is cache reads/writes vs. fresh tokens |
| 11 | 🧵 Context Health | Sessions per day trend + `/compact` recommendations; live context-usage card (color-coded %, from the open Claude Code session's statusline via `hooks/global/caveman-statusline.sh`, shown only while a session is open in the last 15 minutes) |
| 12 | 🔧 Maintenance Status | Per-repo orchestrator log tail and last-run status; **deferred-work banner** — count of `DEBT:` markers (deliberate shortcuts, per `karpathy-guidelines`) found by `git grep` across tracked code, recomputed each dashboard launch |
| 13 | 🤖 Automation Audit | Timeline of automated events — runs from every routine (daily-maintenance, macro-eval, skill-curator, harness-health, tool-failure, security, drift), each with its own icon/label; not-due checks (duration 0s) are hidden and daily-maintenance entries expand to show that day's brief; plus trust-judge scores, session signals, scheduled task outcomes, and a PR-review-pipeline event section (`detect_base_and_create.sh` → `log_review.sh` → `validate_review_json.py` → GitHub Action publish) |
| 14 | 🐑 Herder | Spawn form + live agent roster, backed by `scripts/utils/herder.py` (Herdr). Starts **real interactive** Claude Code sessions that outlive the dashboard process, in whichever repo the dashboard's own repo dropdown is set to — there is deliberately no second repo picker. Each card is a **chat**: an always-visible message feed above a reply box (Enter sends, Shift+Enter newlines) with `@file` tag chips, plus per-agent lifecycle state (`idle`/`working`/`blocked`/`done`), a live tail on a 5s poll, and per-agent token/cost-weighted spend read from that session's own transcript (reported as **None**, not 0, when the transcript cannot be located). Permission modes and model ids are discovered rather than hardcoded, and each list states plainly when it is a fallback. Under `--static` the controls are replaced by a note instead of rendered dead |

### 💰 Model Cost section

Reads session JSONL files from `~/.claude/projects/*/`. Pricing is fetched from `models.dev/api.json` and cached at `.dashboard/models-pricing-history.json`, refreshed bi-weekly. Historical snapshots accumulate so past sessions are costed at the rate in effect when they ran. Sessions where pricing has changed since the run are flagged with a ⚠ icon.

The **"What if?" switcher** lets you recalculate total projected cost against any supported provider (Anthropic, OpenAI, Google, Mistral, DeepSeek, xAI, Cohere, Amazon Bedrock, Azure, Perplexity, Groq) and model — select provider first, then model, and the projected vs. actual totals update instantly.

The **cache-cost stat card** shows what share of a session's token spend was cache reads/writes vs. fresh tokens — the same signal `stop-hook.sh`'s cache-cost dominance check uses to decide whether to write a handoff snapshot.

### 🐑 Herder section

Backed by [Herdr](https://herdr.dev) via `scripts/utils/herder.py`. `herdr server` is a headless daemon needing no TTY, and every `herdr workspace|tab|pane|agent` subcommand answers with JSON on stdout, so nothing in this path pattern-matches terminal text. It is used instead of the `claude --print` primitive the skill-curator endpoints use because `--print` is a one-shot pipe with nothing to attach to; a Herdr-started session can be joined from a terminal mid-run with `herdr agent attach <name>`.

The API endpoints (`/api/herder-status`, `-list`, `-options`, `-spawn`, `-prompt`, `-read`, `-stream`, `-stop`, `-complete`) are guarded by **two independent checks**, either of which alone is bypassable: a per-process `X-Herder-Token` that only the page served by this process holds, and an `Origin` allowlist. An *absent* Origin is allowed (browsers send none on same-origin GET/POST) while a *present but foreign* one is rejected, so a page that somehow learned the token still cannot drive the dashboard from another site.

**Talking to an agent (chat, not a popup).** Each agent card holds the conversation inline: a scrolling message feed, a reply `textarea` (Enter sends, Shift+Enter inserts a newline), a 📎 button that attaches `@filename` chips, and `send`. Attached files are appended to the outgoing prompt as a trailing `Files: @a @b` line, then the chips clear. The feed auto-scrolls only when you were already at the bottom, so reading back through history is not yanked forward by the next message. It renders assistant **text** events from `/api/herder-stream`; reasoning and tool-call events are not shown in the chat. Sending posts to `/api/herder-prompt` refreshes the feed immediately and again after 1s. With the **live tail** checkbox on, every card's feed refreshes on the 5s roster poll, not just an opened one.

Agent text is rendered as **markdown** — headings, lists, fenced code, inline `code`, tables — because agents answer in markdown and escaped plain text read as one run-on paragraph. Parsing is line- and character-scanning only (no regex, per the repo-wide ban), and every piece of agent text still passes through `_hdEsc` before it reaches `innerHTML`. Chat text is clipped at 20,000 characters (`CHAT_TEXT_LIMIT`) rather than folded onto one line, so a long answer keeps its list and fence structure.

**Prompt-box completion.** Both the spawn prompt and each reply box complete the way Claude Code's own input does: `/` at the start completes a skill or command, `@` at the start of any word completes a repo path. Candidates come from `/api/herder-complete` → `herder.complete()`, which reads the target repo off disk (project `.claude/` → user `~/.claude/` → plugins, namespaced `plugin:name`; nested commands as `/kiro:spec-init`) and lists files via `git ls-files --cached --others --exclude-standard`, so gitignored paths are never offered. Results are cached 30s per repo, ranked like a fuzzy finder with shallower-then-shorter tie-breaking, and capped at 50.

The raw pane sits below the chat in a collapsed `raw pane` disclosure. Opening it populates the `<pre>` with the full raw session output via `herderUpdateChat` (refreshes on disclosure open and on live-tail 5s polls).

Two undocumented Herdr behaviours are handled here. A spawned pane inherits `CLAUDE_CODE_CHILD_SESSION`, which turns transcript saving **off** — that would make every herder-spawned session invisible to `scripts/utils/token-forensics.py` and to `agents/kiro/session-judge.md`, so it is scrubbed both in the server env and per-workspace via `--env`. And `herdr agent read` returns raw pane text rather than a JSON envelope, with failures arriving as JSON on stderr, so both are handled separately from the normal JSON path.

Known limitation: spawning into a repo Claude Code has never been opened in fails with `agent_not_ready`, because Claude stops on an interactive startup dialog — the first-run "do you trust the files in this folder?" prompt, or "New MCP server found" when the repo's `.mcp.json` registers a server that has not been approved yet (seen 2026-10-01 right after GitNexus setup wrote one). The pane is read **before** the failed workspace is closed, so the error names the actual dialog and the card shows the captured prompt text in a red block instead of a bare `agent_not_ready`. Open that repo by hand once with `claude` and answer it.

See `docs/workflow/superpowers/specs/2026-05-14-harness-dashboard-design.md` for the full section spec (gitignored — local only).

---

## Raindrop Workshop (Automatic Agent Tracing)

All registered repos emit traces automatically whenever agents run. No commands needed — just open the dashboard Workshop tab.

### Dashboard Workshop tab

```bash
python3 $SDD_HARNESS/scripts/utils/dashboard.py
# → Workshop tab in the sidebar
```

| Action | How |
|---|---|
| Start Workshop | Click **Start raindrop workshop** button (or run `raindrop workshop` in terminal) |
| View traces | Workshop UI loads at `/workshop/` in the dashboard |
| Filter by repo | Use the `event=` label in Workshop sidebar (e.g. `aiq-zora-ai-engine`) |
| Run eval loop | Click **Run Eval Loop** — costs ~5k–30k tokens, always manual |

### What fires automatically

Traces emit whenever an instrumented agent processes a request:

| Repo | Trigger |
|---|---|
| `aiq-zora-ai-engine` | Any call to `AgentPipelineGraph.process()` |
| `aiq-zora-agent-skills` | Any call to `DailyNewsHandler.handle()` |
| `aiq-purina-salesorderintelligence-poc` | Any `/chat` request via `query_portal.py` |

### Self-Healing Eval Loop

Triggered manually from the dashboard. Claude reads Workshop traces, writes `pytest` assertions from them, runs the tests, and auto-fixes failures (max 3 cycles). Budget ~5k–30k tokens.

Skill: `~/.claude/skill-library/raindrop-eval-loop/SKILL.md`

### Instrumenting a new repo

```bash
/raindrop-instrument-agent
```

Or register the repo with the harness and `install.sh` handles it automatically.

See `docs/raindrop/README.md` for full details and troubleshooting.

---

## Frontend Design Quality (Impeccable)

### `/impeccable-audit` — Visual design audit for UI components
Audits frontend code across 7 domains: typography, color & contrast, spatial design, motion, interaction, responsive, and UX writing. Applies Impeccable's 27 deterministic anti-pattern rules + 12 LLM critique rules. Returns a PASS / NEEDS WORK / BLOCK verdict.

```
/impeccable-audit                            # full audit of current component
/impeccable-audit UserCard                   # audit a named component
/impeccable-audit focus: motion              # deep-dive on motion/animation only
/impeccable-audit focus: accessibility       # accessibility + interaction states
```

Use before committing UI work, or when a component "looks AI-generated."

### Auto-scan hook (PostToolUse)
If `impeccable` is installed globally, every Write/Edit to a frontend file (`.tsx`, `.jsx`, `.css`, `.vue`, `.svelte`, `.html`) is automatically scanned and violations are surfaced inline. No extra commands needed.

```bash
# One-time setup (pinned — same version install.sh installs)
npm install -g impeccable@3.6.0
```

### Key anti-patterns flagged

| Pattern | Code signature |
|---|---|
| Gradient text | `background-clip: text` |
| Glassmorphism | `backdrop-filter: blur()` |
| Colored left border | `border-left: 4px solid var(--accent)` |
| Pure white background | `background: #ffffff` |
| Identical card grid | 3-col, same height, same padding |
| Stale easing | `ease-in`, `ease-out` |
| Missing focus state | No `:focus-visible` styles |

See `docs/design/impeccable/impeccable.md` for the full rule set.

---

## Proof Collaborative Review (Spec Phase Gates)

Proof is the built-in review layer used by `spec-requirements`, `spec-design`, and `spec-tasks`. After each phase generates an artifact, the harness publishes it to a live Proof document and presents a browser URL for inline review.

### How it works

1. Phase subagent generates the artifact (requirements / design / task list)
2. `proof-collaborative-review` skill starts a local Proof server (port 4000) and publishes the document
3. A review URL is presented — open it in any browser to annotate, suggest edits, or rewrite inline
4. Come back to Claude and say "done" when finished
5. Skill retrieves the final human-edited version and writes it back to `specs/<feature>/`

### First-time setup (automatic)

No manual steps. The skill auto-installs the Proof SDK into `~/.claude/tools/proof-sdk/` on first use (requires Node.js). Subsequent runs skip the install.

### Remote server

Set `PROOF_SERVER_URL=http://your-host:4000` to point at a shared instance instead of localhost.

### Skill location

```
$SDD_HARNESS/skills/proof-collaborative-review/SKILL.md
```

Bundled in the harness — replicated to every machine via `install.sh`. No per-machine manual copy needed.

---

## Typical Workflow

```
1. /kiro:steering                        ← once per project (or after big changes)
2. /kiro:idea-refine "rough idea"        ← (optional) refine vague ideas
3. /kiro:spec-quick "Add feature X"      ← fast: requirements → design → tasks
   (review and approve each phase)
4. /kiro:spec-impl feature-x             ← implement via TDD (audits choices per pass)
5. /kiro:verify                          ← confirm build, tests, lint all pass
6. /kiro:validate-impl feature-x         ← confirm spec alignment
7. /kiro:audit-choices feature-x --close ← resolve open needs-user calls before shipping
8. /kiro:ship feature-x                  ← (optional) launch readiness check
9. /kiro:reflect                         ← capture what you learned
```

For larger features, use the individual spec phases (`spec-requirements` → `spec-design` → `spec-grill` → `spec-tasks`) instead of `spec-quick` to review each phase separately.

For a whole ambitious project spanning many not-yet-scoped features, `/kiro:idea-refine` charts a program map (`specs/_maps/<name>.md`) and decomposes it slice-by-slice — see the `idea-refine` entry above.

### Quality Gate Sequence (pre-completion)

```
/kiro:verify                             ← Gate 1: does it build and pass?
/kiro:validate-impl feature-x            ← Gate 2: does it match the spec?
/kiro:validate-adversarial feature-x     ← Gate 3: can we poke holes? (optional)
/kiro:validate-perf feature-x            ← Gate 4: will it perform? (optional)
                                         ← Gate 5: production readiness (auto-triggered after spec-impl)
/kiro:ship feature-x                     ← Gate 6: rollout plan & decision thresholds (optional)
```

### Long Session Management

```
/kiro:checkpoint save task-1-done        ← save progress after each task
/kiro:save-session                       ← save full session state before leaving
/kiro:resume-session                     ← pick up where you left off
/kiro:context-budget                     ← check if context is getting bloated
```

### Prompt Improvement (When Agents Misbehave)

Use this when agents consistently produce poor output — wrong conclusions, bad format, missing context, or misunderstanding tasks.

```
/kiro:evolve                             ← analyze alignment scores, diagnose underperformers
                                           (review and approve instruction proposals)
/kiro:harness-test regression            ← verify changes don't regress other scenarios
/kiro:harness-test regression steering   ← test a specific agent after changes
```

This applies to all prompt layers — not just sub-agents:
- **Agent prompt is wrong** → evolve diagnoses it automatically
- **Command passes bad context** → review the command's prompt block
- **Rule is too vague** → sharpen it or add an instruction library bullet
- **Your feature descriptions are misunderstood** → capture effective patterns in the instruction library

See `docs/prompt-improvement/README.md` for the full guide.

---

## GBrain Patterns (Automatic Agent Protocols)

Four protocols extracted from [garrytan/gbrain](https://github.com/garrytan/gbrain) fire automatically via hooks — no commands needed.

### What fires automatically

| Trigger | Hook | Protocol injected |
|---|---|---|
| Every `Agent()` call | `gbrain-agent-spawn.sh` | Model tier selection + background routing + memory-first brief |
| Every `save_observation` | `gbrain-memory-write.sh` | Compiled-truth two-zone structure + source citation format |
| Every `WebFetch` / `WebSearch` | `gbrain-external-search.sh` | Reminder to search claude-mem before external calls |

### The four patterns

**Memory-First Lookup** (`~/.claude/skill-library/memory-first-lookup/`) — Always run `mcp__plugin_claude-mem_mcp-search__search` before reaching for external APIs. The lookup chain: keyword search → semantic search → get_observations → external only if memory is empty.

**Model Tiers** (`~/.claude/skill-library/model-tiers/`) — Match model to task type:
- `haiku-4-5` (`claude-haiku-4-5-20251001`) for classification, validation, dedup (utility)
- `sonnet-5` (`claude-sonnet-5`) for generation, synthesis, agent work (default)
- `opus-5` (`claude-opus-5`) only for deep multi-step reasoning (upgrade when sonnet consistently fails)
- `fable-5` for long, multi-sitting autonomous sessions (`/model fable`)
- Subagents always use `sonnet`, not opus — latency compounds in tool loops

**Background Work Routing** (`~/.claude/skill-library/background-work-routing/`) — Stay inline unless a pain signal fires: gateway restart, state drop, parallel > 3, runtime > 5 min, or user frustration. Offer the switch explicitly; never switch silently.

**Compiled Truth Pattern** (`~/.claude/skill-library/compiled-truth-pattern/`) — Every memory observation has two zones: `## State` (rewrite in place when evidence changes, each fact cited) and `## Evidence / Timeline` (append-only dated log, never edited).

Full reference: `docs/gbrain-patterns/gbrain-patterns.md`

_Last synced: 2026-10-01_
