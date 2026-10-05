# Articles

Online articles, blog posts, and documentation pages that were passed to `/skill-extraction` and turned into harness skills. Ordered by date added.

---

## Cursor: Continually Improving with AI Feedback Loops
**URL:** https://cursor.com/blog/continually-improving | **Added:** 2026-05-07 | **Source:** Cursor Engineering Blog

**What it's about:** How Cursor instruments its AI coding agent to continuously improve through behavioral governance — specifically, how memory contamination by case-specific facts (rather than reusable workflow patterns) is the #1 silent failure mode in long-running agents per the OpenAI cookbook. Covers compaction boundary timing, memory write discipline, and the distinction between investigation artifacts (ephemeral) and workflow patterns (persistent).

**What we added:**
- Hook: `hooks/claude/memory-discipline-hook.sh` (PreToolUse, gate on `*/memory/*.md` and `MEMORY.md` writes) — displays discipline rules before any memory write executes, enabling self-correction of violations (case-specific facts, pronouns, duplicated content)
- Hook: `hooks/claude/compaction-discipline-hook.sh` (PreCompact) — injects boundary-timing principles before every compaction: compact at workflow phase boundaries, preserve artifact paths/decisions/open questions, merge not regenerate (regeneration compounds LLM sampling drift)
- Skill: `agent-memory-discipline` — canonical reference for memory governance; 3-tier type system, body structure rules, what to save vs. skip
- Skill enhancement: `context-compression` — new "Compaction Modes and Boundary Timing" section (3-mode taxonomy: routine, phase-boundary, crisis)

---

## Overloaded Context: Why Agent Memory Fails at Scale
**URL:** https://www.dbreunig.com/2026/05/10/overloaded-context | **Added:** 2026-05-12 | **Source:** Drew Breunig (dbreunig.com)

**What it's about:** Analysis of why agents reach for external search too eagerly — burning tokens and introducing latency — when the answer already exists in session memory or the memory-first lookup chain. Argues for a retrieval priority hierarchy: local memory → external search, not the reverse.

**What we added:**
- Design context for `hooks/claude/gbrain-external-search.sh` — the hook's memory-first lookup chain (search → get_observations → timeline before any WebFetch/WebSearch) directly implements this article's retrieval priority hierarchy. The article was the reasoning input for that hook's design alongside the garrytan/gbrain patterns.

---

## Claude Code `/goal` — Goal-Driven Autonomous Execution
**URL:** https://code.claude.com/docs/en/goal | **Added:** 2026-05-14 | **Source:** Anthropic / Claude Code documentation

**What it's about:** Documents the `/goal` primitive in Claude Code — a completion evaluator (Haiku) that runs after each turn, checks whether the stated goal has been met, and either continues autonomously or stops when done. Enables running any workflow without permission stops until a verifiable condition is met.

**What we added:**
- Skill: `goal-mode` — patterns for running any feature development workflow in autonomous mode (goal-driven, runs until completion) vs interactive mode (permission steps, debuggable). Fills gap in the existing 53 development workflow skills which had no goal-driven autonomous execution pattern.

---

## Continuous Evaluation for Skill Extraction Workflows
**URL:** https://huggingface.co/blog/continuous_eval | **Added:** 2026-05-18 | **Source:** Hugging Face

**What it's about:** Methodology for continuous evaluation of LLM-powered pipelines — specifically, the principle that deterministic enforcement patterns should be implemented as hooks (run every time, measurable) rather than as skills or prompts (advisory, easily skipped). Introduces 4 evaluation signals for identifying hook candidates.

**What we added:**
- Skill enhancement: `skill-extraction` — Phase 3 now has a mandatory "Hook Candidate Assessment" section that invokes the `hook-design` skill to evaluate each extracted capability against 4 signals before proposing integration type: (1) must run every time, (2) describes enforcement, (3) lifecycle-aware, (4) prompt would fail to enforce. If any signal is positive → Hook entry in the proposal.

---

## How is Linear So Fast? A Technical Breakdown
**URL:** https://performance.dev/how-is-linear-so-fast-a-technical-breakdown | **Added:** 2026-05-27

**What it's about:** Technical breakdown of Linear's engineering decisions behind its notoriously fast UI: a local-first sync engine (IndexedDB as primary store, server as sync target), optimistic updates that commit to local state before network confirmation, `modulepreload` for zero-latency navigation, composited-only CSS animations (`transform`/`opacity` exclusively), property-level MobX observables (not object-level), and keyboard-first interaction design with no modals.

**What we added:**
- Skill: `frontend-performance` — architecture decision matrix (local-first vs server-driven), optimistic update pattern, bundle/load strategy, animation constraint rules (absolute: only `transform`/`opacity`, ≤150ms), reactivity granularity (property-level > object-level), service worker guidance, and keyboard-first interaction checklist.

---

## Better Experiments with LLM Evals: A Funnel, Not a Fork
**URL:** https://engineering.atspotify.com/2026/5/better-experiments-with-llm-evals-a-funnel-not-a-fork | **Added:** 2026-05-27 | **Source:** Spotify Engineering Blog

**What it's about:** Proposes using LLM evals as a *filter before* A/B tests, not as an alternative to them. Key data: only ~12% of A/B tests ship positively, ~42% of launched experiments eventually reverse due to secondary metric regression. Running evals first eliminates clearly non-promising candidates before consuming experiment bandwidth. Covers judge calibration (verifying that eval-preferred variants actually win with users), tiered evidence requirements, and guardrail monitoring.

**What we added:**
- Skill: `llm-eval-funnel` — three-tier testing framework (quick eval sweep → rigorous A/B → post-experiment calibration), when each tier is sufficient, the judge calibration loop, and pre-experiment filtering workflow. Prevents teams from running expensive A/B tests on candidates that fail a basic quality bar.

---

## Managed Agents: Verify with Outcome Grader
**URL:** https://platform.claude.com/cookbook/managed-agents-cma-verify-with-outcome-grader | **Source:** Anthropic Cookbook

**What it's about:** Walkthrough of the Outcomes feature in Claude Managed Agents — a stateless grader agent evaluates a writer agent's output against a rubric and drives revisions until the output passes, without requiring custom orchestration code. Covers the TASK/RUBRIC separation pattern, how to write checkable rubrics for objective criteria (research quality, citation checking, compliance review, structural completeness), and the full grade-and-revise loop.

**What we added:**
- Skill: `cma-outcomes` — when Outcomes fits vs. doesn't (rubric-based verification vs. open-ended creative tasks), the TASK/RUBRIC separation pattern, rubric writing guide, and the full grade-and-revise loop implementation with the `OutcomeGrader` API.

---

## Six Levels of Complexity in a Codex Morning Brief
**URL:** https://jxnl.co | **Added:** 2026-05-27 | **Author:** Jason Liu

**What it's about:** A practical framework for AI workflow design that enforces one-real-capability-at-a-time progression through six complexity levels: (1) simple prompt, (2) structured output, (3) tool use, (4) agentic loop, (5) multi-agent, (6) persistent memory vault. The argument is that most teams over-engineer AI features by jumping to level 5 before proving value at level 3. Each level has a specific qualification gate before advancing.

**What we added:**
- Skill: `progressive-complexity-ladder` — the 6-level framework as an AI feature design gate.
- Integration into `kiro:spec-design` via Principle 10 in `kiro/settings/rules/design-principles.md` — automatically invoked when classifying any AI integration feature.
- Step D (Morning Brief) added to `.claude/scripts/daily-maintenance-prompt.md`.

---

## Macro Evals for Agentic Systems
**URL:** https://developers.openai.com/cookbook/examples/partners/macro_evals_for_agentic_systems/macro_evals_for_agentic_systems | **Added:** 2026-05-31 | **Source:** OpenAI Cookbook

**What it's about:** Methodology for evaluating multi-agent systems at *population scale* rather than grading one run. Compress many traces into comparable documents, discover recurring behavior patterns (BERTopic-style: embed → UMAP → HDBSCAN), rank them by impact (prevalence × severity), then backward-trace high-impact patterns to the workflow step most likely responsible via a decomposable suspect score. Core insight: a correct final answer can hide a broken middle, so cluster the evidence, don't trust the last event. Built on Promptfoo (per-run rubrics) + OpenAI Agents SDK traces.

**What we added:**
- Skill: `macro-evals` — the five-phase macro methodology (collect/normalize → trace-document → cluster discovery → impact leaderboard → backward suspect trace), explainable scoring formulas, and do's/don'ts. Sits as the *macro* layer above the micro `evaluation`/`cma-outcomes` skills (cross-linked both ways).
- Routine: `/macro-eval-sweep` command + `macro-eval-runner.sh` + `macro-eval-prompt.md` — twice-weekly local sweep over Raindrop Workshop traces (`query_traces` → group → impact-rank → suspect-trace), writes a dated report to `.claude/reports/macro-evals/` and posts `issue`/`note` annotations back onto offending runs/spans in Workshop. Preflight fails loud (writes `*-SKIPPED.md`) if the Raindrop MCP server is unreachable in a headless context. Scheduler registration left to the user (launchd/cron, Mon & Thu).

---

## Exploring Agent-Assisted Qualitative Analysis
**URL:** https://www.sh-reya.com/blog/ai-qual-analysis/ | **Added:** 2026-05-31 | **Source:** Shreya Shankar (sh-reya.com)

**What it's about:** Shankar points AI agents at grounded-theory qualitative analysis (open → axial → selective coding) over 451 tweets and reports where they break. The valuable part is her empirically-measured failure catalog: agents paraphrase instead of analyzing (93.8–100% of codes used exactly once), cover only 6–68% of the corpus before silently stopping, converge prematurely, overfit/forget human feedback, and produce vague unfalsifiable categories ("Reliability and Trust") that can't be measured or acted on. She flags the direct transfer to agent error analysis on traces.

**What we added** (chose enhance-existing over a new skill — the harness's memory-mining loop already *does* qualitative coding on the observation corpus):
- Enhancement: `learn-eval-agent` — Step 3b hard gates: **falsifiability** (drop vague buckets you can't check adherence to) and **generalizability/anti-paraphrase** (drop one-off restatements of a single event).
- Enhancement: `reflect-agent` — pattern promotion now requires 3+ *distinct* observations, falsifiable, no premature convergence; added a **coverage** report. Canonical rule mirrored in `memory-conventions.md`.
- Process: extended `doc-sync`/`sync-docs` with **Resource & Registry Coverage** — capability additions are now a first-class change bucket that must be documented in the resources (this sources index, `.claude/docs/**`, `README.md`, `SDD-USAGE.md`) per Phase 6. See memory `enhancement-qual-coding-gates`.

---

## Learn Harness Engineering — Lectures 03–05, 07–08, 12
**URL:** https://walkinglabs.github.io/learn-harness-engineering | **Added:** 2026-05-31 | **Source:** Walking Labs (walkinglabs.github.io)

**What it's about:** Practical harness engineering course covering the lifecycle of an agentic instruction file — from initial design to ongoing maintenance. Key lectures: (03–04) lean entry-file + topic-document architecture, "lost in the middle" LLM attention research, SNR auditing, instruction maintenance metadata (source/applicability/expiry), anti-patterns and worked refactor example; (07–08) machine-readable feature state machines (not_started/active/blocked/passing), triple structure (behavior+verification+state), WIP=1 finding (5→1 active feature, 20%→100% pass rate on 8-feature REST API), pass-state gating rules, granularity calibration; (05, 12) session-as-database-transaction model, five-dimension clean state checklist, clock-in/clock-out protocols, PROGRESS.md + DECISIONS.md + QUALITY.md structures, entropy management, harness simplification cadence.

**What we added:**
- Skill: `instruction-architecture` — lean entry-file + topic-document architecture, "lost in the middle" countermeasures, SNR audit procedure, instruction maintenance metadata fields. Enforced by `harness-validate-agent` (Step 8) and `evolve-agent` (Step 1d).
- Skill: `feature-list-primitive` — machine-readable feature state machine, triple structure (behavior+verification+state), WIP=1 discipline with quantified outcomes, pass-state gating, dependent harness components table. Enforced by `harness-validate-agent` (Step 9).
- Skill: `session-clean-state` — five-dimension clean state, clock-out/clock-in protocols, PROGRESS.md/DECISIONS.md/QUALITY.md templates, entropy management, harness simplification cadence. Enforced by `reflect-agent` (Step 6) and `evolve-agent` (Step 1e).
- Agent enhancement: `harness-validate-agent` — Step 8 (instruction architecture audit: line count, constraint count, topic doc adoption, middle-placement check) and Step 9 (feature list primitive audit: triple structure, WIP=1, pass-state).
- Agent enhancement: `evolve-agent` — Step 1d (instruction architecture health check: bloat, SNR, middle placement, monolithic pattern) and Step 1e (session clean state health check: PROGRESS.md freshness, debug artifacts, verify path); added "Harness Architecture Health" scorecard table to output.
- Agent enhancement: `reflect-agent` — Step 6 (session clean state check: five-dimension table with corrective action items) and "Clean State" section in output format.
- Skill enhancement: `agent-harness-design` — Phase 4: Operational Diagnostics (Fresh Session Test, Controlled Ablation Methodology, Affordance Analysis, Harness Rot Detection cadence).

---

## 3 Top Takeaways From Dropbox's Former Most Senior Engineer | James Cowling
**URL:** https://www.developing.dev/p/3-top-takeaways-from-dropboxs-former
**Added:** 2026-05-31
**Source / Author:** Ryan Peterman interview with James Cowling (former Senior Principal Engineer at Dropbox, founder of Convex)

**What it's about:** Interview covering three engineering principles: (1) AI-era skill maintenance — use AI tools without atrophying problem-solving and architectural thinking; (2) system bias / outcome naming — teams named after technologies they own (MySQL team, AWS team) resist necessary implementation changes; rename by problem domain to realign incentives; (3) simplicity as the harder design choice — simple systems cost more upfront design effort but maintain observability and reduce operational burden (Dropbox example: 1,000 MySQL nodes with plain block-ID lookups, trivially queryable).

**What we added:**
- Design Principle augment: `kiro/settings/rules/design-principles.md` Principle 10 **"Problem-Scoped Identity & Observability Gate"** — name agents/commands/scripts by the problem they solve (not the technology), and use immediate state observability as a concrete simplicity criterion. Also added two anti-patterns: mechanism-named components and states invisible without special tooling.

---

## Introducing Dynamic Workflows in Claude Code
**URL:** https://claude.com/blog/introducing-dynamic-workflows-in-claude-code
**Added:** 2026-05-31
**Source / Author:** Anthropic

**What it's about:** Announces Claude Code's `Workflow` tool (research preview, May 2026) — deterministic JavaScript-script-based orchestration that fans out tens-to-hundreds of parallel subagents within a single session. Covers the `ultracode` effort setting (auto-deploys workflows for complex tasks), task shapes that warrant a workflow (service-wide audits, multi-hundred-file migrations, adversarial verification sweeps), token cost warnings (start scoped), first-workflow confirmation protocol, and progress persistence/resume.

**What we added:**
- Skill enhancement: `multi-agent-patterns` v1.3.0 — "Tool Selection: Agent vs. Workflow" section with decision table, task-shape routing tree, ultracode mode note, and token budget guidance. Positions the `Workflow` tool as the scale-escalation path above `Agent`-based dispatch, with explicit cross-link to `superpowers:dispatching-parallel-agents` for the small-fleet path.

---

## Agent Judge: Solving Long-Context Evaluations
**URL:** https://www.judgmentlabs.ai/blogs/agent-judge-solving-long-context-evaluations
**Added:** 2026-06-01
**Source / Author:** JudgmentLabs

**What it's about:** Describes the "Agent Judge" architecture for evaluating long-horizon agents where standard LLM judges fail — specifically when trajectories exceed context limits, actions modify external state (CRM, GitHub, AWS, DB), or evaluation criteria drift as agent behavior evolves. Three core capabilities: Search (slice long trajectories into targeted evidence chunks via worker agents), Verify (cross-check agent claims against external system state rather than trusting agent descriptions), and Adapt (Rubric Builder — closed-loop calibration of rubrics against human labels and production outcomes). Empirical results on trajectory-level hallucination detection: Agent Judge (refined) 0.86 accuracy / 0.79 F1 vs. 0.74 / 0.65 for a standard LLM judge across difficulty deciles.

**What we added:**
- Skill: `evaluation/long-trajectory` — three-phase workflow (Search / Verify / Adapt), evidence-slice strategies, external system verification checklist (API, DB, GitHub, cloud, logs, filesystem), Rubric Builder iteration loop with calibration signals and trigger conditions. Part of the consolidated `evaluation/` skill family.
- Restructure: consolidated `evaluation`, `macro-evals`, and `llm-eval-funnel` into a single `evaluation/` skill family with a router (`evaluation/SKILL.md`) and four sub-skills (`micro`, `macro`, `funnel`, `long-trajectory`). Router provides a decision tree and supports loading multiple sub-skills for cross-layer tasks. Documentation added at `docs/evaluation/README.md`.

---

## Agentic RL: Token-In, Token-Out Done Right
**URL:** https://qgallouedec-tito.hf.space | **Added:** 2026-06-02 | **Source:** Hugging Face (qgallouedec)

**What it's about:** Identifies a silent correctness bug in multi-turn RL training loops for tool-calling LLMs. When conversation history is re-rendered through the chat template at each step, BPE drift produces different token sequences than what the model originally sampled — gradients then target tokens the model never generated. The fix is a single invariant ("never re-encode what you decoded") implemented via a running token buffer. Also covers the prefix-preservation property test (12-line Python), a model compatibility table (18/19 major open-weight models pass unchanged), the Qwen3 one-line Jinja template fix, and edge case handling (history rewriting, truncation).

**What we added:**
- Skill: `agentic-rl-tito` — TITO invariant, correct loop algorithm, prefix-preservation property test with code, model compat table, `compute_tool_delta()` pattern, edge case recipes. Fires only on RL fine-tuning / multi-turn training loop tasks. Full code patterns in `resources/code-patterns.md`.

---

## Running an AI-Native Engineering Organization
**URL:** https://claude.com/blog/running-an-ai-native-engineering-org
**Added:** 2026-06-07
**Source / Author:** Anthropic (Claude Code team)

**What it's about:** Anthropic's Claude Code team describes how they restructured engineering around agentic coding — shifting bottlenecks from writing code to verification, review, and security. Covers four process changes (JIT planning, ask-Claude-first context, AI/human review tiering, blurred team roles), three org principles (dogfood, flat teams, kill obsolete processes), specific adoption metrics (onboarding ramp, PR cycle time, Claude-assisted commit rate), and the "pick your noisiest workflow" prioritization heuristic.

**What we added:**
- Skill: `ai-native-org-patterns` — five-phase framework: process audit ("noisiest workflow" heuristic), JIT planning pattern, AI/human review tiering table, ask-Claude-first context gathering, adoption metrics with targets, and three non-negotiable org principles.
- Skill enhancement: `karpathy-guidelines` — added "Review Tiering" section (Section 5) with explicit AI-owns-mechanical / human-owns-judgment split table and updated "How to Know It's Working" checklist.

---

## Modern Engineering Values
**URL:** https://cpojer.net/posts/modern-engineering-values
**Added:** 2026-06-07
**Source / Author:** Christoph Nakazawa (cpojer) — creator of Jest/Metro

**What it's about:** Five engineering principles that remain essential — and are amplified — when AI agents handle most code execution: Strong Ownership, Taste (judgment over execution), Strict Guardrails & Fast Feedback, Context in the Repo, and Own your Stack. Argues the engineering bottleneck shifts from writing code to exercising judgment. Adds Option Value as a design principle: every architectural choice should unlock future options, not foreclose them.

**What we added:**
- Skill enhancement: `karpathy-guidelines` — Section 6 "Before Adding a Dependency" (ownership cost check: what constraints does this lock in? can it be built with agents instead?) and Section 7 "Option Value Check" (does this architectural decision unlock or foreclose future changes?). Also added two conditional checklist items to "Before You Write a Single Line". No new skill created — principles fit cleanly as additions to an existing checklist skill at different decision points (dependency selection, architecture).

---

## How I Actually Code (and Review) With AI in 2026
**URL:** https://medium.com/google-cloud/how-i-actually-code-and-review-with-ai-in-2026-005c89fbd113
**Added:** 2026-06-11
**Source / Author:** Christina Lin — Google Cloud Community

**What it's about:** Practical cookbook for writing code that AI agents can reason about and modify safely. Central thesis: AI has a cognitive load (the context window) that degrades past ~300k–400k tokens (context rot), so code should be designed for local reasoning with minimal blast radius. Covers five concrete patterns: blast-radius-first design, Rule of Three for abstractions (wait for 3 real call sites before extracting), vertical-slice folder organization (feature = one folder, no cross-imports), fail-fast/fail-loud error handling, and the reviewer-model-mismatch principle (the model that wrote the code is the worst reviewer of it).

**What we added:**
- Skill enhancement: `karpathy-guidelines` — Section 8 "AI-Legible Code" covering all five patterns; two new checklist items ("Blast radius estimated?", "Rule of Three applied?") in "Before You Write a Single Line"; updated frontmatter description. No new skill — principles belong in the always-invoked behavioral checklist.
- CLAUDE.md: Added "AI-Legible Code" section (6-bullet always-on block) to harness CLAUDE.md so principles are present in every session context without requiring skill invocation.

---

## loops! — Named Agentic Dev Loop Catalog
**URL:** https://loops.elorm.xyz/loops
**Added:** 2026-06-11
**Source / Author:** elorm (elorm.xyz)

**What it's about:** Curated catalog of 8 pre-built, self-pacing agentic dev workflow loops — each defined by a standardized kickoff prompt contract: named goal, max iterations, between-iterations check command, and binary exit condition. Loops include: Ship PR Until Green (CI), De-Sloppify Pass (cleanup), Spec-First Ship (checklist-driven impl), Build Until Green, Coverage Until Threshold, E2E Until Green, PR Self-Review (3 passes), and Pre-Commit Guard. The key insight is the portable loop contract format: a natural-language prompt template Claude can execute in any session without scaffolding.

**What we added:**
- Skill: `loop-patterns` — The 8 named loop templates as copy-paste kickoff prompts + the loop contract format + authoring guidance for new loops. Fills the gap between heavy `iterative-repair-loop` (JSON handoffs, artifact-specific) and `goal-mode` (evaluator-driven, delegates to sub-skills). Positioned as the lightweight, check-command-driven loop layer.
- Command: `/kiro:loop` — Interactive picker that lists all 8 loops, accepts a slug argument, and generates + launches the kickoff prompt automatically. Usage: `/kiro:loop ship-pr` or `/kiro:loop` for the picker.

---

## Claude Code — Model Configuration
**URL:** https://code.claude.com/docs/en/model-config
**Added:** 2026-06-15
**Source / Author:** Anthropic (Claude Code docs)

**What it's about:** Reference for how Claude Code resolves models — aliases (`opus`/`sonnet`/`haiku`/`fable`/`opusplan`/`default`), the `[1m]` context-window suffix, reasoning effort levels (`low`→`max`, plus `ultracode`), precedence order across `/model`/`--model`/`ANTHROPIC_MODEL`/settings, subagent model config (`CLAUDE_CODE_SUBAGENT_MODEL`), and enterprise controls (`availableModels`, `enforceAvailableModels`, `modelOverrides`, `fallbackModel`, gateway discovery). Notes Fable 5 as the most capable model for long autonomous sessions and Opus 4.8 as the current deep-reasoning tier.

**What we added:**
- Augmentation: `model-tiers` skill (migrated orphaned skill into harness source `skills/model-tiers/` so it now propagates via `install.sh`) — refreshed the deep-tier model ID `claude-opus-4-7` → `claude-opus-4-8`, added a 5th **autonomous** tier (`claude-fable-5`) for multi-sitting work, and a new "Effort Level — An Orthogonal Dial" section so effort (`low`→`max`) is treated as a lever separate from model choice. Tightened the description to predict WHEN it fires (≤200 chars).
- Doc sync: corrected stale `opus-4-7` references and added the Fable tier in `docs/memory/gbrain-patterns/gbrain-patterns.md` and `docs/harness-documentation/SDD-USAGE.md` (plus their `.claude/` mirrors).

---

## Don't let the LLM speak, just probe it
**URL:** https://blog.j11y.io/2026-06-10_hidden-state-probes/
**Added:** 2026-06-15
**Source / Author:** James Padolsey (j11y.io)

**What it's about:** When an LLM processes "does content X satisfy criterion Y?", the decision is already computed in the residual stream before any token is generated — generation is just the model translating a decision it has already made. Describes a 5-step recipe: small open model, seed token (`Assessment:`), training triples with varied criteria, hidden states at that seed position at ~70% layer depth, tiny MLP head, isotonic regression calibration. Result: one frozen model + one MLP = any English-criterion classifier at embedding-classifier cost with calibrated probabilities. Optional LoRA trick: train the LoRA to *write* verdicts (next-token loss), then never generate at inference — the text is scaffolding that crystallizes decision geometry at the seed token.

**What we added:**
- Skill augmentation: `llm-evaluation` — new "### 4. Hidden State Probes (non-generative classifier)" section under Core Evaluation Types, after LLM-as-Judge. Covers when to use over judges (structural criteria, calibrated probabilities, high-volume batch), when NOT to use (CoT needed, API-only model, dataset too small), the 5-step recipe, the LoRA geometry trick, and result framing. Also added a trigger bullet to "Use this skill when".

---

## index.how/to/articulate — Design Vocabulary Reference
**URL:** https://index.how/to/articulate
**Added:** 2026-06-15
**Source / Author:** Emil & Glenn (Index — pre-launch design education platform)

**What it's about:** 188 precisely-defined design terms across 12 categories (typography, color, iconography, layout, interaction, motion, accessibility, IA, copywriting, tools, analysis, components). Each definition contains embedded design opinions — not just "what is X" but "how to use X correctly." Tagline: "Say precisely what you mean."

**What we added:**
- Augmentation: `frontend-code-quality` skill — added "Visual Design Rules" CSS subsection (disabled state tokens, nested border-radius formula, tabular nums, dvh vs vh, safe area insets, pointer-events on decorative layers, OKLCH for gradients, semantic color tokens); expanded Animations section (ease-out/ease-in asymmetry, 150ms threshold, reduced motion media query); expanded Accessibility section (focus state replacement, 44×44px touch target, DOM order warning, label/for association); updated Quick Review Checklist with 14 new checkboxes across HTML and CSS sections.

---

## Agentic Code Review
**URL:** https://addyosmani.com/blog/agentic-code-review/
**Added:** 2026-06-17
**Source / Author:** Addy Osmani

**What it's about:** When agents make writing code cheap, the leveraged engineering skill becomes *proving* code works. Argues for risk-tiered review (effort proportional to blast radius), heterogeneous reviewers (everyday correctness + production-failure severity), and treating every AI review as a sensor, not a verdict. Sharpest actionable warning: agents take the cheapest path to a passing build — weakening tests or lowering CI thresholds ("gradient descent to green") instead of fixing the code.

**What we added:**
- Hook: `hooks/claude/test-integrity-guard.sh` (PostToolUse, matcher `Write|Edit|MultiEdit`, soft gate) — fires when a test file or CI/coverage config is edited and flags weakening signals: added skip/xfail/`@Disabled` markers, tautological/stub assertions (`assert True`, `expect(true).toBe(true)`), touched coverage thresholds (`--cov-fail-under`, `fail_under`, `coverageThreshold`), and removed assertions. Names the gradient-descent-to-green anti-pattern and asks Claude to confirm a deliberate spec change vs. a shortcut to green. Never blocks. Fills the gap where the harness reviewed code heavily but never watched the agent weakening the test gate itself.
- *Rejected:* risk-tiered review and separate-model review (already covered by CLAUDE.md blast-radius + reviewer-model-mismatch rules, `validate-adversarial-agent`, `session-judge`); decision-log/PR-reasoning capture (covered by `action-capture.sh`); prompt-injection scanning (covered by `scan-pii.sh` + `ai-security-workflow`).

---

## UI Skills — Curated UI Skill Directory + Routing Pattern
**URL:** https://www.ui-skills.com/
**Added:** 2026-06-17
**Source / Author:** ibelick (also https://github.com/ibelick/ui-skills)

**What it's about:** Curated directory of ~106 installable UI/frontend skills (design taste, motion, accessibility, React/Vue/Next, Three.js, charts, slides) fronted by a "UI Skills Root" routing skill. The routing skill's core idea is context economy: given a UI goal, identify the category, load the *smallest useful* set of skills, and **never load more than 3** ("prefer 1; 2 only for two clear angles; 3 only for broad review/redesign").

**What we added:**
- Augmentation: `skills/ui-skills/SKILL.md` — rewrote the prior hollow stub into a **UI build router**. Carries no UI rules of its own; maps task category → the harness's existing UI skills (`ui-ux-pro-max`, `frontend-design`, `wcag-audit-patterns`, `threejs-skills`, `react-best-practices`, etc.) and enforces the "prefer 1, never >3 skills" context-economy discipline via the Skill tool (no `ui-skills` CLI needed). Fills the known wrong-skill-fires / over-selection failure mode in the large UI skill family.
- *Rejected:* the `ui-skills` CLI + 106-skill registry (duplicate infra; harness already exposes equivalents via the Skill tool); individual Three.js/Vue/React/a11y/chart/slide skills (covered by `threejs-skills`, `react-best-practices`, `wcag-audit-patterns`, `frontend-slides`, etc.); niche design-taste skills (Oklch, Brutalist, Morphing Icons, Web Sounds — bloat, low repeated-task value); new hook/routine (routing is contextual judgment, not enforceable/schedulable); dashboard widget (no persistent output).

---

## Forward Future "Loop Library" — 45 Community Agent Loops
**URL:** https://signals.forwardfuture.ai/loop-library/
**Added:** 2026-06-21
**Source / Author:** Forward Future (forwardfuture.ai)

**What it's about:** Curated catalog of 45 community-contributed agent "loops" — self-pacing prompts that iterate → verify against explicit criteria → fix the highest-impact issue → re-test → stop on success/budget/stall. The site distills them to 6 universal principles: define success up front, one change per iteration, independent verification (separate builder/reviewer, adversarial critics, or two models), evidence required (root cause + before/after proof + changed files), fresh/clean state, and stop conditions. Nearly all 45 are domain variations on the same meta-pattern the harness `loop-patterns` skill already encodes.

**What we added** (augment-not-create — the existing `loop-patterns` skill is the engine; only genuinely-absent loop *classes* survived the value gate):
- Augmentation: `loop-patterns` skill — added loop **#9 Fresh-Clone Onboarding** (verify README/install docs from a disposable clean checkout; fix docs not environment) and **#10 Recent-Feedback Sweep** (turn one user correction into a project-wide fix + regression guard). Both fill loop categories the existing 8 CI/build/test loops lacked. Also added a **"Fresh/clean state"** guardrail (run reproducibility/onboarding passes from a disposable env to prevent false-green from a warmed workspace). Updated description count 8→10. See also: prior loop-library source entry "loops! — Named Agentic Dev Loop Catalog" (elorm.xyz) that created the skill.
- *Rejected:* new `loop-library` skill of all 45 (>70% duplicate of `loop-patterns` engine; bloat, fails compression); multi-LLM convergence (covered by `validate-adversarial`/`codex-review` + existing "two models/sessions" guardrail); builder-reviewer (covered by `subagent-driven-development` + `spec-refactor-agent`); accessibility repair (covered by `wcag-audit-patterns`/`accessibility-compliance`); test-stabilizer (covered by E2E-Until-Green + `test-fixing`); housekeeper (covered by `housekeeping-agent`/`simplify`); Goal Forge/SPEC.md (covered by `kiro:spec-*`); propagation/compliance loop (motion subsumed by Recent-Feedback Sweep; held back for leanness); new command/hook/routine/dashboard (`kiro:loop` exists; loops aren't lifecycle-automatable — contextual, user-triggered).

---

## Async Multi-Agent Orchestration — Anthropic Cookbook
**URL:** https://platform.claude.com/cookbook/patterns-agents-async-multi-agent-orchestration
**Added:** 2026-06-21
**Source / Author:** Anthropic Cookbook

**What it's about:** Bare-mechanics skeleton (Anthropic Python SDK + `asyncio`) for two multi-agent shapes — fixed N-agent peer team and dynamic spawn — built on a shared message `Hub` (per-agent inbox + `asyncio.Event`), a `spawn → status → collect → kill` lifecycle, and `send_message`/`wait_for_message` as the only inter-agent channel. Key non-obvious trick: peer messages are **appended to the last tool result** so agents receive them inline in their tool-use loop rather than polling.

**What we added:**
- Augmentation: `multi-agent-patterns` skill — added "### Implementing Async Peer-to-Peer Agents (raw SDK)" subsection under Detailed Topics (after Framework Considerations) summarizing the Hub + Event mechanics, the append-to-tool-result delivery trick, and the spawn→status→collect→kill lifecycle. Full code skeleton moved to `references/async-sdk-orchestration.md` (compression). Added cookbook to the References section. Fills the gap where the skill covered multi-agent *design*/tool-selection but had zero raw-SDK *implementation* mechanics.
- *Rejected:* new standalone skill (better as augmentation — `multi-agent-patterns` is the logical home; parallel skill would fragment "how to multi-agent" and risk wrong-skill firing); hook/routine (nothing fires every-time or on a schedule); script/command (reference knowledge, not an invokable action); dashboard widget (no persistent output); augmenting `async-python-patterns` (that skill is generic I/O-bound asyncio, not agent-specific).

---

## Vercel Eve: Open-Source Agent Framework
**URL:** https://vercel.com/blog/introducing-eve | **Added:** 2026-06-21 | **Source:** Vercel Blog

**What it's about:** Vercel's open-source framework that treats an agent as a directory of files (model, instructions, tools, skills, subagents, channels, schedules) — the framework owns the agent loop. Ships durable sessions, sandboxed compute, human-in-the-loop approvals, tracing/evals, and "channels" (Slack/Discord/Teams surfaces). Nearly all of it maps onto capabilities the harness already has; only the outbound-channel idea filled a real gap.

**What we added:**
- Script: `scripts/integrations/channels/notify.py` — stdlib-only sender that POSTs a message to whichever Slack/Discord/Teams incoming webhooks are configured in `~/.env.channels`; fills the harness's missing outbound-notification path
- Template: `templates/.env.channels.template` — copy to `~/.env.channels` (home dir, chmod 600, outside every repo); webhook URL is the credential, no OAuth
- Command: `commands/global/notify.md` — `/notify <message>` in-session broadcast to configured channels
- Routine wiring: `scripts/orchestration/daily-runner.sh` posts each repo's daily-maintenance summary to channels, gated on `~/.env.channels` existence (no-op otherwise; `SDD_SKIP_CHANNEL_NOTIFY=1` opts out)
- Docs: `docs/integrations/channels/README.md` — setup + per-platform webhook walkthrough
- Rejected: inbound channels / bot listeners (harness is a local CLI, not a deployed service)

---

## Top 30 Prompt Techniques That Actually Work in 2026
**URL:** https://agent-cookbook.com/tutorial/top-30-prompt-techniques-that-actually-work-in-2026 | **Added:** 2026-07-02 | **Source:** agent-cookbook.com

**What it's about:** A listicle enumerating 30 general Claude prompting techniques (explicitness, XML tags, few-shot, extended thinking, self-eval, agent-mode, plus 9 task-specific templates). 29 of 30 are already encoded in the harness's `prompt-engineering` + `prompt-quality-assess` skills and the agent-design skill family. The default-skip gate rejected all but one.

**What we added** (one augmentation — everything else was already covered):
- Skill augmentation: `prompt-engineering` — new "XML Tag Delimiting" subsection after Instruction Hierarchy. Teaches wrapping prompt regions in explicit XML tags (`<instructions>`, `<context>`, `<document>`) so the model never confuses instructions with data — Anthropic's most-emphasized structuring primitive, materially relevant on a 1M-context model that concatenates large context blocks, and a first-line prompt-injection defense for untrusted input. The one item literally absent from the existing skill (which taught only a markdown-header Instruction Hierarchy).

**Rejected (29/30, already covered):** few-shot / extended-thinking / context-first / self-eval loop / 1M-context / iterate → `prompt-engineering` sections; be-explicit / output-format / define-"done" / negative-constraints → `prompt-quality-assess` dimensions; agent-mode / multi-persona debate / prompt-chaining / reverse-brainstorming → `agent-execution-control` + `multi-agent-patterns`; the 9 task-specific templates → instances of the existing "Template Systems" pattern (encoding them = bloat). Also rejected the two other links from this batch outright — **DeepSeek dSpark** (GPU inference-serving speedup, a layer the harness never touches) and **Ornith-1.0** (self-scaffolding RL is training-only, baked into weights; the harness trains no models) — zero integrations each.

---

## "How to Kill the Bloat in Claude Code's System Prompt" — aihero.dev
**URL:** https://www.aihero.dev/how-to-kill-the-bloat-in-claude-codes-system-prompt
**Added:** 2026-07-08 | **Source:** aihero.dev

**What it's about:** Claude Code injects a hidden per-session startup payload (tool definitions, bundled skills, workflow engine) before any user content — a fixed token tax that runtime optimization techniques (RTK, lean-ctx) never see. The article provides a concrete four-lever taxonomy to reduce this: `disableBundledSkills`, `disableWorkflows`, `permissions.deny` (bare tool name to remove definition entirely), and `skillOverrides: "off"` / `"user-invocable-only"`. Measurement workflow: `/context` for category totals → proxy script for per-tool breakdown → apply levers → verify savings.

**What we added:**
- Skill augmentation: `context-optimization` — new "Claude Code System Prompt Levers" subsection inside "Two Independent Token Axes: Startup vs Runtime". Expands the previously empty startup axis with: measure-first procedure (`/context` baseline), per-tool inspection via proxy, a five-lever decision table (`disableBundledSkills`, `disableWorkflows`, `permissions.deny`, `skillOverrides: "off"`, `skillOverrides: "user-invocable-only"`), and the key invariant that RTK handles runtime and these levers handle startup — strictly independent axes.

**Rejected:** Standalone `claude-system-prompt-optimization` skill (conceptual home already exists in `context-optimization`); `claude-prompt-audit.sh` script (one-time developer investigation, not a recurring routine); `/claude-payload-audit` command (every decision node requires human judgment, no automation value).

---

## Batch Triage 2026-07-08 — 6 articles (4 AUGMENT, 2 SKIP via separate index files)

The following six articles were triaged in a single parallel batch on 2026-07-08.
Two additional resources (OpenWiki — git, PACE — papers) are logged in their respective category files.

---

## Autoresearch: The Feedback Loop Behind Self-Improving Agents
**URL:** https://www.latent.space/p/autoresearch-introspection
**Added:** 2026-07-08
**Source / Author:** Latent Space / Gavrilescu

**What it's about:** A Three-Loop Blueprint for self-improving agents: inner loop (task execution) + outer loop (loop improvement) + signal filtering layer. Introduces "Agent Recipes" — versioned artifacts capturing not just agent code/prompts but evals, judges, human expertise examples, failure history, and WHY decisions were made. Advocates git-based audit trails and staged autonomy (start human-heavy, agents absorb preferences over time).

**What we added:**
- Command augmentation: `commands/kiro/autoresearch.md` — new "Agent Recipe" section describing the `recipe.md` artifact, signal filtering policy, and staged autonomy levels. The inner loop (Karpathy experiment loop) was already handled; this adds the outer loop meta-layer and recipe convention.

**Rejected:** Standalone `agent-recipes` skill (content belongs in the command that uses it, not a separate artifact); outer loop hook (no lifecycle event maps to "the loop should improve itself"); `evolve-agent` augmentation (already handles behavioral audit — MCE concept in agent-harness-design covers the meta-improvement framing more precisely).

---

## Advanced Tool Use
**URL:** https://www.anthropic.com/engineering/advanced-tool-use
**Added:** 2026-07-08
**Source / Author:** Anthropic Engineering

**What it's about:** Three empirically validated techniques for high-scale tool systems: Tool Search (deferred discovery via `defer_loading: true`, 85% token reduction), Programmatic Tool Calling (Claude writes orchestration code, 37% token reduction, enables parallel calls), and Tool Use Examples (concrete JSON examples in definitions, 72%→90% accuracy). Enabled via `betas=["advanced-tool-use-2025-11-20"]`.

**What we added:**
- Skill augmentation: `skills/tool-design/SKILL.md` — new "Advanced Tool Use Patterns" section covering all three techniques with implementation details and decision rules. These are orthogonal to the existing consolidation/description engineering content.

---

## Anthropic Prompt Caching Documentation
**URL:** https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching
**Added:** 2026-07-08
**Source / Author:** Anthropic

**What it's about:** Authoritative implementation guide for Anthropic's server-side prefix caching via `cache_control` blocks. Covers: minimum token thresholds by model family, the 20-block lookback window limit, the non-symmetric cache invalidation dependency table (tool changes cascade; tool-choice-only changes don't), pre-warming with `max_tokens: 0`, and automatic vs. explicit breakpoint modes.

**What we added:**
- Skill augmentation: `skills/context-optimization/SKILL.md` — new "Anthropic API Prompt Caching" section. This is a third, orthogonal caching mechanism distinct from CAG (local HuggingFace KV preloading) and the KV-cache ordering heuristics already in the skill.
- Skill augmentation: `skills/cag-implementation/SKILL.md` — clarifying note in Limitation 2 pointing to `context-optimization` for the Anthropic API variant, preventing confusion between the two mechanisms.

---

## Graph-Based Agent Memory
**URL:** https://newsletter.systemdesign.one/p/graph-based-agent-memory
**Added:** 2026-07-08
**Source / Author:** System Design Newsletter (Omnigraph case study)

**What it's about:** Omnigraph: typed entity nodes + labeled edges + schema enforcement, with three retrieval layers (graph traversal + BM25 + vector, fused via Reciprocal Rank Fusion). Atomic manifest versioning for all-or-nothing writes, git-like branch isolation for concurrent multi-agent writes, and commit lineage for audit trails.

**What we added:**
- Skill augmentation: `skills/para-memory-files/SKILL.md` — new "Relationship-First Memory" section extracting the schema enforcement principle, explicit relationship entries (written to both sides), and branch isolation for concurrent writes. Full Omnigraph infrastructure was not extracted (too heavy for the file-based harness model).
- Reference fix: `skills/multi-agent-patterns/SKILL.md` — fixed two dangling references to a non-existent `memory-systems` skill; replaced with `para-memory-files` pointers (the actual closest equivalent in the harness).

---

## Agent Test Harnesses
**URL:** https://lilianweng.github.io/posts/2026-07-04-harness/
**Added:** 2026-07-08
**Source / Author:** Lilian Weng (lil'log)

**What it's about:** Survey of agent harness engineering patterns: plan→execute→observe→improve loop, persistent filesystem memory for long-horizon tasks, parallel sub-agents, Meta Context Engineering (MCE — a meta-agent that optimizes context management strategy itself), and AlphaEvolve-style evolutionary search. Introduces a seven-bottleneck checklist for diagnosing harness quality plateaus.

**What we added:**
- Skill augmentation: `skills/agent-harness-design/SKILL.md` — new "Phase 5: Harness Bottleneck Checklist" (7-item table: weak evaluators, memory lifecycle, incentive misalignment, diversity collapse, reward hacking, short-term bias, inappropriate oversight points) and "Meta Context Engineering (MCE)" section (three MCE shapes: agentic crossover, context flow strategy evolution, harness self-repair loop).

---

## Don't Rewrite Your CLI for Agents
**URL:** https://developer.microsoft.com/blog/dont-rewrite-your-cli-for-agents
**Added:** 2026-07-08
**Source / Author:** Microsoft Developer Blog

**What it's about:** Empirical study showing traditional argument-based CLI interfaces strictly outperform JSON payloads for agent use. Key findings: args achieve 100% correctness across all models; JSON degrades smaller models (Haiku 4.5: 40% vs 100%). JSON costs 4×–11× more tokens per task due to retry cycles. Shell escaping tax causes 9× cost gap on PowerShell vs Bash for JSON mode (args unaffected). Core principle: narrowing the valid input space compensates for model capability gaps.

**What we added:**
- Skill augmentation: `skills/tool-design/SKILL.md` — new "CLI-to-Agent Bridging" section with empirical data table, the "don't rewrite" rule, and a minimal intervention checklist (exit codes, quiet mode, optional `--json` flag). Fills the gap between "design a new tool" (existing content) and "make an existing CLI agent-friendly" (previously uncovered).

---

## Agent-Assisted SGLang Development: An Initial Exploration
**URL:** https://www.lmsys.org/blog/2026-07-02-agent-assisted-sglang-development
**Added:** 2026-07-28
**Source / Author:** LMSYS Org (SGLang Team)

**What it's about:** Agents are most effective inside complex systems when constrained by executable, evidence-gated workflows rather than given free rein. Emphasizes evidence-before-code (fixed profiling tables), frozen benchmarks, anti-reward-hacking containment (identical build path/flags for baseline vs candidate), and hard machine-checkable loop exit conditions.

**What we added:**
- Skill augmentation: `skills/agent-execution-control/SKILL.md` — "Machine-Checkable Exit Conditions" pattern ("a single sentence claiming task complete is not enough to exit").
- Resource augmentation: `skills/evaluation/resources/benchmark-construction.md` — anti-reward-hacking A/B containment (hold everything identical except the change under test; interleave runs; invalidate a run if the measured path silently changed).

---

## Agentic coding notes from Galapagos Island
**URL:** https://danluu.com/ai-coding/
**Added:** 2026-07-28
**Source / Author:** Dan Luu

**What it's about:** Practitioner-grounded notes on agentic coding failure modes and workarounds. Key empirical finding: agents explaining a hypothesis without running code were wrong ~50% of the time even across independent cross-checks; forcing actual execution removed most errors. Also covers contrarian persona ensembles, fuzzing invariants, and building bespoke loops over heavyweight orchestrators.

**What we added:**
- Skill augmentation: `skills/agent-execution-control/SKILL.md` — "Forced execution beats cross-checking" empirical rule tied into Plan-Execute-Verify.
- Skill augmentation: `skills/multi-agent-patterns/SKILL.md` — "Contrarian Persona Ensemble" pattern (each persona guards a named loop pathology; improves output at equal budget).
- Skill augmentation: `skills/loop-patterns/SKILL.md` — "Fuzz invariants, don't write tests" + "Build bespoke loops, not heavyweight orchestrators" (with the loops-degrade-without-a-human caveat).

---

## Closing the Verification Loop
**URL:** https://thinkroom.kieranklaassen.com/d/njrS5TJhis
**Added:** 2026-07-28
**Source / Author:** Kieran Klaassen (ThinkRoom)

**What it's about:** A seven-phase autonomous-QA skill where a branch proves itself ready: real-browser reality, functional + experiential (persona) judges, a fix-loop governor that escalates decisions it shouldn't make, and proof durable to a commit SHA per scenario. Central thesis: autonomous verification is about being auditable, not confident.

**What we added:**
- Skill augmentation: `skills/verification-skill-authoring/SKILL.md` — "Autonomous-QA Methodology" section encoding Flows-before-Matrix (The Email Rule), dual judges, the Fix-Loop Governor, regression-test-per-fix (red-before/green-after), independence-budgeting, and the exit gate ("a green matrix with a red suite is not ready").

---

## Stop Being the Code Review Bottleneck
**URL:** https://newsletter.posthog.com/p/code-review-tips
**Added:** 2026-07-28
**Source / Author:** PostHog Newsletter — Jina Yoon

**What it's about:** Delegate review toil to swarms of AI reviewer agents behind fail-closed automation, reserving human attention for genuinely complex decisions. Introduces StampHog, a PR auto-stamper with concrete fail-closed safety gates. Mostly corroborates existing harness rules (reviewer≠author, adversarial swarm, verify-by-observation); the novel artifact is the auto-approve gate checklist.

**What we added:**
- Agent augmentation: `agents/kiro/guardrails-agent.md` — "PR Auto-Approve Gate (Fail-Closed)" checklist: no conflicts/change-requests → deny-list blast radius (auth/secrets/billing/public APIs) → diff cap (<500 lines AND <20 files) → LLM showstopper pass → SME/CODEOWNERS routing; stamps only when all pass.

---

## Benchmarking Coding Agents on Databricks' Multi-Million Line Codebase
**URL:** https://www.databricks.com/blog/benchmarking-coding-agents-databricks-multi-million-line-codebase
**Added:** 2026-07-28
**Source / Author:** Databricks Blog — Gaba, Mathur, Singh, Wendell, Zaharia

**What it's about:** An internal benchmark built from real merged PRs. Findings: no vendor owns the Pareto frontier, the harness matters as much as the model (>2x cost swing on the same model), and setups should be ranked by cost-per-completed-task rather than per-token price. Uses execution-based grading (no LLM judge) and git-history sealing to prevent agents recovering the solution from commits.

**What we added:**
- Resource augmentation: `skills/evaluation/resources/benchmark-construction.md` — real-PR→prompt recipe (filter, strip solution, human-review, rewrite tests to allow alternative implementations), execution-based grading, and git-history-sealing anti-cheat.
- Skill augmentation: `skills/agent-harness-design/SKILL.md` — governing principle blockquote "The harness matters as much as the model" + cost-per-completed-task ranking.

---

## Flint: A Visualization Language for the AI Era
**URL:** https://www.microsoft.com/en-us/research/blog/flint-a-visualization-language-for-the-ai-era/
**Added:** 2026-07-28
**Source / Author:** Microsoft Research — Wang, Sarikaya, Tsukamaki, Galley, Gao

**What it's about:** An intermediate, semantic, human-editable chart language: the LLM emits terse high-level intent + semantic types, and a compiler derives the fragile low-level details (scales, formatting, layout). The transferable principle — emit intent, derive config — applies well beyond charts to any tool where the model currently hand-writes verbose fragile config.

**What we added:**
- Skill augmentation: `skills/tool-design/SKILL.md` — "Intent-vs-Compiler: Emit Intent, Derive Config" section (two-layer example, token-cost + error-surface payoff, decision rule).

---

## 18 Claude Settings That Change Everything
**URL:** https://agent-cookbook.com/tutorial/18-claude-settings-that-change-everything-14-are-hidden-3-clicks-deep-4-arent-in
**Added:** 2026-07-28
**Source / Author:** Agent Cookbook

**What it's about:** A catalog of Claude/Claude Code/API settings. Reliability is mixed — several claims (inference_geo, residency premiums, a "Dreaming signal") appear fabricated. Only a verified token-economics subset was extracted; the rest was deliberately excluded.

**What we added:**
- Skill augmentation: `skills/context-optimization/SKILL.md` — "Claude Code Token-Economics Settings" with ONLY two verified items: the MCP server `enabled` flag (~800–6,000 tokens/server, toggle per session) and prompt-caching breakpoint placement (after the stable prefix; TTL economics cross-checked against `claude-api`, with the 1h-TTL break-even discrepancy flagged rather than encoded). No settings.json was modified.

---

## Introducing OpenWiki Brains: General-Purpose Wiki Memory for Agents
**URL:** https://www.langchain.com/blog/introducing-openwiki-brains-general-purpose-wiki-memory-for-agents
**Added:** 2026-07-28
**Source / Author:** LangChain Blog — Brace Sproul

**What it's about:** Argues agent memory should be *proactive* (the agent fetches and maintains its own structured wiki from connected sources on a schedule) rather than *reactive*. Distinguishes deterministic connectors (auto-fetch feeds) from agentic connectors (goal-directed search tools). (Note: distinct from the `langchain-ai/openwiki` CLI repo logged in `git/README.md`, which was SKIP'd — this is the conceptual blog post, mined for framings only.)

**What we added:**
- Skill augmentation: `skills/agent-harness-design/SKILL.md` (Memory component) — "proactive vs. reactive memory" axis + "deterministic vs. agentic connector" taxonomy.

## Agent Skills — Best Practices (Anthropic official docs)
**URL:** https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices | **Added:** 2026-07-28 | **Source:** Anthropic / Claude platform docs

**What it's about:** Official Anthropic authoring guide for SKILL.md files: naming/description rules, "one level deep" reference discipline, progressive disclosure, eval-driven development (build scenarios + a no-skill baseline before writing docs), per-model-tier testing, and script-writing anti-patterns.

**What we added:**
- Skill enhancement: `skill-creator` — added gerund-form naming check, third-person description mandate, one-level-deep reference rule, >100-line-needs-TOC rule, eval-driven development (≥3 scenarios + baseline), per-model-tier testing, "solve don't defer" script-constant justification, and MCP fully-qualified tool naming.
- Skill enhancement: `skill-extraction` — reconciled Phase 5b SkillOS Quality Gate table against the same checklist (added as sub-bullets under existing Content quality / Compression dimensions, no new row added).

---

## Graph Engineering vs Loop Engineering
**URL:** https://www.aibuilderclub.com/blog/graph-engineering-vs-loop-engineering | **Added:** 2026-07-28 | **Source:** AI Builder Club

**What it's about:** Argues a loop is just one node in a graph, and promoting a loop to a multi-node graph only pays off under specific conditions — otherwise it's a relabeled loop with added maintenance cost. Provides a 4-question gut-check to score whether a design is a genuine paradigm shift.

**What we added:**
- Skill enhancement: `loop-patterns` — new "When a loop should become a graph, not just relabeled" subsection with the 4-question gut-check and scoring rubric (0-1 yes = keep it a loop; 2-3 = genuine composition; 4 = paradigm shift).

---

## AI-Native Code Review
**URL:** https://agentfield.ai/blog/ai-native-code-review | **Added:** 2026-07-28 | **Source:** AgentField

**What it's about:** Argues traditional PR review bundles jobs that fracture once AI writes code autonomously, and proposes "risk telescope" framing — per-dimension tunable thresholds instead of a single pass/fail verdict — plus an adversarial false-positive-suppression pass before surfacing findings.

**What we added:**
- Agent enhancement: `agents/kiro/guardrails-agent.md` — new "Refinements: Risk Telescope + Adversarial Suppression" section composing with the existing PR Auto-Approve Gate (deny-list decides which dimensions need human review; risk telescope tunes how sensitive each dimension's auto-flagging is; adversarial pass filters findings before they reach the human or the showstopper check).

---

## Designing APIs for Agents
**URL:** https://www.freestyle.sh/blog/opinion/designing-apis-for-agents | **Added:** 2026-07-28 | **Source:** Freestyle

**What it's about:** Argues agent-facing API design inverts human-API conventions — agents read full docs in one pass, so explicit-over-defaults, strict/precise error messages, unambiguous field naming, and thin "facts not utilities" interfaces serve agents better than convenience abstractions.

**What we added:**
- Skill enhancement: `tool-design` — new "Agent-Facing API Design Checklist" section with the four principles (explicit-over-defaults, strict errors over lenient coercion, unambiguous field naming, facts-not-utilities).

---

## 3 Techniques to Reduce Token Consumption in Claude Code / Codex
**URL:** https://tech.autoscout24.com/blog/posts/3-techniques-to-reduce-token-consumption-claude-code-codex/ | **Added:** 2026-07-28 | **Source:** AutoScout24 Tech Blog

**What it's about:** Argues token waste mostly comes from "information movement" (noisy output, blind file search, over-powered models) rather than reasoning. Covers output-trimming, structural/code-graph search over brute-force Grep, and task-based model routing.

**What we added:**
- Skill enhancement: `rtk-token-reduction` — new "Structural index before Grep/Glob" section (prefer AST-aware tools as the first navigation step on unfamiliar code) and "Subagent token caps" section (cap subagent reply length/model/tool allowlist for bounded subtasks; track cache-hit ratio and cost-per-task).

---

## Thariq (Anthropic / Claude Code team) — agentic workflow talk
**URL:** https://www.youtube.com/watch?v=IHbsfvbfAto | **Added:** 2026-07-28 | **Source:** YouTube (summary derived from search snippets, not a verified transcript)

**What it's about:** Describes a `/goal`-style mechanism to keep long agent runs on a persistent objective, a plan-before-build "remove unknowns" pass, and an 80% system-prompt-length cut by the Claude Code team.

**What we added:**
- Skill enhancement: `agent-harness-design` — one sentence cross-referencing the `/goal`-style persistent-objective pattern next to the existing Agent-Run Contract, explicitly flagged as a low-confidence/unverified source since the summary was search-derived rather than primary-source verified.

---

## Own the Outer Loop
**URL:** https://addyo.substack.com/p/own-the-outer-loop | **Added:** 2026-07-30 | **Source:** Addy Osmani (Substack)

**What it's about:** As agents write more code, the human's job shifts to owning the "outer loop" — accountability for shipping decisions, not code authorship. Introduces a "ladder of agency" (flag → investigate → execute → diagnose → propose → recommend → resolve) for graduated autonomy on out-of-scope discoveries, and reframes the governing question from "can we build this" to "should this exist, can we answer for it." Third Osmani piece extracted here — see "Agentic Code Review" (2026-06-17) and "Agentic Autonomy Levels" (2026-07-28, `docs/sources/x/README.md`), both of which already absorbed most of this article's accountability/contract/evidence framing.

**What we added:**
- Skill enhancement: `agent-permissions-design` — new "Ladder of Agency (Handling Out-of-Scope Discoveries)" section, mapping the 7-rung escalation model onto the skill's existing Scope Inheritance principle and Step 4 action-classification table.
- Command enhancement: `commands/kiro/pref-elicit.md` — new Step 1.5 "Confirm This Is Worth Building" existence check before the 5-question Socratic session, plus a "Why This Exists" section in the generated `prefs.md` output. Advisory, not a gate.
- Rejected: accountability contract (already distributed across `pr-babysit` Authority Boundary, Post-Task Convention, `action-capture.sh`); brownfield practices — worktrees/scoped-changes/time-boxing (covered by `using-git-worktrees`, `agent-permissions-design` scope inheritance, `agent-harness-design` stopping conditions); "operationalize your taste" (already the operating model of `impeccable-audit`/`clarity-gate`); distinct human roles reorg (org-design advice, not agent-actionable); decision-evidence logging for long-horizon tasks (covered by `action-capture.sh` + Agent-Run Contract evidence requirements); survey/study statistics (context only).

---

## Interviewing Engineers in the AI Era: Lessons from a Year of Rebuilding
**URL:** https://www.coinbase.com/blog/interviewing-engineers-in-the-ai-era-lessons-from-a-year-of-rebuilding | **Added:** 2026-07-30 | **Source:** Coinbase Engineering Blog

**What it's about:** Coinbase rebuilt its engineering interview loop for the AI-coding era around a 3-dimension "AI Fluency Rubric" (Usage / Application / Understanding Limits) and a strict "we don't add rounds" discipline — new signal must replace or merge with existing signal, never stack. The concrete evidence: two interview rounds meant to test different things showed 84% outcome correlation, meaning most of the second round's signal was already captured by the first; the fix was merging rounds, not adding a third to compensate.

**What we added:**
- Skill enhancement: `multi-agent-patterns` — new "Redundant Signal Check" subsection (after Contrarian Persona Ensemble) applying the 84%-correlation finding to review/verify pipelines: before stacking a new pass, check whether it's empirically redundant with an existing one; merge if so, only add a pass that catches a genuinely distinct failure mode.
- Rejected: AI Fluency Rubric and the rest of the interview-loop redesign (HR/hiring process, out of scope for a coding-agent harness).

---

## Eval Gates for Prompts
**URL:** https://luke.geek.nz/azure/eval-gates-for-prompts/ | **Added:** 2026-07-30 | **Source:** luke.geek.nz

**What it's about:** Argues prompt edits deserve the same CI gate as code deploys — fail closed if no eval exists, evaluate only the latest version, require actionable failure messages, and warn that a slow/opaque gate invites silent bypass paths. Frames a 4-stage maturity model (no process → manual review → automated eval gate → continuous evaluation of live traffic feeding regressions back into the test set), illustrated via Microsoft Foundry's prompt-agent versioning and the `microsoft/ai-agent-evals` GitHub Action.

**What we added:**
- Skill enhancement: `skill-curator` — new "Phase 3.5: Continuous Eval-Gate Drift Check" (stage 4 of the article's maturity model): samples Raindrop traces for skills with a logged `skill-eval-gate` PASS, clusters via `active-observability` to surface failure patterns the original scenario set missed, and proposes new scenarios as an "Add eval scenario" curation action. Rides the existing weekly cron — no new schedule.
- Skill enhancement: `skill-eval-gate` — new Safety bullets: overrides of a FAIL/INCONCLUSIVE verdict must be logged durably to `docs/skill-curation-report.md` history (not just the turn's chat summary), per the article's silent-bypass-becomes-default warning; and an explicit note that scenario sets go stale and should be revisited via the new drift check, not treated as one-time checkpoints.
- Rejected: statistical-significance/confidence-interval scoring (article's CI action runs over hundreds of live samples; harness gate runs n=3-5 authored scenarios — stat testing at that N is theater); prompt versioning + N→N+1 promotion routing (already solved by git + `update.sh`); separate status-check vs promotion-authority roles (no analog — skill-extraction Phase 5b is already the single promotion gate); standalone new hook/script (trigger is time-based drift, not a tool event — folded into the existing weekly routine instead).

---

## The Session You Cannot Take With You
**URL:** https://earendil.com/posts/session-portability/ | **Added:** 2026-08-02 | **Source:** Earendil Engineering blog

**What it's about:** Argues stateful LLM provider APIs (OpenAI Responses API, Anthropic Messages, Gemini Interactions API) are breaking the assumption that a saved transcript fully represents a session, via 6 opacity mechanisms — encrypted reasoning tokens, hosted search with only citations exposed, opaque server-side compaction, encrypted subagent messages, provider-only-resolvable file/cache references, and server-keyed conversation IDs. Proposes 5 audit criteria (Inspection / Export / Replay / Audit / Deletion) and 7 recommendations for anyone building agent systems against these APIs.

**What we added:**
- Skill enhancement: `secure-agent-design` — new "Pattern 7: Provider-State Portability & Audit Trail" section applying the 5 criteria as a design-review checklist (relying solely on `store:true`/`previousResponseId`, hosted-search citations without logged evidence, forwarding encrypted reasoning/signature blocks across a model switch, encrypted subagent messages with no plaintext log), plus a new checklist line in the skill's "Before Shipping an Agent" list.
- Rejected: standalone new skill (this is the same "agent touches external/provider state safely" concern `secure-agent-design` already owns — augmenting keeps it one coherent file rather than fragmenting); hook enforcing portability checks on file writes (no reliable static signal without heavy false positives — needs judgment); dashboard widget scoring session portability (nothing in this harness's actual Claude Code CLI usage exercises the measured failure mode — would show a permanently empty metric); augmenting `compaction-discipline-hook.sh`/`write_handoff.py` to log "lineage of what compaction dropped" (not automatable — Claude Code exposes only a `PreCompact` hook, no `PostCompact` diff, so what the real compaction step removed is invisible to us; current handoff snapshot is already the best available substitute); verbatim `session.export()`/`continueFrom()` pseudocode (no real SDK implements it as shown — documentation-only noise).

---

## Evals Skills for Coding Agents
**URL:** https://hamel.dev/blog/posts/evals-skills/ | **Added:** 2026-08-02 | **Source:** Hamel Husain (hamel.dev)

**What it's about:** Argues vendor eval MCP servers (Braintrust, LangSmith, Phoenix, Truesight) give agents data access but not judgment, and ships 7 Claude Code skills (`eval-audit`, `error-analysis`, `generate-synthetic-data`, `write-judge-prompt`, `validate-evaluator`, `evaluate-rag`, `build-review-interface`, repo: [hamelsmu/evals-skills](https://github.com/hamelsmu/evals-skills)) as a starting-point taxonomy of eval judgment tasks. Recommends practitioners build stack/domain-specific skills rather than relying on generic ones.

**What we added:**
- Skill enhancement: `evaluation/micro` — new "Error-Analysis Bootstrap" section (7-phase manual process: collect ~100 traces, read-and-note first-root-cause-only, cluster into 5–10 categories after 30–50 traces, label, compute failure rates, prioritize direct-fix > code-check > LLM-judge-evaluator, iterate). Fills a real gap: `evaluation/macro` and `active-observability` both assume an ML-clustering pipeline already exists — this is the human-driven bootstrap step before that infra is worth building. Output written to `.claude/memory/failure-taxonomy-<date>.md`, picked up automatically by the dashboard's memory-files panel (no new widget needed).
- Doc update: `docs/evaluation/README.md` — new entry for the error-analysis addition, matching the existing per-sub-skill convention (extracted-from link, coverage bullets, automation status).

**Rejected:** `validate-evaluator`'s TPR/TNR + Rogan-Gladen bias-correction judge-validation technique — real methodology, but requires a volume of human pass/fail labels on the exact quality dimension being judged, and this harness (internal dev tooling, no end-user-graded outputs) has no automatic source for that. Existing `action-capture.sh` only captures objective git/test/deploy signals, which don't need an LLM judge in the first place. Revisit if a downstream project starts collecting real human-graded output at volume. Also rejected: `eval-audit` (repo SKILL.md unfetchable, and structurally redundant with the existing `evaluation` router + `skill-curator`/`claudemd-review` fan-out-and-synthesize pattern), `write-judge-prompt` (binary-vs-Likert nuance folded as a one-line note rather than a standalone skill — rest already covered by `evaluation/micro`'s LLM-as-Judge section), `evaluate-rag` (no RAG project in current harness scope), `generate-synthetic-data` (no concrete method given in the source, already loosely covered by `evaluation/macro` Phase 0), `build-review-interface` (one-off UI-scaffolding task, not a repeatable methodology).

---

## Stacked pull requests are now in public preview
**URL:** https://github.blog/changelog/2026-07-30-stacked-pull-requests-are-now-in-public-preview/ | **Added:** 2026-08-02 | **Source:** GitHub Changelog

**What it's about:** GitHub native feature (public preview, 2026-07-30): break one large change into ordered, dependent PRs ("layers"), each targeting the layer below via the `gh-stack` CLI extension (`gh extension install github/gh-stack`). Reviewers see one layer's diff at a time via a stack map; merging the top PR cascades all unmerged layers beneath it; merging a lower layer auto-rebases/retargets the layers above. Launched after the model's Jan 2026 training cutoff — genuine capability gap, not generic git-workflow duplication.

**What we added:**
- Skill: `stacking-pull-requests` — reference for the automated flow below; owns troubleshooting (sync conflicts, reordering via `gh stack modify`, abandoning a stack), manual overrides (`SDD_SKIP_STACK`, `SDD_STACK_MIN_TASKS`), and deliberately does not restate `gh stack`'s CLI surface (public preview, delegates to GitHub's own docs/companion skill to avoid staleness).
- Script augmentation: `skills/git-pushing/scripts/smart_commit.sh` — auto-detects stack eligibility on the first task commit of a spec-impl branch (gh-stack extension installed + a `specs/<slug>/tasks.md` whose slug matches the branch name + ≥2 total tasks), inits the stack, and routes every subsequent commit through `gh stack add` + `gh stack submit --auto` instead of a plain commit — mapping this harness's existing "one task = one commit" convention (`CLAUDE.md:19`) onto "one task = one stack layer" with no per-instance judgment call needed.
- Hook augmentation: `scripts/pr/detect_base_and_create.sh` (shared by `pr-auto-create-hook.sh` / `pr-mention-nudge.sh`) — if `.git/gh-stack` shows an active stack, runs `gh stack submit --auto` instead of bundling everything into one `gh pr create`. Covers manual pushes that bypass `smart_commit.sh`.
- Doc updates: `docs/hooks/README.md` (both `detect_base_and_create.sh`-backed hook sections, new `[STACK-SUBMITTED]` output line); `skills/git-pushing/SKILL.md` (new "Stacked PRs" note).

**Rejected:** wrapper script around `gh stack` (CLI already complete, no capability gained); new `/stack-pr` slash command (redundant — the automation is unconditional, and manual invocation is covered by the skill's natural-language trigger); dashboard widget (one-time workflow decision, not an ongoing metric); editing the plugin-owned `create-pr`/`finishing-a-development-branch`/`git-advanced-workflows` skills (`~/.claude/skills/`, not harness source — edits would be lost on the next plugin update).

---

## Token-budget-aware LLM reasoning: cut costs in 2026
**URL:** https://redis.io/blog/token-budget-aware-llm-reasoning/ | **Added:** 2026-08-16 | **Source:** Redis engineering blog

**What it's about:** Argues reasoning-model token spend should match problem difficulty ("token-budget-aware reasoning," TALE). Covers prompt-level fixes (TALE-EP budget self-estimation, Chain-of-Draft ≤5-word reasoning steps) and architectural levers (semantic response caching, complexity-based model cascades/routing, persistent agent memory, OpenTelemetry token observability) — content verified directly against the source article, not paraphrased secondhand.

**What we added:**
- Skill enhancement: `skills/rtk-token-reduction/SKILL.md` — new "Sizing the cap: TALE-EP" subsection under the existing (previously purely qualitative) "Subagent token caps" section: the estimate-then-constrain two-phase pattern (~67% average token reduction, <3% accuracy drop across 7 benchmarks), plus the Chain-of-Draft caveat that arithmetic/multi-step-math subtasks lose ~4 accuracy points under a tight budget while commonsense/symbolic tasks lose nothing — don't apply one budget uniformly across task types.
- Skill enhancement: `skills/model-tiers/SKILL.md` — new "Cascade Escalation" subsection under "Cost vs Quality Trade-off": the per-call version of the existing session-level manual-upgrade guidance (try cheap tier first, escalate only calls that fail a confidence check), explicitly scoped to Claude's own tiers only — the cited research's cross-provider trained-classifier machinery doesn't fit this harness's single-vendor setup.
- Skill enhancement: `skills/prompt-caching/SKILL.md` — the existing "Response Caching" section was a one-line stub; replaced with the real Store/Match/Serve mechanism, the accuracy-vs-hit-rate distinction (92.5–97.3% positive-hit accuracy matters more than raw 61.6–68.8% hit rate), and an explicit fit caveat that the ~30%-similar-traffic premise underneath this technique is a multi-user/high-QPS assumption this single-developer harness doesn't obviously meet.

**Rejected:** semantic caching as new built infrastructure (workload mismatch — no embedding/vector-store infra exists, and this harness's own tasks are mostly novel per-session, not repeated queries); complexity-based routing as a trained router/classifier (architecturally out of scope — this harness only routes across Claude's own tiers, never third-party models; `model-tiers` already covers the portable cascade *shape*); persisting reasoning as episodic memory (Reflexion-style buffer) — already far exceeded by `agent-memory-systems` + `agent-memory-consolidation` + `agent-memory-discipline` and this harness's own steering/hot-memory system; reasoning-token observability (OpenTelemetry `gen_ai.usage.reasoning.*`) — not automatable, Claude Code's CLI doesn't expose a per-session reasoning-token breakdown the harness can read programmatically, and `rtk gain`/lean-ctx's `ctx_metrics` already give token-savings observability on a different axis; provider-specific thinking-budget parameters (Gemini/OpenAI) — this harness is Claude-only, no target to configure; cloud cost-optimization angle — confirmed `cost-optimization` already exists but is scoped to AWS/Azure/GCP infra spend, a different domain, no overlap to resolve.

---

## Interviewing Engineers in the AI Era: Lessons from a Year of Rebuilding
**URL:** https://www.coinbase.com/blog/interviewing-engineers-in-the-ai-era-lessons-from-a-year-of-rebuilding | **Added:** 2026-08-16 | **Source:** Coinbase Engineering blog

**What it's about:** Coinbase documents a year rebuilding its engineering *hiring interview* loop as AI-generated code rose from 5.7% (Q1 2026) to over 50% (Q4 2025) of everything merged. Converges on three durable interview signals (judgment/taste, telling correct-vs-plausible AI-assisted changes apart, knowing when to override the model) via a new "AI Fluency" rubric and reworked debugging/system-design rounds. Content mostly organizational/HR — a different company's hiring process, not a software-building technique — so almost none of it transfers to this harness's actual domain.

**What we added:**
- Skill enhancement: `skills/keep-rate/SKILL.md` — new "Step 1b — AI Adoption %" section computing what fraction of recent commits are Claude-co-authored at all (a volume/adoption signal), distinct from the skill's existing Keep Rate metric (a durability signal — what fraction of that code survived). Wired into `scripts/utils/dashboard.py`'s Session Quality stat-cards and glossary (4th card, shown only when adoption data exists).

**Rejected:** the "AI Fluency" rubric (Usage/Application/Understanding Limits) as a new grading rubric — ran a `better-call` comparison against this harness's existing `session-quality-rubric.md` + `session-judge` (charges/drains, evidence-cited, asymmetric scoring, kept-blind judge) + `spec-refactor-agent`; verdict KEEP INCUMBENT (27/30 vs 11/30) since the source rubric is three unelaborated labels written for grading human candidates, with no evidence-citation mechanism or automation path; debugging-round philosophy (catch AI-introduced bugs, judge correctness vs. plausibility) — already `spec-refactor-agent`'s explicit job; system-design-round philosophy — describes a human hiring-interview format, out of domain, and Coinbase itself flags it as still unpiloted; restating the AI-code-adoption philosophy in `CLAUDE.md`'s AI-Legible Code section — already operationalized there as concrete rules (blast radius, rule of three, etc.), restating would be hollow; any hiring/recruiting artifact as a new skill — `hr-pro` already exists (plugin-owned, can't be augmented from harness source) and the whole domain sits outside this harness's actual product surface regardless.

---

## Bloated Claude Code — a token-efficiency checklist
**Added:** 2026-08-16 | **Source:** X post via archive.codenewsletter.ai (2087176716901023834), originally by @EXM7777, 2026-08-11

**What it's about:** A ~12-item checklist for reducing Claude Code token bloat/slowness — CLAUDE.md hygiene, `/context`/`/usage` audits, disabling unused MCP/skills, permissions-over-prose, effort tuning, `/clear` at checkpoints, subagent delegation, a live statusline context meter, and working from a terminal rather than the desktop app.

**What we added:**
- Augmentation: `hooks/global/caveman-statusline.sh` — the script received Claude Code's native `context_window.used_percentage` telemetry on stdin but never read it. Added a color-coded live context-usage segment (green/yellow/red at 70%/90% thresholds), explicitly scoped to `context_window.used_percentage` rather than a naive key-name grep (the payload also has `rate_limits.*.used_percentage` at a different path under the same key name — verified this collision risk and guarded against it with a test). Also fixed a latent bug found while implementing this: the badge-rendering logic used to `exit 0` the *entire script* when caveman mode was off, which would have silently killed the context meter for the ~majority of sessions not running caveman mode — restructured to a `SHOW_BADGE` flag so the meter always renders independently.
- Dashboard: extended the fix onto the dashboard itself (not part of the original checklist, added per a follow-up user request) — the statusline now also persists a small per-repo state file (`~/.claude/dashboard-context/<hash>.json`, keyed by `sha256(repo_path)`) on every render, and `scripts/utils/dashboard.py`'s "Context Health" tab reads it to show a "Live context usage" card when a session for that repo has reported within the last 15 minutes. Deliberately *not* placed inside the "Workshop" tab as originally suggested — that tab is scoped to Raindrop traces for other registered app repos (aiq-zora-*, etc.), unrelated to Claude Code's own context usage; augmenting the existing Claude-Code-specific "Context Health" tab (Budget & Efficiency section) was the better fit.

**Rejected:** 11 of the resource's ~12 tips were found already implemented, generally more rigorously, on audit — CLAUDE.md terseness (Caveman mode's measured compression vs. a static instruction line), CLAUDE.md length discipline (`claudemd-review` bi-weekly audit + `/kiro:context-budget`), `/context` audits (`/kiro:context-budget`), `/usage` audits (`dashboard.py`'s `gather_usage_data()` + `skill-curator`'s usage-evidence logs), CLI-over-MCP preference (directly conflicts with this harness's enforced lean-ctx MCP-first policy — architecturally incompatible, not a gap), disabling unused MCP/skills (`skill-curator`'s automated deprecate/archive-at-N-days workflow), permissions-over-prose (`agent-permissions-design` + existing `.claude/settings.json` deny-lists), effort tuning (`model-tiers`' "Effort Level" section, verbatim match), `/clear` at checkpoints (`stop-hook.sh`'s automatic cache-cost-dominance handoff write, which doesn't rely on the user remembering), subagent delegation (`background-work-routing` + ~40 specialized `*-agent` definitions); working from a terminal/ADE instead of the desktop app — personal client choice, no settings.json key or hook could enforce it, out of scope.

---

## Claude Managed Agents: Consult an Advisor
**URL:** https://platform.claude.com/cookbook/managed-agents-cma-consult-an-advisor | **Added:** 2026-08-16 | **Source:** Anthropic Cookbook

**What it's about:** Documents the CMA "Advisor" roster entry — a reserved `{"type": "advisor", "model": ...}` coordinator-roster entry that lets a Managed Agents session's working model consult a stronger model, once, mid-turn, on a single high-stakes/irreversible decision, with the platform handling thread spin-up, delivery, and per-consultation cost tracking. Content (roster JSON, event types, constraints, cost-retrieval code) verified directly against the cookbook page, not paraphrased secondhand.

**What we added:**
- Skill: `skills/cma-advisor/SKILL.md` (new, sibling to the existing `skills/cma-outcomes/SKILL.md`) — covers roster setup, the platform-enforced constraints (one advisor per roster, reserved `anthropic.advisor` name, no-input tool so policy lives in the system prompt, fixed at session creation, concurrency-exempt, no per-consultation spend cap), the thread-lifecycle event stream (distinct from `agent.tool_use`), reliable thread identification (`thread.agent.type == "advisor"`, more reliable than the name), per-consultation cost retrieval, and the redaction behavior. Distinct API surface and use case from `cma-outcomes` (mid-turn escalation vs. post-hoc grade-and-revise) — not a merge candidate.

**Rejected:** nothing else proposed from this source — it's a single, narrow cookbook page describing one platform feature; the whole extraction is the one skill above.

---

## A Complete Guide to AGENTS.md
**URL:** https://www.aihero.dev/a-complete-guide-to-agents-md | **Added:** 2026-08-16 | **Source:** aihero.dev

**What it's about:** Guide to preventing AGENTS.md/CLAUDE.md files from becoming a bloated, contradictory "ball of mud" — lean entry file, progressive disclosure into topic files, avoiding hardcoded file paths (they go stale as the codebase evolves), monorepo root/package splitting, periodic cleanup audits with a copy-paste audit prompt.

**What we added:**
- Augmentation: `skills/claudemd-review/SKILL.md` — Phase 1 discovery now also finds `AGENTS.md` (the open cross-tool standard read by Codex/Cursor/Aider alongside CLAUDE.md); new Phase 2 checks for AGENTS.md/CLAUDE.md drift (one file a near-empty stub while the other carries real project conventions) and hardcoded-file-path staleness (instructions naming an exact path that no longer exists). Found the gap live in this repo on audit: root `AGENTS.md` was a 9-line lean-ctx stub while `CLAUDE.md` carried the real project rules — any AGENTS.md-only tool would see almost nothing. Phase 3 scoring template and Phase 6 summary table updated to report both file types.

**Rejected:** new `/kiro:agents-md-init` scaffold command (the source itself warns against auto-generating AGENTS.md via init scripts; would also duplicate `steering-agent`'s existing project-context bootstrap); symlinking `AGENTS.md` → `CLAUDE.md` (real fix but a one-off local file edit, not a reusable harness capability, and risks conflicting with lean-ctx's marker block in AGENTS.md); dashboard widget for instruction-file SNR/line-count trend (no existing hook instrumentation, disproportionate build cost vs. the audit skill already reporting the same info textually every 2 weeks).

---

## Specula: Scaling Formal Specifications
**URL:** https://muratbuffalo.blogspot.com/2026/08/specula-scaling-formal-specifications.html | **Added:** 2026-08-16 | **Source:** Murat Demirbas (muratbuffalo.blogspot.com), reviewing arXiv 2607.25333 / github.com/specula-org/Specula

**What it's about:** Specula is an agentic system (built on Claude Code) that auto-derives TLA+ formal specifications from real codebases and validates them via trace replay against actual execution, then runs a model checker to find concurrency bugs. The core novelty is a "self-evolving loop": trace validation pulls the spec toward reality, while model checking pushes back against the agent gaming or weakening invariants just to make them pass — weaker models (Sonnet, Haiku) were shown to reward-hack this far more than Opus. The TLA+/model-checker toolchain itself doesn't port to this harness (different domain — concurrency bugs, not general feature specs), but the anti-gaming half of the loop is a portable, currently-missing primitive: this harness already does trace-validation-equivalent work (`validate-impl-agent` checks impl against requirements/design) but had no check for whether the spec itself gets quietly weakened post-approval to make implementation pass easier.

**What we added:**
- Augmentation: `agents/kiro/validate-impl.md` — new "Spec Integrity Check (anti-gaming)" step. Diffs `requirements.md`/`design.md` against the git commit where the spec was approved; flags weakening edits (MUST→SHOULD/MAY, deleted edge cases/acceptance criteria, widened tolerances) as Critical unless a fresh human-approval marker exists for the edit, per CLAUDE.md's existing "never skip the human review gate" rule. Non-weakening edits are reported as "Spec refined," not a violation. Falls back gracefully if no approval commit is found.
- Augmentation: `agents/kiro/reflect-agent.md` — new `spec-drift` observation tag; when `validate-impl` flags spec integrity drift, `reflect-agent` now captures which feature/invariant drifted and promotes to a pattern in `meta/patterns.md` once 3+ observations share a theme (same feature repeatedly drifting, same invariant type weakened, same model tier implicated). This is the "progressive learning" half — recurring drift becomes a longitudinal signal the harness accumulates, not a one-off flag.
- Doc sync: `docs/harness-documentation/SDD-USAGE.md` — `/kiro:validate-impl` entry now mentions the spec integrity check.

**Rejected:** TLA+/model-checking pipeline in `specs/` (domain mismatch — harness produces natural-language EARS requirements, not TLA+; adopting a model-checker toolchain for general feature dev violates blast-radius/vertical-slice principles); model-weakness-correlates-with-reward-hacking as a model-selection config note (interesting but not a portable technique — it's an empirical finding specific to TLA+ bug-finding, would be a hollow "here's an interesting paper" addition on its own); bug reproduction via generated timing-sensitive integration tests (concurrency-bug-specific technique with no model-checker output to translate from — nothing concrete to port).

---

## The Shapes of Agent Memory
**URL:** https://www.pinglin.tw/blog/the-shapes-of-agent-memory/ | **Added:** 2026-08-18 | **Source:** pinglin.tw

**What it's about:** Empirical comparison of agent-memory architectures on LongMemEval/LoCoMo benchmarks — file-based (Claude Code/Cline-style markdown + grep) vs structured/store-based (raw dated-fact stores vs LLM-distilled knowledge graphs like Zep/Graphiti) vs trained experience memory. Key numbers: structured beats files 73.6% vs 44.9% on LongMemEval-S at ~25x the token cost (665k vs 27k tokens/correct answer); files win on abstention (88.9% vs 77.8%); the structured/file gap widens 15pts further at 500-session scale; raw dated-fact stores beat LLM-distilled graphs at 6x less context and 400x less ingest cost — the win is specific to cross-session joins/temporal aggregation, not "structure beats files" in general.

**What we added:**
- Augmentation: `skills/agent-memory-systems/SKILL.md` — new "File-Based vs Structured Memory" section with the LongMemEval evidence table and a practical read for this harness's own file-based `.claude/memory/` design (fine for session-scoped recall and small fact counts; degrades specifically on cross-session joins/temporal aggregation at scale — the signal for when to add a structured layer, not before).
- Augmentation: `skills/memory-systems/SKILL.md` — sharpened the existing "❌ Knowledge graphs for agent memory" anti-pattern with the article's nuance: the underperformance is specific to *LLM-distilled* graphs, not structured stores generally; raw dated-fact stores score ~78%, beating both files and distilled KGs at far lower cost.

**Rejected:** standalone new "memory shapes" skill (would be a 5th+ memory skill in an already-saturated area — `agent-memory-systems`/`agent-memory-consolidation`/`agent-memory-discipline`/`memory-systems` already cover this territory; augment, don't fragment); reader/judge-model-sensitivity finding (already implicit in existing evaluation/judge-calibration skills); trained experience-memory finding (sufficiently adjacent to `agent-memory-consolidation`'s existing episodic-first stance, not a distinct enough mechanism).

See also: [git/README.md](../git/README.md) — the `x74353/Amphetamine` scan run in the same batch surfaced an unrelated but real gap (LaunchAgent sleep interruption), fixed via native `caffeinate`, logged there.

---

## 14-source sweep, 2026-08-25 — batch note

One `/skill-extraction` run over 14 links (newsletter archives, two GitHub repos, three YouTube talks, one arXiv paper, three essays), one sub-agent per source. **8 of 14 yielded nothing.** The 6 that did are logged individually below and in `git/`, `papers/`. Recording the rejects here so sibling links from the same newsletters are not re-litigated:

- **archive.codenewsletter.ai/2089475434903953561** — archived X post, Magnitude CLI "model catalog" product copy. Covered by `llm-inference-async-batching` / `local-llm-eval`, and irrelevant: this harness is subscription-only with no local-inference path.
- **youtube.com/watch?v=iqRcGCah0Kw** — freeCodeCamp multi-agent PR reviewer course. Transcript unobtainable; syllabus-only. Ideas already implemented end-to-end by `multi-agent-patterns` + the `pr-auto-create-hook` → `gitnexus-pr-review` → `code-review-learning-runner` pipeline.
- **archive.codenewsletter.ai/2090245922685063634** — single X post announcing the `"outputStyle": "Concise"` config key. One vendor toggle; verbosity already governed by CLAUDE.md conventions + the lean-ctx directive.
- **archive.codenewsletter.ai/2090141955695198633** — @poteto "1000 PRs" thread. Its only technical link (`cursor/plugins/pstack`) was already mined 2026-07-28, see [git/README.md](../git/README.md). `/goal`, `/loop`, `/swarm` map 1:1 onto `goal-mode`, `commands/kiro/loop.md`, `dispatching-parallel-agents`.
- **github.com/mukul975/Anthropic-Cybersecurity-Skills** — 817 genuinely high-quality skills, Apache-2.0, ~30.7k★. ~99% redundant against the ~50 security skills already installed. Three real gaps exist and were **deliberately deferred**, not missed: honeytokens/canarytokens (harness has zero deception coverage), Sigma detection-rule authoring, threat-hunt hypothesis framework. Deferred on context-budget grounds — `skill-curator` + `skill-usage-tracker` exist to fight description bloat, and these target a discipline this harness may never invoke. Revisit if defensive security becomes an active domain.
- **seangoedecke.com/good-api-design** — 26 of its 29 rules already covered with file:line citations across `api-design-principles`, `api-patterns`, `backend-architect`, and 5 more. The 3 uncovered (per-customer killswitch, accidental-abuse shapes, "version only as a last resort") are ~15 lines of declarative prose, none mechanizable. Maximally saturated model-strong domain — per SkillsBench (arXiv 2602.12670) ~4.5pt lift here vs ~51.9pt in model-weak domains.
- **youtube.com/watch?v=AQ_Iqo3UYMk** — Jan Marshal dev-stack tour. Full transcript read. Its "cap skills at 50–60" heuristic is strictly worse than existing `skill-curator` (measured char budgets + git-tracked usage evidence vs a vibes number). Sole novel idea — pick a stack by LLM training-data density — is a 4-line heuristic, not skill #1000.

**Method note (fed into `skills/skill-extraction` Phase 1):** WebFetch failed or degraded on 5 of 14 sources. `yt-dlp` is absent, and YouTube `timedtext`/InnerTube are now gated behind a PO token with transcript mirrors returning 403 — the rung that works is `uvx --from youtube-transcript-api youtube_transcript_api <ID> --languages en`. Three of four `archive.codenewsletter.ai` IDs turned out to be single X posts, not roundups, so the `articles` classification was wrong for those (category `x` would have been correct).

---

## Learn Harness Engineering (Walking Labs) — lectures 06, 09–11, 13–14
**URL:** https://walkinglabs.github.io/learn-harness-engineering/en/
**Added:** 2026-08-25
**Source / Author:** Walking Labs (second pass; first pass logged 2026-05-31 above, covering lectures 03–05, 07–08, 12)

**What it's about:** Second extraction pass targeting the lectures the 2026-05-31 pass did not cover. L09 premature completion, L10 end-to-end verification, L11 observability and the sprint contract, L13 loop engineering, L14 graph engineering. Core argument of the uncovered half: completion judgment must be externalized to the harness and based on runtime signals, not agent confidence.

**What we added:**
- Augmentation: `agents/kiro/verify-agent.md` — new conditional **Stage 7 (System/runtime)** plus an explicit three-layer model (L1 static → L2 unit → L3 system) with strict layer gating, a new `UNPROVEN` verdict, and a completion-priority constraint (no refactor/perf/style findings until functional verification passes). The gap was concrete: stages 1–6 all inspect *artifacts* — files, exit codes, output text — and none observes the software running, so `verify-agent` could return READY for code that had never been executed. `verification-before-completion` did not close this; it demands fresh evidence but is agnostic about which layer supplies it, so a green unit test satisfied it.
- Augmentation: `commands/kiro/verify.md` — mode resolution for the conditional system stage (`full`/`pre-pr` only), plus `UNPROVEN` next-steps guidance.
- Augmentation: `agents/kiro/guardrails-agent.md` — scaffolded rules must carry **what / why / how-to-fix** in their violation message, and `audit` now reports a Message Quality line for project-authored rules. Agents are the primary reader of lint output; a bare assertion produces a retry loop instead of a self-correcting one. `skills/tool-design` already stated the principle but scoped it to agent-facing APIs, never reaching the lint rules the harness itself generates.

**Rejected:** loop engineering (six primitives, `/goal` vs `/loop`, generator-evaluator separation, maturity ladder) — already covered by `goal-mode`, `loop-patterns`, `iterative-repair-loop`, `commands/kiro/loop.md`, `scripts/routines/*`; four silent costs — already in `multi-agent-patterns` `[loop-debt]`; orchestration tax — already extracted; **graph engineering** — a prior pass explicitly decided *not* to create a standalone graph skill (`x/README.md`, >70% subsumed by `multi-agent-patterns`), and this lecture adds nothing that decision did not weigh; sprint contract (only novel part is a binding Exclusions section; the SDD spec flow with human gates is already a heavier version); evaluator rubric thresholds — already in `guardrails-agent` Risk Telescope; review-feedback promotion — already `code-review-learning-runner`; OTel sprint traces — no consumer in a bash/markdown harness; L06 initialization-as-a-phase — already `spec-init` + `adapt-to-repo` + `steering`.

---

## AI agents can't read social media (Corey Haines / Maker Skills)
**URL:** https://archive.codenewsletter.ai/2089027423774048326
**Added:** 2026-08-25
**Source / Author:** Corey Haines — archived X post marketing the "Maker Skills" plugin marketplace

**What it's about:** Marketing post for a 20-skill founder/operator plugin marketplace, hooked on the observation that agents get stopped cold by login walls and bot blocks. Its `/social-fetch` skill walks a free-first strategy ladder (native oEmbed → agent-browser → paid scrapers), accumulates a *reason* for each rung's failure, and uses the Wayback Machine both mid-chain and as last resort.

**What we added:**
- Augmentation: `skills/skill-extraction/SKILL.md` Phase 1 — a **retrieval recourse ladder**. Phase 1 previously read, in full, "Use WebFetch or WebSearch to retrieve the content at the provided link," with no guidance for degraded retrieval. Now: name the failure mode, walk 7 rungs (direct → redirect → no-auth provider JSON → `uvx youtube-transcript-api` → archive mirrors → Wayback → WebSearch-for-writeup), report which rung succeeded, and on total failure report *which rungs were tried and why each failed* rather than a vague "unavailable". Carries the source's null-vs-zero rule (a field the source never mentioned is unknown, not `0`) into `docs/sources/` entries, and adds a hard rule: if no rung returns real content, propose nothing — an extraction built on a title and a guess is fabrication wearing a citation.

The gap was demonstrated four times in the same batch that surfaced it: this source resolved only via an incidental archive mirror; the Harrison Chase talk initially fell back to a secondhand recap; the freeCodeCamp video yielded syllabus-only; and one video succeeded solely through `uvx youtube-transcript-api` after five other methods failed. The harness's own Phase 0 table had already conceded the gap structurally by routing pasted X content to category `x` — i.e. the documented path for X was "the user pastes it manually."

**Rejected:** `social-fetch` itself as a harness skill — domain mismatch (founder marketing tooling in an SDD coding harness), and hollow in practice since the load-bearing `references/strategies.md` was not retrievable; Maker Skills marketplace structure (`skillify`/`toolify`, per-skill semver) — `skill-extraction` + `commands/kiro/skill-extract*` + `skill-curator-runner` already cover this more deeply; social-post output schema — no harness consumer; 24h TTL caching — textbook, and `ctx_read` already caches at the real retrieval layer; the other 19 Maker Skills — business/operator domain, and `second-brain` overlaps the existing memory system.

---

## Practical Loop Engineering (Addy Osmani)
**URL:** https://addyo.substack.com/p/practical-loop-engineering
**Added:** 2026-08-25
**Source / Author:** Addy Osmani (fourth Osmani piece logged; see agentic-code-review and own-the-outer-loop above, plus agentic-autonomy-levels in `x/README.md`)

**What it's about:** A four-rung autonomy ladder (agentic → goal-based → time-based → proactive) with the anatomy of a well-formed `/goal` condition and six loop failure modes drawn from maintaining the Agent Skills repo at 80–90 PRs/day. Near-total prior absorption — an explicit overlap map put 15 of its 17 ideas against existing harness artifacts.

**What we added:**
- Augmentation: `skills/goal-mode/SKILL.md` Phase 1 — the condition formula went from three parts to five, adding **(4) an invariant** the run must not violate ("do not change the public API of any exported hook", "do not edit or delete an existing test to make it pass") and **(5) a per-turn progress requirement** with an abort clause. Also: name the tool that produces the evidence in part 2. The gap is precise — the evaluator reads the *transcript* and is not a quality reviewer, so a condition guarded only by a metric and a turn cap is silent on the two ways a run fails while technically succeeding: satisfying the metric by breaking something out of scope, and burning every turn making zero progress. Cross-referenced to `loop-patterns` circuit breakers so the two thresholds stay one concept.

**Rejected:** a new loop-engineering skill — `loop-patterns` (11 named loops, contract format, loop→graph gut-check) plus its routing table already covers the ladder; `verify-frontend-change` as a skill — >70% overlap with `ui-visual-validator`, and the residual delta (console-error hard gate, CWV trace) has no frontend call site in this repo, so Rule of Three fails; the failure-mode catalogue — five of six map onto existing artifacts and the harness's thresholds are *stricter* (2-pass breakers vs his 3× rule), so importing would weaken it; parallelism numbers (5–10/day) — personal telemetry, not a mechanism; `/loop` fine print (7-day expiry, session scope) — volatile product detail, and this harness schedules via `daily-orchestrator.sh`; "first loop = your morning manual check" — already implemented seven times over in `scripts/routines/`.

---

## When to Build Your Own Agent Harness (Harrison Chase, LangChain)
**URL:** https://www.youtube.com/watch?v=HI2q3ci3Iuc
**Added:** 2026-08-25
**Source / Author:** Harrison Chase, LangChain — Sequoia/Sovereign-AI talk, ~23 min

**What it's about:** Decomposes an agent into model / context / harness and argues the harness — "bring context to the model at the right point in time" — is the part most teams buy without thinking. Contributes a buy-vs-build rule for harnesses and an argument that private evals replace gradient descent at the harness layer.

**What we added:**
- Augmentation: `skills/agent-harness-design/SKILL.md` — new **Distribution Test** section under the Improvement Layer Decision block. The existing block routes *which layer to fix*; this routes *whether to own the layer at all*. The rule: the closer work sits to what frontier models were trained on, the better an off-the-shelf harness performs — and critically, **distribution is per-subtask, not per-mission**. A legal-AI mission is far out of distribution while *editing a file* is not, so the right shape is a custom harness that still delegates in-distribution subtasks to the model-native tool. Concrete mechanism from the talk: LangChain's Deep Agents uses **model profiles**, swapping the edit-file implementation per model, because OpenAI and Anthropic models were RL'd on different edit formats and each is best at its own. Includes an applies-to-this-harness note: sdd-harness replaces model-native Read/Grep/Glob wholesale with `ctx_*` wrappers, and file reading and repo search are maximally in-distribution subtasks — defensible on token-compression grounds, but a trade of accuracy-the-model-already-had for context budget that should be re-justified rather than assumed.

**Verification note:** the sub-agent's first pass reached only a secondhand recap and flagged its own conclusion as unverified. The claim was then confirmed against the primary transcript before writing, which also yielded the model-profiles mechanism the recap omitted. This is the recourse-ladder rule from the Maker Skills entry working as intended.

**Rejected:** the context-is-the-usual-failure thesis — already in `agent-harness-design` near-verbatim; Harbor eval-case format — `skills/evaluation`, `macro-eval-sweep`, `learn-eval`, `macro-eval-runner` saturate this, and a 4-field naming convention is hollow without the runner; trace→feedback→experiment flywheel — fully covered by `agent-trace-hook`, `reject-feedback-hook`, `code-review-learning-runner`; hooks around a plain loop — this repo has 35+ hooks plus lean-ctx compression doing exactly this; LLM-judge cost → fine-tune small models — no local capability (subscription-only, no API key), so the automation verdict would be dishonest.

---

## Five design patterns for a long-horizon agent harness (Google ADK)
**URL:** https://archive.codenewsletter.ai/2090248297214525569
**Added:** 2026-08-25
**Source / Author:** Google ADK team — long-form engineering post with an Apache-2.0 reference implementation (`google/adk-samples`, `core/python/long-horizon-harness`)

**What it's about:** Not a roundup — a real engineering post. Organizing claim: long-horizon agents fail *silently*. The team dogfooded for weeks while "nothing ever threw an error" and five distinct defects accumulated. Five patterns follow, each anchored to a named source file: stable prefix, background learning, persistent workspace, explicit failure, guard chain.

**What we added:**
- **Hook rewrite:** `hooks/claude/git-destructive-guard-hook.sh` — replaced string matching with structural parsing. The hook previously regex-stripped quoted segments then `grep -E`'d the remaining raw text; it is the harness's only hard refusal point, and every one of `F=--force; git push $F`, `bash -c 'git push --force'`, `git push --fo""rce`, and `cd sub && git push --force` defeated it while remaining a real force-push. Now tokenized with `shlex`, split on shell operators, compared token-by-token against exact flag names, recursing into `bash -c` and resolving `git -C`/`git -c` prefixes; fails closed on unparseable input and on unresolved `$VAR`/`$(...)` in a destructive verb. The ADK isomorph: blocking the literal string `169.254.169.254` does nothing about `curl http://2852039166/`. New test suite `hooks/claude/git-destructive-guard-hook.test.sh`, 46 cases, all four historical bypasses pinned as regression tests.
- Augmentation: `skills/agent-permissions-design/SKILL.md` — new "Verdict Computation and Context-Dependence" section: normalize-before-compare as a stated rule, fail-closed on unresolvable values, tiered grant matching so an approval for one command shape cannot be replayed by a rewrapped variant (labelled design-guidance-only, since Claude Code exposes no grant store a harness controls), and **an `ask` verdict degrades to `deny` under headless execution** — `daily-orchestrator.sh` and `scripts/routines/*` have no interactive user, so a prompt-the-human verdict there silently becomes a hang or an implicit allow. Two new anti-pattern rows.

**Rejected (ADK entry):** stable prefix / prompt ordering — Claude Code owns prompt assembly and hook `additionalContext` already lands at the tail; its recommended diagnostic (read cached-token count on turn two) is unavailable on a subscription with no usage object, so any hook claiming to measure cache hit rate would be fabricated; background learning / write-behind memory — asyncio- and ADK-bound (task GC, 4s-vs-5s drain), and the transferable residue (throttle, isolate the writer) is already `startup-payload-audit` day-guard + `daily-orchestrator` single tick + `gbrain-memory-write`; persistent workspace — assumes a managed cloud sandbox, this harness runs on one local filesystem; egress/exfil guard as a hook — threat model doesn't transfer, the agent already has the user's full shell, so a metadata-IP blocker here is theater (the *reasoning* inside it is what was kept); loop caps (200 tool calls/iteration) — arbitrary constants for a different runtime, and nothing in `hooks/` can strip tools from an in-flight request; **typed terminal state for sub-agent handoffs** — real gap (grep of `subagent-driven-development` + `agent-trace-hook` returns zero hits for timeout/truncation) and deliberately deferred, not missed: it touches every agent template and a marker-presence hook cannot catch a hallucinated `STATUS: completed`, so blast radius outweighs enforcement strength. Revisit if a parent agent is observed laundering a truncated child's report.

---

## How to Build the Most Powerful System for AI Coding (the "AI dark factory")
**URL:** https://www.youtube.com/watch?v=eecUhBpTz_g
**Added:** 2026-08-26
**Source / Author:** YouTube video. Channel metadata never loaded — the page returned only boilerplate, so the creator is **inferred from transcript self-reference** ("Archon, my open source harness builder", "the Dynamis community"), not confirmed. Transcript retrieved via `uvx --from youtube-transcript-api youtube_transcript_api eecUhBpTz_g --languages en` (recourse-ladder rung 4).

**What it's about:** A walkthrough of an "AI dark factory" — a repository that ships its own code. A spec goes in as a GitHub issue; a 30-minute cron triages it through plan → build → test → PR → blind review → merge → blue-green deploy with no human in the loop. Positioned as levels 4–5 of Dan Shapiro's five levels of AI coding, with the explicit caveat that level 3 (human in the planning and validation loop) "is mostly where we should be." The dense parts are the reliability mechanisms, not the pipeline: a **priority ladder** for the cron tick (fix-failing-PR → review-open-PR → build-next-approved → triage-new), a **three-file guidance layer** splitting global rules from stricter factory rules from a `mission.md` of goals *and non-goals*, **holdout scenarios** written before the work and kept unreadable by the builder, and a hard information barrier between builder and validator: "you don't want the builder to know about your tests. You don't want the validator to know about the plan."

**What we added:**
- **Hook:** `hooks/claude/headless-envelope-hook.sh` — `SessionStart`, gated on `SDD_HEADLESS=1`, emits zero bytes in every interactive session. Injects six constraints into unattended runs: one unit of work; no push/rewrite/force git (commit only when the routine prompt says so); writes confined to report files and `.claude/memory/` unless the routine prompt names a harness artifact class as its output; a two-strike loop guard that writes `ESCALATION:` into the report instead of retrying a third time; honest partial reporting; no new dependencies. Registered `SessionStart` in `templates/settings.json.template`, `templates/settings.harness.json.template`, and this repo's `.claude/settings.json`. Tests: `hooks/claude/headless-envelope-hook.test.sh`, 19 cases. Opt-out `SDD_SKIP_HEADLESS_ENVELOPE=1`, per-runner only.

  What transferred is the **factory-rules-are-not-global-rules** separation, mapped onto a real and previously unguarded surface. Seven headless entry points — the six `scripts/routines/*-runner.sh` and `daily-orchestrator.sh`'s drift review — all run `SDD_HEADLESS=1 claude --print --permission-mode bypassPermissions`. Verified at time of extraction: `SDD_HEADLESS` was read in exactly three places (`stop-hook.sh`, `caveman-savings-hook.sh`, `scripts/utils/dashboard.py`) and in every one of them to **suppress** interactive behavior. Nothing anywhere read it to **tighten** behavior. The least-supervised sessions in the harness were running with the widest permissions and a suppressed stop hook. The two-strike guard comes from the same source's stated reason for keeping a human fail-safe — agents "go through an infinite loop of trying to fix a problem."

  One design correction caught before shipping, worth recording: the first draft of rule 3 flatly forbade writing harness artifacts, which would have contradicted `harness-health-prompt.md` step 3 (rewrite up to 3 `SKILL.md` files per run, gated on a ≥2-point score improvement) and `daily-maintenance-prompt.md` step E (draft up to 3 `BEHAVIOR.md` specs). The rule now defers to an explicit routine instruction and refuses to generalise from it. A blanket prohibition would have silently broken two working routines.

**Rejected:** the autonomous spec→merge→deploy pipeline itself — `CLAUDE.md` mandates "Never skip the human review gate between spec phases," so a self-merging factory contradicts a standing rule, and this repo ships no deployed application for blue-green to refer to. **Holdout scenarios (full form)** — the most interesting unimplemented idea in the batch and still a skip: it needs an acceptance-scenario artifact plus a real mechanism to hide it from the builder, and this repo has no test suite at all (verification is `*.test.sh` plus throwaway-tree runs). No observed defect it would have caught. The cheap 20% was taken instead as the `validate-adversarial` augmentation below. `mission.md` reject-right — ~75% covered by `idea-refine`, `spec-grill`, `clarity-gate`, `surfacing-unknowns` plus the human approval gate; the pushback path here is a human reading a spec, not an agent auto-rejecting an issue. Cron priority ladder — `daily-orchestrator.sh` already sequences routines with per-routine cadence guards.

**Also extracted here (same session, from the builder/validator barrier):**
- **Augmentation:** `agents/kiro/validate-adversarial.md` — Step 0 now specifies a **read order** rather than a read set. Requirements first, commit to the acceptance criteria from requirements alone, *then* read design and tasks. Any criterion that does not trace back to requirements.md gets reported as **criterion drift**: the builder's framing leaking into the review. This is distinct from `validate-impl`'s spec-integrity check (`agents/kiro/validate-impl.md:108-114`), which catches the spec being weakened *in git* after approval; this catches the reviewer silently inheriting the builder's definition of done from a spec nobody edited.

---

## Intelligence EXPLOSION: Harness Engineering with Pi Agent, Deepseek, and Gemini
**URL:** https://www.youtube.com/watch?v=rqZHR-hRllI
**Added:** 2026-08-26
**Source / Author:** YouTube video. Channel metadata never loaded; creator **inferred from transcript self-reference** ("Andy Deb Dan here" — an ASR rendering), not confirmed. Transcript via recourse-ladder rung 4.

**What it's about:** A V2 "fusion harness" built as a Pi coding-agent extension, demonstrated against the DuckDB V2 preview. Three multi-model orchestration commands: `/fh opinion` (N models answer one question, with a per-model cost and tokens-per-second comparison), `/fh debate` (3–5 models argue across rounds, sharing each other's positions between rounds, converging or not), `/fh collaborate` (every model produces a plan, one designated architect — the most expensive model — integrates them and validates). A practical detail worth keeping: model identities are masked behind aliases (Rune, Flux, Drift) because revealing the real model name to a peer makes them "start emitting weird behavior… start competing and start sabotaging each other." Thesis: combine compute, don't select it; own your agent harness; the next leverage step is the "software factory" and out-loop agentic coding.

**What we added:** nothing.

**Rejected:** all three fusion commands — **structurally blocked, not merely covered**. Multi-provider fusion needs Gemini/DeepSeek/Grok API keys; every LLM call in this harness rides the Claude subscription via `claude --print` and no API key exists. Proposing a router would be fabricating a capability. The within-Claude analogue already exists as `better-call`, `multi-agent-brainstorming`, `validate-adversarial-agent`, and `codex-review`. The model-identity-masking trick is genuinely interesting and has no call site without multi-provider — recorded here rather than encoded. The "own your harness" thesis is what this repo already is; the cost-comparison discipline is `model-tiers` and `context-budget`.

---

## My agent.md to improve LLM-assisted code quality
**URL:** https://fabiensanglard.net/agent.md/index.html
**Added:** 2026-08-26
**Source / Author:** Fabien Sanglard, 2026-08-21. *(Submitted twice in the same batch; deduped.)*

**What it's about:** One engineer's hand-written `agent.md`, plus the reasoning for keeping it. Rules: minimal wording in anything a human reads; no flattery, "the cold hard truth"; extract recurring and spec-derived values into constants; reduce indentation via early returns and `continue`; function names under 30 characters; enums not booleans for parameters; blank lines between logical blocks; private by default, widening visibility is "a breaking design shift"; respect layer boundaries, never let UI call the database; always use braces; a seven-rule commit-message format; failing test first for bugfixes. Two context mitigations, citing *Lost in the Middle*: start a fresh session per feature, and tell the agent to "Reload agent.md" when quality slips. Maintenance tip: have the agent append its own new rules rather than editing by hand.

**What we added:** nothing.

**Rejected:** the rule set — ~90% covered by `CLAUDE.md`'s AI-Legible Code section plus `codebase-legibility`, `clean-code`, and `guardrails-agent`; porting it would add new *text*, not new *behavior*. The "Reload agent.md" drift mitigation — `address-check-hook.sh` is already precisely this mechanism (a Stop hook logging when the address convention goes missing as a passive signal that CLAUDE.md is being ignored), and `CLAUDE.md` already documents the self-compact response. Short sessions per feature — already the standing practice, and unenforceable from a hook. Agent-appends-its-own-rules — that is `evolve-agent` plus `learn-eval` plus `skill-augment-agent`, all of which already run under human approval, which is stricter than the article's suggestion.

See also: [git/README.md](../git/README.md) and [x/README.md](../x/README.md) — same 13-source sweep, 2026-08-26.

---

## Build Your Own AI Coding Agent Harness (Vercel Academy)
**URL:** https://vercel.com/academy/build-ai-agent-harness
**Added:** 2026-08-30
**Source / Author:** Vercel Academy — 38-lesson TypeScript course, 11 modules

**Fetch status:** partial. The index and lesson pages are readable as `<url>/<slug>.md`; 8 lessons were read in full. **Four returned "No response from model" and were never read** — `pruning-old-results`, `fast-context-understanding`, `structured-questions`, `cache-control`. Nothing below is credited to those four. `cache-control` is worth a retry.

**What it's about:** A hands-on course that builds a Claude-Code-shaped coding agent ("TeensyCode") from a bare chat loop to a production harness, on Vercel AI SDK + AI Gateway + Vercel Sandbox. Pedagogy is explicitly causal — each layer exists because the previous one broke something. Because it teaches how to *build* Claude Code, ~85% of it re-derives machinery this harness inherits for free.

**What we added:**
- Hook: `hooks/claude/todo-focus-hook.sh` (`PostToolUse`, matcher `TodoWrite`) — enforces one in-progress todo at a time. Native `TodoWrite` accepts any number of concurrent `in_progress` entries and enforces nothing; the course names the single-active constraint "the load-bearing part," because without it an agent starts everything and splits attention. Exits **2, not 0**, since `PostToolUse` stdout goes to the debug log and exit-0 stderr is never shown to Claude — exit 2 is the documented way to surface stderr from that event without blocking. Reads `.tool_input.todos[].status` with `jq`, satisfying the no-regex rule by consuming structured data at the source.
- Augmentation: `skills/tool-design` — the **six-section description skeleton** (opening line / WHEN TO USE / WHEN NOT TO USE / DO NOT USE FOR / USAGE / EXAMPLES) with the deliberately-repeated negative: a soft handoff ("prefer X") and a hard prohibition ("never use this for Y") are both required, because the soft form bends under ambiguity. Tier-sensitive in a useful way — smaller models drop the soft boundary, mid-tier models gain from the restatement, the largest are unaffected and unharmed, so the repetition costs nothing where unnecessary. Plus the named failure mode **"bash gravity"**: as tool count grows, routing collapses toward whatever is most general, because the general tool is never *wrong*, only worse.
- Augmentation: `skills/tool-design` — **output cap contract**: cap, announce the cut *and its true total*, offer the continuation. "Silent truncation is worse than none" because the model believes it has the full picture and acts on a false premise. For command output keep the **tail**, not the head — failures put their signal last. Genuinely absent before this: `tool-design`, `agent-tool-builder`, and `mcp-builder` had zero coverage of truncation, and the harness's own quality-gate hooks inject command output into context.
- Augmentation: `skills/skill-eval-gate` — per-section ablation as the description eval method (strip one section at a time against three fixed probes; the section whose removal first collapses routing is the one carrying the weight). Folded into `tool-design`'s verification note, since it tells you *which part* of a description works, which whole-artifact A/B cannot.

**Rejected:** the event bus and its five lifecycle events (Claude Code's native hooks are a 1:1 superset; `hook-design` frames the choice). `session_before_compact` instruction injection (`compaction-discipline-hook.sh` + `context-compression`). The approval-config discriminated union and safe-prefix bash allowlist (this is Claude Code's permission system re-derived; `skill-permissions-gate.sh`, `protected-path-hook.sh`, and `agent-permissions-design` already cover it). The skills system itself — `SKILL.md` + frontmatter, project-shadows-global, char caps — that *is* Claude Code. Per-role model tiers (`model-tiers`). Task-tool routing and spawn permissions (`multi-agent-patterns`, `dispatching-parallel-agents`, `agent-permissions-design`). The verification contract (`verification-before-completion` has a strictly stronger Iron Law). The hedged-future-tense confabulation tell — sharp, but automating it means regex-matching "should be fine" in prose, which this project bans; it stays a grading heuristic and that path already exists. All of Modules 4 and 7 (sandbox interface, state machine, snapshot/restore, five lifecycle gotchas) — excellent and entirely inapplicable to a harness running on a developer's own machine. The `--chaos` failure injector (nothing to inject into). Module 10 surfaces (Claude Code *is* the surface).

See also: [git/README.md](../git/README.md) and [x/README.md](../x/README.md) — same 8-source sweep, 2026-08-30.

---

## Audit Your Agent Files
**URL:** https://addyo.substack.com/p/audit-your-agent-files
**Added:** 2026-08-30
**Source / Author:** Addy Osmani, 2026-08-27 — 2,767 words, not paywalled (verified via the Substack API: `"audience":"everyone"`, `"meter_type":"none"`)

**What it's about:** Agent configuration "has a half-life" — models and harnesses improve while old instructions linger, so CLAUDE.md/AGENTS.md/skills need periodic re-justification rather than monotonic growth. Prose advocacy with cited studies, not a spec: no code, no checklist. Its sharpest claim is that must-always rules belong in tests, hooks, and permissions, not prose. Reported figures: lint leakage 62% / context bloat 42% / skill leakage 35% across 100 popular repos; Anthropic cutting >80% of Claude Code's system prompt for the Claude 5 generation with no measurable eval loss; prose summaries answering 4/45 behavioral code questions vs 27/45 from source.

**What we added:**
- Script: `scripts/skill-listing-budget.py` + augmentation to `skills/skill-curator` Phase 2 — an **aggregate ceiling** for the skill listing, which the article supplies (~1% of the context window) and `skill-curator` provably lacked. Curator scored each description individually (>150 ⚠️, >200 🔴) and printed a total against nothing, so the aggregate was decorative: every skill can pass and the sum still be far over. First run here: **995 skills, ~42.2k tokens — 21.1× over on a 200k window, 4.22× on 1M.** The skill now instructs reading the *ratio*, and routes anything above ~2× to the deprecate/archive passes rather than proposing 200 description rewrites, since at that point the driver is skill count, not description length. The script also surfaced 4 skills with **no description at all** — strictly worse than a long one, since they hold a listing slot while giving the router nothing to match on.
- Augmentation: `skills/claudemd-review` — **unenforced-MUST promotion**. The existing Over-Constraining check runs one direction only (flag prose that duplicates a hook); this adds the other: for each MUST/NEVER/ALWAYS, find what mechanically enforces it, and where nothing does, propose the concrete artifact. A hard rule backed only by prose holds while context is short and attention is on it, and stops holding under long sessions, compaction, and subagents — exactly when it mattered.
- Augmentation: `skills/claudemd-review` — **three-axis leakage** (lint config / README + manifests / SKILL.md bodies). The review previously checked duplication against hooks only. Lint leakage is the most common of the three and near-mechanical to check.

**Rejected:** `/doctor` on a cadence — `claudemd-review` already fires bi-weekly off `session-start-hook.sh` with a stamp file, and `/doctor` is an Anthropic built-in, not a harness artifact. Test-removal ablation — `claudemd-review` Phase 2b already does it *better* (N=2 baseline/treatment, haiku tier, deterministic check, hard 3-candidate/6-call ceiling, three-way verdict); the article's "delete it and see" is the naive version. The 200-line cap, "short decision guide not knowledge base", over-long examples, "keep to what the model can't infer" — all covered with tighter budgets by `instruction-architecture` (SNR > 0.8, <20% applicability → evict). Reflexive rule-addition after every error — named verbatim in `instruction-architecture` as the "Reactive rule-addition" anti-pattern. Archive-before-delete (version control is the archive; `skill-curator` Phase 5 already gates deletion behind explicit confirmation). Skill sprawl / "250 skills cut to 25" — `skill-curator` Phase 3 already uses real invocation data from `logs/skill-usage.jsonl` plus a dependency ground-truth map, which beats vibes-based pruning.

See also: [git/README.md](../git/README.md) and [x/README.md](../x/README.md) — same 8-source sweep, 2026-08-30.

---

## The New Rules of Context Engineering for Claude 5 Generation Models
**URL:** https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models
**Added:** 2026-09-01
**Source / Author:** Thariq Shihipar, member of technical staff, Anthropic — 2026-07-24

**What it's about:** Anthropic removed "over 80% of Claude Code's system prompt" for Opus 5 / Fable 5 "with no measurable loss" on their coding evals. The diagnosis is **overconstraint, not verbosity**: transcripts showed "several conflicting messages in a single request like 'leave documentation as appropriate,' or 'DO NOT add comments'" arising among system prompt, skills, and user requests. The cost of a guardrail is now paid in arbitration on every turn. Six then/now pairs: rules→judgement, examples→interfaces, upfront→progressive disclosure, repeat-yourself→simple tool descriptions, CLAUDE.md memory→auto-memory, simple specs→rich references. The only figure in the post is the 80%; it gives **no** line cap or token budget for CLAUDE.md or skills.

**Already logged once:** [x/README.md](../x/README.md) (2026-07-30) captured this same article from pasted text and shipped the `claudemd-review` Conflicting Instructions rubric row and the rich-references clause in `templates/CLAUDE.md.template:18`. The 80% figure arrived a second time via Osmani on 2026-08-30 (entry above). This entry exists because re-reading it *firsthand against the harness* found contradictions the content-level pass could not — 9 of 12 ideas were already covered, and the yield was entirely self-audit.

**What we added:**
- Config/edit: **de-duplicated the lean-ctx tool mapping.** The same native→`ctx_*` table was resident in four always-loaded surfaces at once — `~/.claude/CLAUDE.md`, `rules/lean-ctx.md`, the lean-ctx MCP server's own `instructions` block, and `hooks/claude/subagent-context-hook.sh` (multiplied by every spawn). The MCP server states it at the tool layer, which is where the post says it belongs. `rules/lean-ctx.md` now points at the server instead of restating it, and the hook keeps one line carrying the single fact the server *cannot* supply: native Grep/Glob are policy-denied here. The hook's own header already warned "Keep the injected text SHORT… its cost is multiplied by spawn count."
- Augmentation: **one blast-radius rule with explicit precedence**, replacing three competing MUSTs that never arbitrated (`CLAUDE.md` GitNexus "NEVER edit… without first running `impact`", `rules/lean-ctx.md` Risk Gate `ctx_callgraph`, `CLAUDE.md` Serena `find_referencing_symbols`, plus a fourth restatement in the subagent hook). Editing an exported Python function fired all three. New ordered rule: Serena for Python symbols → GitNexus otherwise → `ctx_callgraph` when the index is broken or the edit is not symbol-shaped. Written into `CLAUDE.md` **above** the auto-generated `<!-- gitnexus:start -->` block (which is regenerated on re-index and would clobber an edit made inside it), `rules/lean-ctx.md`, and the hook. Critically it adds a **stated fallback**: the GitNexus MUST was *unsatisfiable* at the time of writing — every call returns `Database file version: 42, Current build storage version: 40` — and a NEVER that cannot be complied with trains the model to discount every other NEVER.
- Augmentation: `skills/claudemd-review` — **hook-injected context is now in scope for the Conflicting Instructions check.** `SKILL.md:63` previously scoped it to "CLAUDE.md/skills/system prompt" and `:150` explicitly fenced hooks out of the ablation phase. But any hook emitting `hookSpecificOutput.additionalContext` is a first-class always-resident instruction surface, and for a subagent it is the *only* one — CLAUDE.md and `.claude/rules/` never reach a spawned agent. Both findings above lived partly inside `subagent-context-hook.sh`, so the bi-weekly review was structurally incapable of finding them. Adds two named failure modes (duplicate statement; unsatisfiable MUST) and preserves the `:150` ablation fence — hooks are read as inputs, never edited by an experiment.
- Regression guards: `hooks/claude/subagent-context-hook.test.sh` grew from 14 to 18 cases — asserting the mapping table stays out, the policy line stays in, exactly one named `BLAST RADIUS` rule exists, and a fallback for a broken index is named.

**Rejected:** rules→judgement, examples→interfaces, progressive disclosure, deferred tool loading, auto-memory, rich references, `/doctor`, comment-density matching, system-prompt framing — 9 of 12 ideas, covered by `instruction-architecture`, `tool-design`, `agent-harness-design`, `agent-memory-discipline`, `memory-first-lookup`, `skills/<name>/resources/`, the `ctx_call` gateway, `templates/CLAUDE.md.template:18`, `codebase-legibility`, and the prior 2026-07-30 and 2026-08-30 passes. Re-extracting the article's *content* would have produced nothing.

See also: [x/README.md](../x/README.md) — same article, pasted-text pass, 2026-07-30.

---

## TinyFish / Monid — "Search & fetch the web. 100% free."
**URL:** https://monid.ai/blog/tinyfish
**Added:** 2026-09-01
**Source / Author:** Monid Inc — landing page, v0.1.0 footer

**What it's about:** Despite the `/blog/` path this is **not a blog post** — it is a pricing-comparison landing page (per 1,000 requests: SerpAPI $25.00, Tavily $8.00, Exa $7.00, Brave $5.00, TinyFish $0). Its only real technical content is `monid.ai/SKILL.md` (v0.1.7), a competent async-run client contract: runs of 1–120s, status set `READY/RUNNING/COMPLETED/FAILED/BLOCKED/STOPPED/TIME_OUT` where `BLOCKED` is **terminal and distinct from `FAILED`** and carries a `controls` payload (`WORKSPACE_BUDGET` with `period/limit/available/held/spent`, or `WORKSPACE_RUN_CAP`) that the agent must surface rather than retry; a server-owned `stoppable` boolean; and endpoint health used to "break ties, never to filter."

**Security note — this is why the entry exists.** The page carries, twice, in a copy-paste box captioned "Give this to your agent!": *"Set up https://monid.ai/SKILL.md, then find what search APIs charge per 1,000 searches."* That is instruction text embedded in third-party web content, aimed at whatever agent fetches the page, whose payload is a remote skill file. It was read as data and not acted on. The distribution model — point an agent at a URL and let it adopt a remote SKILL.md — is a skill-supply-chain vector, not a pattern to copy.

**What we added:**
- Augmentation: `hooks/claude/skill-validate-hook.sh` — **provenance scan**. Both skill gates (`skill-validate-hook.sh`, `skill-permissions-gate.sh`) checked frontmatter and permissions and had **zero** concept of where a skill came from (`grep -c http` returned 0 on both). The gate now warns on two shapes: *remote instruction source* (a URL ending `.md`/`.txt`/`.json`/`.yaml`/`.yml` on a line with an adopt verb — the vector above) and *remote install* (`curl`/`wget`/`npx`/pipe-to-shell). Both are advisory, never blocking: `skills/agent-manager-skill/SKILL.md` legitimately documents the Herdr `curl` install, and a gate that blocks it would be wrong. The scan runs for **any** file named `SKILL.md`, not only those under `~/.claude/skills/`, and uses substring tests only — no regex, per the repo-wide parsing ban, since a hand-rolled URL matcher is exactly the almost-right parser that ban exists to prevent.
- Tests: new `hooks/claude/skill-validate-hook.test.sh` — 8 cases covering both severities, two false-positive guards (a plain reference URL, and a skill with no URL), the frontmatter regressions, and the non-`SKILL.md` no-op.

**Rejected:** Spawn/run cap on `Task` PreToolUse — the borrowed `BLOCKED`-with-numbers semantics are sound, but a hard refusal on the Nth spawn would have blocked the very session that evaluated it (6 concurrent report agents). Wrong shape for this user's usage. Server-owned `stoppable` — Claude Code exposes no subagent cancellation API; nothing to attach it to. Health-as-tiebreaker — already implemented as `tool-failure-capture.sh` + `tool-failure-recall.sh` (recall, never suppression). Server-supplied `hints` — hook `additionalContext` is the same pattern, already shipping. Conditional-fetch monitoring — would require building a source poller that does not exist, to obtain the cheap-skip. All TinyFish corporate figures (30M+ monthly operations, 99.99% reliability, 81% vs 43% on Mind2Web, $47M raise) are vendor self-reported and belong nowhere in the harness.

---

## Maximizing the Value of Your Claude Code Sessions
**URL:** https://claude.com/blog/maximizing-the-value-of-your-claude-code-sessions
**Added:** 2026-09-01
**Source / Author:** Lydia Hallie, 2026-08-14 — reached via a LinkedIn post (`linkedin.com/posts/…-7499166569458126848-bscS`) whose four bullets contain no figures at all; the outbound `lnkd.in` shortlink is where every number below lives. Logged against the guide, not the post.

**What it's about:** Cache mechanics as the dominant lever on Claude Code session cost. Stated ratios: output ≈5× input, **cache reads 0.1×** input, **cache writes up to 2×** input — a write happens once, the 0.1× reads recur every turn. The cache prefix order is fixed (tool definitions → system prompt → conversation, with `CLAUDE.md` first) and any change to a prefix forces a full re-prefill. Named cache busters: `/model` (per-model caches, including `opusplan` which swaps models entering/leaving plan mode), `/effort` (part of the cache key — which is why it asks for confirmation), fast mode (part of the key; re-prefill billed at fast-mode prices, though turning it *off* is free), `/compact`, and time. Cache TTL is **one hour on subscription, five minutes on API key**; "resumed sessions almost always miss." Bash output above **30,000 characters** spills to a file with only a preview inline (`BASH_MAX_OUTPUT_LENGTH` tunes it) — and the stated real hazard is output *just under* the limit, e.g. 400 passing tests printed line-by-line, which then persists through every remaining turn.

**What we added:**
- Augmentation: `scripts/utils/token-forensics.py` — **cost weighting**. The script printed `cache read` and `cache create` as raw counts and ranked on unweighted totals, so a million cache reads outranked 100k output tokens despite costing half as much. Adds `COST_WEIGHTS`, a `weighted_cost()` in input-token-equivalents, a per-class percentage-of-cost breakdown, and a callout when cache *creation* dominates (the signature of sessions too short to amortize their own prefix). On the first real run over 7 days: cache_read is 96% of tokens but **52.8%** of cost, while cache_create is 3% of tokens and **36.2%** of cost — a ranking the raw counts actively hid.
- Augmentation: `scripts/utils/token-forensics.py` — **cache-bust detection**. The script read `cache_creation_input_tokens` but never `message.model`, so a mid-session model switch and the re-prefill it caused were both invisible. Now tracks model per request and reports mid-session switches with the following re-prefill size. Two honesty constraints are built in and stated in the output: transcripts record only the *model* half (effort and fast-mode switches leave no trace), so the count is labelled a **floor, not a total**; and `<synthetic>` placeholder model names are excluded — including them produced **4 spurious busts out of 5** on the first real run, every one with 0 re-prefill.
- Tests: new `scripts/utils/token-forensics.test.sh`, 14 cases. Written specifically because a detector reporting `0` is ambiguous between "nothing happened" and "never fires" — it plants a known opus→haiku switch to prove a real positive, and plants `<synthetic>` lines to prove the filter. Building it caught a real error in the *test's* own arithmetic: two planted lines shared a `requestId` and were correctly deduplicated by the tool, so the expected total was wrong, not the code.
- Config: `templates/settings.json.template` and `templates/settings.harness.json.template` — new `env` block setting `BASH_MAX_OUTPUT_LENGTH` to `15000`, half the 30,000 default. Neither template had an `env` block. This targets the article's named hazard: output that sits just under the threshold is never spilled to a file and instead persists in context for every remaining turn of the session.

**Rejected:** the LinkedIn post itself — four generic bullets, zero figures; extracting from it alone would have been worthless. Just-under-threshold output detection — `token-forensics.py`'s existing amplified-cost ranking already surfaces these and is a better metric. `MAX_THINKING_TOKENS=0` for `scripts/routines/` — degrades exactly the judgment-heavy jobs (health checks, audits) it would be pointed at; risk exceeds saving. `@`-mention-once — contradicts `.claude/rules/lean-ctx.md`, which mandates `ctx_read`. `/rewind` over `/compact`, and compact-before-idle — real advice, but a human judgment call with no mechanism to attach to; the PreCompact hook fires *during* compaction, too late to influence when you compact. `/context` audit, MCP pruning, CLAUDE.md→skills migration — covered by `commands/kiro/context-budget.md`, `scripts/skill-listing-budget.py`, `skills/claudemd-review`. Long-vs-short session guidance — `skills/auditing-token-spend/SKILL.md` Phase 3 already states it better ("High cache-create vs cache-read → sessions too short to amortize the prompt"). `/autocompact 200k` and `/loop`-from-fresh-session — version-pinned to v2.1.221+ and niche; staleness cost exceeds value. Subagent `model: inherit` audit — measured **25 of 39 agents on inherit, 9 haiku, 3 sonnet, 2 with no `model:` field**, which is a real finding, but deferred: it belongs in `harness-validate-agent` as a check rather than as its own artifact, and was not in the approved scope.

See also: [papers/README.md](../papers/README.md) — same 6-source batch, 2026-09-01.

---

## Herdr Crash Course — Step-by-Step Setup
**URL:** https://www.youtube.com/watch?v=Ct-mtWqV3Ro
**Added:** 2026-09-01
**Source / Author:** Alejandro AO (@alejandro_ao) — video, transcript retrieved with `uvx --from youtube-transcript-api youtube_transcript_api Ct-mtWqV3Ro --languages en` (3,081 words)

**What it's about:** A product tour of Herdr, a terminal multiplexer built for coding agents: workspaces → tabs → panes, agent auto-detection, a colour-coded status bar (**yellow = working, blue = done**) aggregated across workspaces, desktop notification on completion (**off by default**), tmux-style `Ctrl-B` prefix so unprefixed keys pass through to the agent, and `herdr --remote <ssh-target>` which renders the TUI locally while running every process on a VPS. Recommended layout: one workspace per project, one tab per process. Herdr also exposes its own API as a CLI so an agent can drive Herdr itself.

**Content verdict: superseded.** The harness had already mined Herdr's actual repo on 2026-08-16 — see [git/README.md](../git/README.md) — into `skills/agent-manager-skill/SKILL.md`, which is a strict superset of the video's agent-control material and carries the `HERDR_ENV=1` gate and the "never close panes you didn't create" rule the video omits. All nine transferable ideas were rejected on that basis. This entry exists for a different reason.

**What we added:** the video is what identified Herdr as the right **backend for the dashboard herder** the user asked for — spawning agents and sessions from inside the dashboard. The originally proposed design was a bespoke process manager built on `subprocess.Popen(["claude","--print",…])`; probing Herdr instead showed it is strictly better and smaller, so that design was scrapped before it was written.
- Script: `scripts/utils/herder.py` — JSON client over the Herdr socket API (`status`/`list`/`spawn`/`prompt`/`read`/`stop`). `herdr server` runs headless with no TTY, so the dashboard can drive it directly.
- Dashboard: new **Herder** section in `scripts/utils/dashboard.py` — agent-kind × model × permission-mode × prompt spawn form, plus a **responsive grid of agent cards** (`auto-fill, minmax(290px, 1fr)` — three or more per row, wrapping), each with its own status badge, token/cost figures, a one-line "what it is doing right now", and stop. The feed reads that agent's **transcript** — not the raw terminal — because the pane is a rendered TUI whose text would have to be pattern-matched back into meaning while the transcript already carries the events as data; the raw pane stays as the escape hatch. Cards rebuild only when the agent set or a status changes, so an open card is not collapsed under you and a feed scrolled back is not yanked to the bottom. **Revised 2026-09-06:** the collapsed `activity` disclosure and the `window.prompt` popup were replaced by an always-visible **chat box** per card — message feed, reply `textarea` (Enter sends, Shift+Enter newlines), 📎 `@filename` tag chips appended to the outgoing prompt, and `send`; the raw pane moved into the collapsed disclosure. The chat renders assistant `text` events only, so reasoning and tool calls no longer appear colour-coded in the feed, and with **live tail** on every card's feed refreshes on the 5s poll rather than only an expanded one. The **target repo is the dashboard's existing top-level repo dropdown**; the herder deliberately has no repo picker of its own, since two selectors for one concept is a thing you get wrong at 1am.
- Discovery, not hardcoding: `GET /api/herder-options?kind=` serves the model and permission-mode lists pulled from the selected agent itself — modes from the agent CLI's own enum validator, models from the pricing catalogue the dashboard already refreshes. Both report whether they were really discovered, and the form says so when a list is a fallback.
- Attribution: herder-spawned sessions are ordinary Claude Code sessions, so `scripts/utils/token-forensics.py` and the full harness hook stack already apply to them — **verified**, not assumed: `caveman-statusline.sh` wrote live context state for both spawned repos, and token-forensics counted their transcripts. A per-agent ledger then maps each agent to its transcript so its individual spend is reportable. That mapping took three attempts and the first two failed *live*, which is worth recording: resolving by "newest transcript modified after the spawn" credited a fresh agent with **94,259,775 tokens** belonging to the interactive session that spawned it (an open session in the target repo is always the newest file); resolving by "newest transcript absent before the spawn" then made three agents started seconds apart each claim a sibling's transcript. The working rule is to read the name the session records for itself — `--name herder:<label>` lands in the transcript as typed `agentName`/`customTitle` fields — and to re-validate a stored id against that tag on every read, since the ledger is a shared file that any process running older code rewrites on every poll.
- Endpoints: `GET /api/herder-status|herder-list|herder-read`, `POST /api/herder-spawn|herder-stop|herder-prompt`.
- Security: herder endpoints require a per-process token embedded in the served page **and** pass an Origin allowlist, and send no CORS header. The pre-existing endpoints all sent `Access-Control-Allow-Origin: *`, which is survivable for fixed-prompt actions and would have been remote code execution for a free-text spawn endpoint — so the wildcard was removed from those five as well. Default permission mode is `acceptEdits`, with `bypassPermissions` behind an explicitly-labelled checkbox.
- Fix: `dashboard.py` gained the `--port` flag its own module docstring had advertised since the file was written but argparse never defined, so passing it was an error and the port was effectively hardcoded.
- Tests: `scripts/utils/herder.test.sh` — 19 offline cases including the full endpoint authorization matrix (no token / wrong token / valid token / foreign Origin / same Origin / no-CORS), plus an opt-in `HERDER_LIVE=1` tier that really spawns and stops a session.

**Verified end to end, not asserted:** a session was spawned through the HTTP API into `whisper-pipeline` and a second into `kakophonia` concurrently, both appeared in the roster as real interactive Claude Code panes (`agent_status: idle`, `interactive_ready: true`), and both were stopped cleanly. A third spawn into a repo Claude Code had never been opened in failed with `agent_not_ready` — Claude stops on its first-run trust prompt — which is documented as a known limitation rather than worked around.

**Rejected from the video itself:** all nine ideas (agent-driven multiplexer control, lifecycle polling, `--remote` split-plane, `session list`, detach-and-survive, completion notification, workspace-per-project layout, prefix-key namespacing, agent auto-registration) — covered by `skills/agent-manager-skill/SKILL.md`, `hooks/claude/stop-hook.sh`, `skills/dispatching-parallel-agents`, `skills/using-git-worktrees`, or inapplicable to a tool the harness does not ship. The `tau` agent promotion that fills much of the runtime is off-topic.

See also: [git/README.md](../git/README.md) — the `herdrdev/herdr` repo entry, 2026-08-16, which is where the content was actually extracted from.

---

## Six-source sweep — agentic testing, software factories, AI-slop thresholds, writing rules
**URLs:**
- https://theaiengineer.substack.com/p/what-is-agentic-testing-fa2 — Paolo Perrone, *The AI Engineer*, 2026-09-02
- https://www.youtube.com/watch?v=ZDOTYfJBuLw — "I Built A Self-Improving AI Software Factory" (channel and upload date **not retrieved** — unknown, not absent)
- https://archive.codenewsletter.ai/2094457600259842065 — "The Complete Guide to pstack Pt. 1", @poteto, 2026-08-31
- https://archive.codenewsletter.ai/2093388974194872781 — "AI Engineering Skills Map", Andrew Ng, 2026-08-28
- https://archive.codenewsletter.ai/2094127829419995613 — @QuentinCody on cleaning up AI slop, 2026-08-30
- https://clarity.addy.ie/ — Clarity, Addy Osmani

**Added:** 2026-09-03

**Retrieval:** five via WebFetch (rung 1); the video via `uvx --from youtube-transcript-api youtube_transcript_api ZDOTYfJBuLw --languages en` (rung 4, 3,138 words) after WebFetch returned only page chrome. The two `archive.codenewsletter.ai` entries are X-post mirrors whose full articles sit behind links on X that were not followed — those two are **partial**. Everything except the video transcript was condensed by WebFetch's summarizer rather than read verbatim, so the third-party figures below (TestGen-LLM, Uber, Airbnb) are reported-by-summarizer, one step removed.

**What they're about:** Perrone defines agentic testing as specifying an outcome instead of a step sequence, and separates a test's frozen *locator* from its frozen *oracle*; his measurement argument is that `pass^k` (all of k runs passed) is the only honest gate, since "a gate that calls a test green after two of its three runs failed is not a gate." The video describes a "software factory" — control plane plus worker data plane, one fixed foreman prompt per task, metrics recorded per run — whose stated value is that a frozen prompt makes run-to-run deltas readable. Ng argues fundamentals matter for steering an agent's tradeoffs. Cody lists numeric quality thresholds for AI-written code. Addy Osmani's Clarity is 18 writing rules, distributed as an installable Agent Skill.

**What we added:**
- Augmentation: `skills/skill-eval-gate/SKILL.md` — treatment arm now runs **k=3 with `pass^3`**; baseline stays at 1 run, because Phase 1b already disqualifies any scenario a strong baseline passes. Phase 4's table shows the 3 runs individually and Phase 5 gains an **INCONCLUSIVE** verdict for split runs (2/3 or 1/3), which is the honest reading of a skill whose effect is inside the noise floor and which previously had nowhere to land. Cost 6 → 12 spawns; the skill now says to cut scenarios rather than runs.
- Augmentation: `scripts/session/trust_score.py` — `apply --delta` is repeatable, applies the **median**, and records the day `inconclusive` with delta `0.0` when samples spread beyond `JUDGE_SPREAD_LIMIT` (2.0 on ±4.5). Raw `samples`/`spread`/`inconclusive` persist to `trust-score.jsonl`. One sample reports `"spread": null`, not `0.0` — unsampled and "sampled and agreed" are different facts. `auto-score` is untouched and deliberately single-sample: it counts tags deterministically, so there is nothing for a spread gate to measure. New: `scripts/session/trust_score.test.sh` (24 cases).
- Augmentation: `commands/kiro/daily-maintenance.md` — Step 1 spawns `session-judge` **3× concurrently**, Step 4 passes all three deltas. A failed run is treated as *missing*, never as a vote for zero, since substituting 0 for a crashed judge manufactures agreement out of a failure.
- Augmentation: `agents/kiro/session-judge.md` — new **sample mode**. This was a live conflict, not a hypothetical: the agent's own duplicate-run rule made runs 2 and 3 no-op against run 1's `[judge]` observation, which would have silently turned three draws into `[-2, 0, 0]`. Sample mode suppresses the append (the caller writes one line) and suspends the duplicate guard.
- Augmentation: `scripts/utils/dashboard.py` — the existing Trust Battery tile gained a `⚠ judge split` badge with the raw samples, and a quiet `median of N runs · spread X` line otherwise. Records predating this change render no badge at all.
- Augmentation: `agents/kiro/guardrails-agent.md` — new **Assertion Strength** audit axis (mutation testing: mutmut/cosmic-ray, Stryker, gremlins, cargo-mutants, PIT), reported on its own line and never folded into the complexity ratio. WARN-level, recommend-only, explicitly not wired into any per-write gate — a minutes-to-hours check on a write hook is a check that gets bypassed. Two sources converge here: Cody's "surviving mutants: 0" and Perrone's point that generated tests look handwritten and get waved through, so an assertion that a build *started* rather than *passed* reads fine in a diff.

**Rejected — Cody's thresholds, itemised.** Most are *looser* than what `guardrails-agent` already enforces: cyclomatic <22 and cognitive <22 (incumbent baselines are 10), LOC/file <500 (incumbent caps functions at 40 lines), zero `any`/`unknown` (already covered, more precisely, by the anti-slop type-evidence rules). Halstead <80 and CRAP <25 are round numbers with no tooling story, and CRAP derives from two metrics already covered. **Test coverage 100% was rejected as actively harmful** — it is precisely what makes assertion-less tests attractive, which is the gap the one adopted item closes.

**Rejected — everything else.** Feature Maps (GitNexus already computes this: 48,505 symbols, 290 execution flows; a hand-maintained index would drift against a generated one). "Build the Lever" — prefer a CLI over markdown — a hollow addition, since it is already the harness's practice and three of the then-current hot-memory priorities were instances of it; noted as two-source validation of an existing choice, not a new artifact. Verification skills as critical infrastructure and daily `/maintain-verification-skill` (covered by `verification-skill-authoring`, `verification-before-completion`, and `skill-augment-agent` on the daily tick). Cloud agents over worktrees (`scripts/utils/herder.py` + Herdr already goes further). The software-factory control/data-plane architecture — the harness *is* this, and the video's own closing insight, "replace the agent step with a deterministic script where the step is fixed", was already the standing priority list. Ng's five skill areas — pure conceptual taxonomy, no mechanism, no trigger, no threshold. Clarity's 18 rules — the writing domain is saturated here (`ai-writing-guard-hook.sh` hard-denies, plus `beautiful-prose`, `copy-editing`, `writing-skills`, `doc-coauthoring`), and it ships as an upstream skill (`npx skills add addyosmani/clarity`), so vendoring a copy would fork a maintained artifact; of the 18, only rules 6 and 10 are gate-shaped and neither is mechanizable in a deny-list hook without an LLM call per write. Silent-skip detection for agentic test repair — already signal class 1 of `test-integrity-guard.sh`.

**Deferred, not rejected:** prompt-version tagging in `metrics.jsonl`. The video's metrics are comparable *because* the foreman prompt is frozen, whereas `skill-augment-agent` edits harness agent definitions daily — so the trust-score series compares readings taken under changing treatments. A real confound, and `--meta` would carry an agent-definition SHA with no schema change. Held back on scope: it is a measurement-methodology decision of its own, and the median-of-k change above already addresses the larger of the two errors in the same metric.

See also: [git/README.md](../git/README.md) — Visa VVAH, same 7-source batch, 2026-09-03.

---

## Software factory, drawably, Baseten inference frontier, Kilo Code, Han Xiao on `pi` (5-source batch)
**Sources:**
- https://www.youtube.com/watch?v=blI10_91xgA — "I built my own software factory (it's not what you think)" (channel not captured; see Retrieval)
- https://archive.codenewsletter.ai/2094519020531994639 — Han Xiao (@hxiao) on driving `pi` for 6h+ agent tasks, 2026-08-31
- https://www.baseten.co/blog/the-efficient-frontier-of-llm-inference/ — Philip Kiely, Baseten
- https://github.com/Kilo-Org/kilocode — Kilo Code, MIT, ~27.2k stars
- https://www.drawably.dev/ — Drawably, hand-drawn UI library, MIT, v0.3.10

**Added:** 2026-09-03

**Retrieval:** video transcript via `ctx_url_read(mode="transcript")` (~4.3k tokens, truncated at the tail; the full argument arc was captured). Han Xiao post, Baseten, Drawably, and the kilocode README all via WebFetch rung 1. **kilocode took two passes.** `kilo.ai/docs` returned a navigation index with no substance and `/docs/code-with-ai/memory-bank` returned **404**, so the first pass rejected Memory Bank and Orchestrator on incumbent coverage without their content — an argument from absence. Second pass went to the source instead of the docs site: `gh api search/code` then `gh api .../contents/packages/kilo-docs/pages/` (rung 3), which holds the same docs as markdown. Both features turned out to be deprecated upstream; see the rejection below. **Retrieval is now complete for all five sources.** The Han Xiao link arrived labelled as a newsletter issue about Anthropic pricing; it is neither — it is a single archived X post about `pi`. Logged here rather than in `x/` per the precedent set by the two `archive.codenewsletter.ai` mirrors in the 2026-09-03 batch above.

**What they're about:** The video describes a harness-agnostic "software factory": every task starts in a fresh worktree, code follows a fixed structure, changes are proved with before/after runtime evidence recorded into the PR, and the PR loop runs until a review bot returns its top confidence score with zero unresolved comments — the author's point being that he merges on the reviewer's verdict, not the agent's. Han Xiao argues for keeping an agent wrapper minimal ("no double-dip" — programming `pi` via another agent produces redundant glue), and reports compaction taking progressively longer on long tasks. Kiely frames inference tuning as an efficient frontier that is "very jagged," with cutoffs that "must be discovered empirically through sweeps." Kilo Code is an open-source multi-surface coding agent with switchable built-in agents and a CLI forked from OpenCode. Drawably renders sketch-style UI controls, re-drawn per mount.

**What we added:**
- Hook: `hooks/claude/pr-evidence-hook.sh` (PreToolUse, matcher `Bash`) — tokenizes the command with `shlex`, matches the `gh pr create` verb structurally, resolves the body from `--body`/`-b`/`--body-file`/`-F`, and nudges when there is no literal `## Evidence` heading, or no inspectable body at all (`--fill`, bare `create`). **Soft — always exits 0.** A hard block would force fabricated evidence onto docs-only PRs, which is worse than none. Registered in both settings templates and in the live `.claude/settings.json` (41 → 42 registrations, verified by parsing the file, and the wiring proved live by sentinel). Tests: `pr-evidence-hook.test.sh`, 36 cases, asserting on emitted text rather than exit code — a soft gate's exit code is constant, so an exit-code test would pass no matter what the hook did.
- Augmentation: `skills/create-pr/SKILL.md` — new "Attach Runtime Evidence" step with a form table (visible surface → screenshot/video; no visible surface → same command's failing then passing output; genuinely nothing → say so). Two rules carry the weight: **use the same probe both times**, and **capture the before-state while reproducing, before fixing** — after the fix it costs a revert, so it gets skipped and replaced by the old behavior recalled from memory and presented as an observation. Also draws the line against the skill's own standing "no test plan" rule: a plan is intent, evidence is a result.
- Augmentation: `skills/iterate-pr/SKILL.md` — the loop's exit condition moves from "CI green + feedback addressed" to "CI green + **the reviewer re-ran after your last push and came back clean**." Reads unresolved non-outdated threads via a `gh api graphql` query rather than naming a vendor, so it works with whatever reviewer a repo runs; a numeric score (Greptile's n/5) is treated as one such verdict, not the mechanism. Adds a 5-round cap that escalates, and refuses two failure modes: a reviewer verdict predating the last push, and self-resolving threads to reach zero.

**Why the PR pair and not the rest.** "Feedback addressed" was the last loop in this harness still terminating on the agent's own assessment, which sits badly beside `session-judge` (independent adversarial scorer) and `skill-eval-gate` (measured lift, not self-assessed confidence). `verification-before-completion` already demands evidence for claims made *in conversation*, but a grep confirmed `create-pr`, `iterate-pr`, and `pr-babysit` contained **zero** references to evidence artifacts — the evidence stopped at the PR boundary. On the SkillsBench trade-off, both items target agent-verification honesty rather than adding another generic software-engineering skill to the crowded, low-yield end.

- Augmentation: `scripts/pr/detect_base_and_create.sh` — closes the auto-create gap. PRs opened by the push-triggered path call `gh pr create` inside the script, so no PreToolUse event fires and `pr-evidence-hook.sh` never sees them. The script now writes the `## Evidence` section itself, as an explicit "not captured — opened automatically on push" placeholder to be replaced before the PR leaves draft. It is deliberately *not* a real evidence section: no probe ran on that path, and generating one would be the failure the hook exists to prevent. Also stops parsing the PR number out of the printed URL (`grep -oE '[0-9]+$'`) and asks `gh pr list --json number` for it instead — the URL is free text, the number is a field. Tests: `scripts/pr/detect_base_and_create.test.sh`, 16 cases, `gh` stubbed in a throwaway git tree so the composed body is inspected without opening a real PR.

**Doc error found and fixed en route:** `scripts/README.md` described `iterate-pr` as "retired," replaced by `pr-babysit`. It is not retired — `pr-babysit` names it and delegates its triage steps to it explicitly ("Don't duplicate its triage logic"). Had that line been believed, augmentation #2 above would have been aimed at a dead artifact.

**Rejected — Drawably and Baseten, entirely.** Drawably has no harness surface. Baseten's domain does not apply: this harness serves no models, it shells to `claude --print`. Its one transferable principle — jagged frontier, discover cutoffs by sweep rather than assuming smooth curves — is *already* the median-of-k / `pass^k` decision recorded in the batch above, so adopting it would restate a standing decision as a new one.

**Rejected — Kilo Code.** `kilo run --auto`, the built-in agent set, the MCP marketplace, and Kilo Gateway are product features of a competing platform, covered by `behavioral-modes`, plan mode, `agent-execution-control`, and the existing MCP stack. Memory Bank and Orchestrator were initially rejected on incumbent coverage while their mechanics were unretrieved (the docs site 404s); that was the batch's one rejection arguing from absence, so the docs were then pulled from the repo itself via `gh api` — `packages/kilo-docs/pages/`. **Both features are deprecated by their own authors.** Memory Bank: "deprecated in favor of AGENTS.md," with `.kilo/rules/memory-bank/` content to be migrated into `AGENTS.md`. Orchestrator mode: "deprecated, will be removed in a future release — agents with full tool access can now delegate subagents automatically" via a `task` tool into isolated contexts, which is what `subagent-driven-development` and the Agent tool already do here. The rejection now rests on retrieved fact rather than coverage: adopting either would have vendored a pattern upstream is removing.

**Rejected — Han Xiao's post.** "No double-dip / keep the wrapper lean" is `lean-ctx`'s existing thesis restated: new text, no new behavior. The `pi/models.json` failure modes (65K context ceiling causing needless compaction; omitted image modality causing a shell-out to tesseract) are `pi`-specific config with no Claude Code analogue. The compaction-duration-growth metric is a real signal but was not worth its maintenance cost here: the loop has been idle since 2026-09-01, the standing priorities are the no-regex ledger and `[loop-debt]`, `compaction-discipline-hook.sh` (PreCompact) already occupies that slot, and the observation describes `pi`'s compactor rather than Claude Code's.

**Rejected — from the video.** Worktree-per-task isolation is covered by `using-git-worktrees` plus the native `EnterWorktree`/`ExitWorktree` tools. The service-layer `code-structure` skill was rejected twice over: the software-engineering domain is saturated (SkillsBench, 4.5pt lift vs 51.9pt in model-weak domains), and its "shared services own the reusable how" **directly contradicts a standing project rule** — CLAUDE.md mandates vertical slices and bans `shared/`, `utils/`, and `common/` under the Rule of Three. Adopting it would have put the harness in conflict with itself. See also the same-day rejection of a different video's control/data-plane architecture in the batch above; the two videos overlap in theme, and only the evidence and reviewer-verdict mechanisms survived either pass.

---

## Six-source batch — funes, DX Core 4, three X-archive posts, and a "19 Claude Code mistakes" video
**URLs:**
- https://huggingface.co/blog/funes — "funes: Durable Memory for Coding Agents", David Corvoysier (dacorvo), Hugging Face, 2026-09-03
- https://docs.getdx.com/dx-core-4/ — DX Core 4 measurement framework, getdx.com docs
- https://archive.codenewsletter.ai/2095460192871698728 — Matt Pocock (@mattpocockuk) on the `show-me` skill, 2026-09-03
- https://archive.codenewsletter.ai/2094955084123910297 — Dan McAteer (@daniel_mac8), "5 Fable 5.1 optimizations", 2026-09-02
- https://www.youtube.com/watch?v=icM0ewXGvAw — "19 Claude Code mistakes" (channel not captured; the transcript names no author)
- https://archive.codenewsletter.ai/2095549949870289285 — Google Cloud Tech (@GoogleCloudTech), "6 ways to test AI agents with eval engineering", written by Praveen Rajasekar, 2026-09-03

**Added:** 2026-09-06

**Retrieval:** funes, DX Core 4, and all three `archive.codenewsletter.ai` mirrors via WebFetch rung 1. The video via rung 4 — `uvx --from youtube-transcript-api youtube_transcript_api icM0ewXGvAw --languages en --format text`, 648 lines, complete from the cold open to the sign-off. Two results are **partial** and are labelled as such below: the `humanlayer/skills` `show-me/SKILL.md` (WebFetch declined to reproduce it verbatim and returned its structure instead), and the McAteer post (the page fetched, but its five items came back condensed into command names that do not resolve against any documented command, so its wording is not trustworthy). The three X-archive mirrors are logged **in both places** — summarized here as part of the batch, and in full in [x/README.md](../x/README.md), which is where the two earlier `archive.codenewsletter.ai` sweeps (2026-08-26 and 2026-08-30) already live. The two repositories these sources pointed at — `humanlayer/skills` and `huggingface/funes` — are in [git/README.md](../git/README.md); neither was an input URL, and both were rejected, so they are logged there to keep the rejections findable. No paper source in this batch, so `papers/` is untouched.

**What they're about:** funes is a single-binary local memory layer that indexes coding-agent session traces into a Lance dataset and serves `recall`/`get` tools back to the agent, optionally syncing to a Hugging Face dataset the user owns; its handoff-vs-recall benchmark reports recall as 8x cheaper than a written handoff on one task and 4x on another, with compaction the only channel that split — passing one task and failing the other, where "its summary had flattened the findings that mattered." DX Core 4 consolidates DORA/SPACE/DevEx into four dimensions (Speed, Effectiveness, Quality, Impact) with paired self-reported and system-based metrics on 90-day and 12-week lookbacks. Pocock recommends dexhorthy's `show-me` skill, a format-selection table for explaining code visually. McAteer relays five Fable 5.1 cost optimizations. The video walks 19 Claude Code practices it argues were rewritten in the last six months — persona prompts measuring no better than direct questions, negative instructions, mid-session `CLAUDE.md` edits being inert, `CLAUDE.md` length as the reason a rule gets ignored, subagents not inheriting `CLAUDE.md`, `~7x` token cost for agent teams, fast-mode and model-switch cache economics, long-context retrieval falling from 93% at 256k to 76% at 1M, verification as "the single most impactful tip," and the 30-day `cleanupPeriodDays` default wiping session history. The Google Cloud piece proposes six eval-engineering methods: deterministic schema checks, mathematical groundedness scoring, execution-trajectory inspection, AutoRaters with strict rubrics, pairwise AutoSxS tournaments with answer order swapped between runs, and golden regression fixtures gating CI.

**What we added:**
- Config: **`cleanupPeriodDays: 365`** in `templates/settings.json.template`, the harness repo's `.claude/settings.json`, and `~/.claude/settings.json`. Claude Code's default is 30 days, after which session transcripts under `~/.claude/projects/<slug>/` are deleted — and the harness's whole measurement stack reads those raw transcripts (`token-forensics.py`, `rtk-net-effect.py`, `dashboard.py`, `herder.py`, `session-judge`, `detect_reexplanation.py`, `/insights`). The setting was absent from all three files. At the time of the change this project held 193 transcripts with **zero older than 30 days**, the oldest dated 2026-08-12 — consistent with the sweep running, though not proof it deleted anything, since the repo could simply have no older sessions. This is also what makes any future recall-over-traces work possible: retrieval cannot reach past the retention window. `0` does **not** mean unlimited — it wipes immediately. Auto-memory files are exempt from the sweep; transcripts are not.
- Hook: `hooks/claude/claudemd-edit-notice.sh` (PostToolUse, matcher `Write|Edit|MultiEdit`) — says that a just-written `CLAUDE.md`/`CLAUDE.local.md`/`AGENTS.md` is **not active in the running session**, because instruction files are read once at session start and held in memory. Not hypothetical here: `harness-fix-agent`, `skill-augment-agent`, `claudemd-review` and `/kiro:evolve` all write `CLAUDE.md` mid-session and then proceed as though the rule is in force. The agent cannot observe its own stale context, so only something outside that context can say so. **Soft — exit 2, which for `PostToolUse` is the only exit code whose stderr Claude ever sees.** Matches on basename via `jq`, never substring: the tests assert it stays silent on `templates/CLAUDE.md.template`, on lowercase `claude.md`, and on a `README.md` inside a directory named `CLAUDE.md`. Tests: `claudemd-edit-notice.test.sh`, 18 cases. Registered in `templates/settings.json.template` and the live `.claude/settings.json` (44 registrations, counted by parsing the file).
- Augmentation: `skills/better-call/SKILL.md` — new **Step 2b**, scoring the pair a second time with the incumbent presented first, and a new `INCONCLUSIVE` verdict for when the two passes disagree. From AutoSxS's order-swapping. The skill already guarded *defender* bias in its Key Principles and said nothing about *position* bias; the two are independent, and `better-call` is the gate every extraction routes through, so its bias decides what enters the harness. Same principle as the median-of-3 judge decision of 2026-09-03: one presentation order is one sample. `skills/skill-extraction/SKILL.md` Phase 3f gained the matching row — `INCONCLUSIVE` keeps the incumbent and is recorded as *inconclusive, not rejected on merit*.
- Augmentation: `agents/kiro/validate-adversarial.md` — every surviving concern is now labelled **BLOCKING or OPTIONAL**, and BLOCKING requires naming the requirement, correctness property, or done-condition it violates; asymmetric scoring applies to BLOCKING concerns only. From the video's warning that an adversarial reviewer told only to find problems "will basically keep going until it finds problems." The agent's existing asymmetric scoring deliberately double-weights concerns and set no floor on what counts as one; its "maximum 7 findings" cap bounds the count, not the relevance. The deliberate design is kept and bounded, not reversed.
- Augmentation: `skills/skill-eval-gate/SKILL.md` **Phase 6** + `hooks/claude/skill-validate-hook.sh` — from the golden-regression-fixtures method. On PASS the gate now writes `eval-verdict.json` beside the `SKILL.md`, carrying the **sha256 of the instructions it measured**; the hook hashes every incoming `SKILL.md` write and warns on mismatch, on a missing verdict for an already-existing skill, and on a verdict file that is unreadable or has no hash. Hash and not date, because a skill edited an hour after its eval has a same-day verdict that means nothing. Deliberately silent when the `SKILL.md` does not yet exist on disk — a brand-new skill has not reached the gate, and warning there would fire on every creation and train the warning out. Tests: `skill-validate-hook.test.sh` grew from 8 to 14 cases.

**Rejected — funes as a memory layer.** The tool is good and the benchmark is pointed, but this harness already runs two memory stores that do not see each other (`.claude/memory/` and `~/.claude/projects/<slug>/memory/`), plus `ctx_knowledge` and headroom. A third store makes a known fragmentation problem worse. **Rewriting `/kiro:save-session`'s written handoff into transcript recall was also rejected**, on evidence strength rather than merit: n=2 tasks, author-run, on the author's own tool. What survived is the dependency — the `cleanupPeriodDays` change above is what would make such a rewrite possible at all.

**Rejected — DX Core 4, entirely.** An org-scale, multi-engineer, survey-instrumented framework (PR throughput per engineer, DXI, change-fail percentage, innovation ratio) against a single-operator harness. Its one transferable idea — freeze snapshots over a fixed lookback so historical views stay stable — is already answered by the median-of-3 trust-score decision of 2026-09-03.

**Rejected — the `show-me` skill.** Overlaps `diff-teach`, `mermaid-expert`, `artifact-diagramming`, and `gitnexus-exploring` four ways, in the saturated software-engineering domain (SkillsBench, 4.5pt lift there vs 51.9pt in model-weak domains). If it is wanted later it should be installed as the upstream `humanlayer/skills` plugin, which costs this repo no maintenance. Noted for provenance: its own text was never retrieved, only a description of it.

**Rejected — the Fable 5.1 post.** Retrieval was low-fidelity and the five items map to commands the built-in `claude-api` skill already owns. Its one usable datum — switching effort levels mid-conversation loses the cache — duplicates the video's model-switch item, and is knowledge rather than behavior.

**Rejected — from the video.** Dropping persona/role framing and rewriting "do not X" into positive instructions are both **secondhand** (a video citing a study that was not read) and would mean rewriting 50+ agent prompts and a pervasive `NEVER`/`MUST` convention on hearsay; the blast radius exceeds the evidence. Restating `CLAUDE.md` constraints to subagents is already solved by `subagent-context-hook.sh` via `SubagentStart`. The `CLAUDE.md` word-count target (~300–350 words; this repo's is 1287) is real, but `claudemd-review` already audits that file at session start and ablation-tests candidates before proposing removal — a bare threshold next to that is text, not behavior. Subagent token cost, fast-mode billing, and model-switch cache rebuilds are covered by `model-tiers`, `background-work-routing`, and `dispatching-parallel-agents`. The 93%-at-256k vs 76%-at-1M retrieval figures corroborate the existing ~300k coherence line in CLAUDE.md and earn no change.

**Rejected — four of the six eval methods.** Deterministic schema checks, groundedness scoring, AutoRater rubrics, and trajectory-order assertions: `skill-eval-gate` (`pass^3` against a no-skill baseline) and `session-judge` (median of 3) already cover the measurable ground, groundedness has no surface here, and trajectory-order assertions are a genuine gap but low-yield for a shell harness with no tool-call fixtures. Only the two that named a mechanism this harness lacked — order-swapped pairwise scoring and hash-anchored regression verdicts — were taken.

---

## Eleven-source batch — Fable 5.1 prompt diff, structural quality, and nine rejections
**URLs:**
- https://www.dbreunig.com/2026/09/07/what-we-can-learn-from-claude-s-fable-5-1-system-prompt.html — Drew Breunig, 2026-09-07
- https://codescan.dev/blog/ruff-mypy-pytest-and-then-what — "Ruff, mypy, pytest, and then what?", codescan.dev / pyscn, 2026-09-08
- https://www.seangoedecke.com/large-established-codebases/ — Sean Goedecke, "Mistakes engineers make in large established codebases"
- https://github.com/llm-as-a-verifier/llm-as-a-verifier — LLM-as-a-Verifier framework
- https://github.com/humanlayer/skills/blob/main/plugins/show-me/skills/show-me/SKILL.md — humanlayer `show-me` skill
- https://github.com/langgenius/dify — Dify, open-source LLM app platform
- https://archive.codenewsletter.ai/2095890279865721217 — Andrew Ng (@AndrewYNg), "AI Engineering Skills Map: Using coding agents", 2026-09-04
- https://archive.codenewsletter.ai/2094871453879402747 — Ramp Labs (@RampLabs), "Building the missing layer between AI spend and ROI", René Sultan
- https://archive.codenewsletter.ai/2096439998539321653 — Prasenjit Sarkar (@stretchcloud) on Spotify's "Portal" model routing, 2026-09-06
- https://www.youtube.com/watch?v=W4EwfEU8CGA — "1 million HTTP requests per second" scaling walkthrough
- https://www.youtube.com/watch?v=AbZODZ_4VaM — "How I AI", Stripe's internal agent "Kai"

**Added:** 2026-09-09

**Retrieval:** All eleven via `ctx_url_read` rung 1, including both videos (the tool resolves YouTube to transcript directly, so rung 4 was not needed). Two transcripts and the Ramp article returned truncated tails; the truncation is at the end, after the substance, and is noted rather than papered over. One source is **secondhand and labelled as such**: the Spotify "Portal" item is an X post *about* a Spotify engineering article that was never retrieved, so its 90% token-reduction figure is a claim in a summary, not a measurement this repo read. The three `archive.codenewsletter.ai` mirrors are logged in full in [x/README.md](../x/README.md); the three repositories are in [git/README.md](../git/README.md), where their rejections stay findable. No paper source in this batch, so `papers/` is untouched.

**What they're about:** Breunig diffs Claude's Fable 5.1 system prompt against 5.0 and reads product decisions out of the changes — a bullets-and-lists rule dialled back, an "avoid saying honestly" hotfix, a `user_wellbeing` deletion, and search-tool-call counts replaced with judgement — closing on the observation that skills written for one model generation break on the next, with Anthropic's own advice being to simplify or delete them. codescan measures a mostly agent-written Python repo at five points in its history and finds average cyclomatic complexity drifting 6.9 → 8.5 while the summary grade never left A; its framing case is an agent stalling because four near-identical normalization helpers had accumulated and it could not tell which was canonical. Goedecke argues consistency beats cleanliness in million-line codebases: research prior art first, follow existing patterns even when ugly, resist new dependencies, instrument before removing code. LLM-as-a-Verifier scores agent trajectories by taking an expectation over the verifier's logprob distribution, ranks N candidates with an O(Nk) pivot tournament, and tracks live progress to abandon hopeless rollouts. Ng maps coding-agent skill into five areas. Ramp turns 200,000 agent runs into 250,000 labelled "work items" for cost attribution, and validates a 74% trace compression by first measuring uncompressed run-to-run variance. The Stripe video walks an internal agent serving 10,000 employees off a team under ten, with projects as a governance and model-routing boundary and telemetry driving skill promotion and demotion across ~2,000 skills.

**What we added:**
- Augmentation: `skills/skill-eval-gate/SKILL.md` **Phase 6** + new `scripts/skill-eval-staleness.py` + `/kiro:daily-maintenance` **Step 7** — from Breunig. `eval-verdict.json` now carries `measured_on_model` alongside the existing `skill_md_sha256`, because a measured lift is a joint fact about the instructions *and* the model that read them, and only the instructions were being recorded. The hash catches an edited `SKILL.md` on write; a model change invalidates every verdict at once with **no write anywhere**, so it needs a scan on the daily tick instead of a hook. Three findings, worst first: `hash-mismatch`, `stale-model`, `unknown-model` (a verdict predating the field reads as unprovable, not as passing). `--current-model` is required with **no default and no auto-detection** — a wrong guess marks every stale verdict as current, the exact failure the script exists to prevent. The step **reports only**: re-measuring one skill costs 12 agent spawns, so a fleet-wide unattended re-run on a model-change day is never triggered from the routine. Skills with no verdict file are counted, never flagged — most installed skills are vendored and never went through the gate. Tests: `scripts/skill-eval-staleness.test.sh`, 27 offline cases, most planting a stale verdict and asserting it is named. Measured at time of writing: **zero `eval-verdict.json` files exist** in either tree (997 skills scanned, all never-gated), so three of those cases exist to make the empty case say *nothing was checked* rather than print an all-clear — at zero verdicts a checkmark is vacuously true and reads exactly like a clean bill of health. The field starts paying off with the next skill that passes the gate.
- Augmentation: `agents/kiro/guardrails-agent.md`, `commands/kiro/guardrails.md`, `kiro/settings/rules/deterministic-enforcement.md` — from codescan. A **fourth independent audit dimension, Structure**: duplicated code, dead code, and dependency direction. The first three dimensions all look inside one function; nothing in a standard pipeline looks *across* functions, which is where agent-written code degrades. Two constraints are stated as load-bearing rather than advisory: **gate on the delta**, never whole-repo state (a first run reporting hundreds of findings gets the check disabled the same day, which leaves the repo worse off than never adding it, because the config reads as coverage while nothing runs), and make the checker **agent-callable**, not CI-only (the benefit came from the agent collapsing a clone group in the session that wrote it, while it still knew why both copies existed). Also added: track the *average* over time rather than the grade, from the 6.9 → 8.5 drift under a flat A; and a graduation-path note that architectural rules are the most common thing left stuck at the documentation step, since an agent mostly complies with a written rule while a dependency contract fails the build.

**Rejected — LLM-as-a-Verifier.** The core mechanism takes an expectation over the verifier's **logprob distribution**, and this harness calls `claude --print` on a subscription, which returns no logprobs — the mechanism does not port at all. What remains is repeated evaluation with criteria decomposition, already implemented as `skill-eval-gate`'s `pass^3` and `session-judge`'s median of 3. The pivot tournament ranks N > 2 candidates; `better-call` compares exactly two, with an order flip. The TurboAgent proxy generates N candidates per turn, multiplying subscription spend in a harness that already treats token cost as a problem. Its one genuinely uncovered idea — live progress scoring to abandon a rollout early — would here be a coarse single-shot LLM judgement made repeatedly, which is the failure mode already recorded in `~/.claude/projects/*/memory/single-sample-judge-not-measurement.md`.

**Rejected — Goedecke, but logged as a standing conflict.** Not an integration, and the reason is worth keeping. His central rule is that consistency beats cleanliness: sink into the legacy codebase, follow prior art, never make your corner nicer than the rest. `CLAUDE.md` says close to the opposite — vertical slices, no `shared/`/`utils/`/`common/`, no extraction until three call sites. Both are right in their own regime: he is writing about ~5M lines and hundreds of engineers, where existing patterns are a safe path through a minefield, while the harness rules are built for a repo an agent must hold in context. Worth knowing the harness is making a scale-bounded bet. Writing "follow prior art" into `CLAUDE.md` would be text without behavior, sitting in tension with the Rule of Three directly above it.

**Rejected — the `show-me` skill, a second time.** Rejected on 2026-09-06 on the strength of a *description* of it; this time its text was actually retrieved and the rejection holds unchanged. Its sharpest idea, showing a change as a diff against the shape that already exists, is `diff-teach`'s territory, and `diff-teach` is stronger because it makes the user predict before revealing. Mermaid and HTML-artifact forms are covered by `mermaid-expert` and the built-in `artifact-diagramming`. A fourth overlapping explainer in the saturated software-engineering domain.

**Rejected — Ng's skills map.** A five-area taxonomy of coding-agent skill: directing the workflow, enabling autonomy, reviewing work, customizing the agent and its environment, and agent foundations. Every area maps onto something the harness already runs. It adds framing, not behavior — the definition of a hollow addition.

**Rejected — Ramp's semantic layer, both halves.** Trace-to-work-item extraction with cost attribution is enterprise ROI reporting across 200-plus internal agents; this is a single-operator harness with `auditing-token-spend` and `utils/token-forensics.py` already measuring spend. The stronger idea — validating a compression change by first measuring uncompressed run-to-run variance, then requiring the compressed-vs-uncompressed difference to fall inside it — is genuinely good and **already landed**: commit 618e34f added the compression-regression scenario to `skill-eval-gate`, and the median-of-3 trust-score decision is the same principle.

**Rejected — Spotify "Portal" routing.** The philosophy (hard blocks at the routing layer beat soft rules, which engineers and the model both route around) is already this harness's philosophy — it enforces through hooks, not prompts. The mechanism needs a per-tool-call model-routing proxy Claude Code does not expose. The nearest implementable version, a read-size gate forcing `ctx_read` map/signatures mode, is occupied by `lean-ctx-nudge-hook.sh`. Secondhand besides.

**Rejected — Stripe's Kai, because the harness got there first.** Skill telemetry, the promote-and-demote loop for hot versus long-tail skills, and platform-driven suggestions for improving a skill map one-to-one onto `hooks/claude/skill-usage-tracker.sh`, `skill-curator`, and `skill-augment-agent`. Its "projects as a governance boundary that scopes which skills load" is a real answer to the measured listing-budget problem (`scripts/skill-listing-budget.py`), but `skill-curator` already proposes deletions from usage evidence behind an approval gate, so the delta is archiving rather than deleting — a nuance, not an artifact. Logged as the strongest external confirmation to date that the harness's skill-curation design is right.

**Rejected — the 1M-requests-per-second video and Dify.** The video is a benchmarking and scaling walkthrough (CPU utilization arithmetic, autocannon, C++ over Node) with no agent, harness, or code-quality content. Dify is a product rather than a technique: visual workflow canvas, RAG pipeline, model management, LLMOps. One incidental observation from Dify worth recording — it ships `.agents/skills` and `.claude` alongside both `CLAUDE.md` and `AGENTS.md`, so the dual-standard skills directory is spreading.

---

## Measuring Code Sloppiness
**URL:** https://earendil.com/posts/measuring-code-sloppiness/ | **Added:** 2026-09-16 | **Source:** earendil.com

**What it's about:** Proposes a deterministic sloppiness score for AI-generated diffs (dead code, redundant comments, needless abstraction, inconsistent style) as a lagging quality signal separate from test pass/fail.

**What we added:**
- Script: `scripts/quality/sloppiness-score.sh` — deterministic heuristic scorer over a diff/file set. Fixed 2026-09-16 for a bash-3.2 `mapfile` incompatibility (see `ERRORS.md`).

---

## Fat Agents vs. Narrow Agents
**URL:** https://adlrocha.substack.com/p/adlrocha-fat-agents-vs-narrow-agents | **Added:** 2026-09-16 | **Source:** adlrocha (Substack)

**What it's about:** Argues that a dispatched subagent task is only well-scoped once its input/output contract can be written down without running any code — "if you can't write the task's I/O contract in advance, you haven't decomposed it enough."

**What we added:**
- Skill: `skills/subagent-driven-development/SKILL.md` — new "Decomposition Check (before dispatching any task)" section between "The Process" and "Prompt Templates": input/output/boundary contract check, plus the corollary that per-stage eval sets follow from the same contract.

---

## kiro.dev — Frontier Engineering
**URL:** https://kiro.dev/topics/frontier-engineering/ | **Added:** 2026-09-16 | **Source:** kiro.dev

**What it's about:** Practices for keeping AI-assisted engineering coherent at scale.

**What we added:**
- `templates/CLAUDE.md.template` + live `CLAUDE.md`, AI-Legible Code section, "3rd patch, same function" rule — on a third patch to the same function, regenerate from spec instead of layering another fix. (This section previously existed only in the live installed `CLAUDE.md`, not the harness source template — fixed as part of this pass so it now ships to every project.)
- `templates/CLAUDE.md.template` + live `CLAUDE.md`, "Boundary tests outlive the code they check" bullet — e2e/property/load tests are the stable spec; write them before regenerating a function under the 3rd-patch rule above, not after.
- Skill enhancement: `skills/clean-code/SKILL.md` — new "Comments as Persistent Agent Memory" section: a comment recording *why* a non-obvious decision was made is memory a future agent session gets for free; scoped as an explicit exception to Section 3's "don't comment bad code, rewrite it" rule, not a replacement for it.
- Skill enhancement: `skills/legacy-modernizer/SKILL.md` — new Approach item 6, "One prepared module at a time" — pick the next module only after the current one is fully migrated and tested; parallel migration loses the incremental safety the strangler-fig approach exists for.

---

## ALTK-Evolve: Closing the Consistency Gap
**URL:** https://huggingface.co/blog/ibm-research/altk-evolve-consistency | **Added:** 2026-09-16 | **Source:** IBM Research (Hugging Face blog)

**What it's about:** Black-box, per-step resampling (k=5) of a recorded agent trajectory's decision points to classify each as "sharp" (stable across resamples) or "flip-prone" (near-tied) — no logits, ground-truth, or live re-execution needed. Flip-prone steps become reusable "consistency guidelines" re-injected at inference time. Reported: Pass^5 53.0%→69.0%, Mean@5 77.4%→81.0%, gap 24.4pp→12.0pp; accuracy never dropped; +13pp generalization to related tasks.

**What we added** (augmentation, not a new skill — `evaluation/macro` already does population-scale pattern discovery, this adds the missing metric):
- Skill enhancement: `skills/evaluation/macro/SKILL.md` — new "Consistency Gap (Reliability vs. Capability)" section: `consistency_gap = mean_at_k − pass_hat_k` per repeated task signature; a large gap names a `flip_prone: <signature>` finding distinct from an ordinary failure `behavior_pattern`, diagnosed via the existing Phase 5 backward suspect-trace to isolate the specific varying decision step.
- Pipeline enhancement: `commands/kiro/macro-eval-sweep.md` — new "Step 4b — Consistency Gap" (groups repeated task signatures with ≥3 runs, computes the gap, runs suspect-trace on the top flip-prone signature) and a "Consistency gap" table in the report template; hands off flip-prone steps to `skill-augment-agent` as guideline material rather than writing the guideline itself.
- Prompt enhancement: `scripts/routines/macro-eval-prompt.md` — Rule #2 now explicitly requires the Consistency Gap step for any task signature with ≥3 repeated runs in the sweep window.

---

## Maintaining Context as a Manager (Parts 1–2)
**URL:** https://softwareleads.substack.com/p/maintaining-context-as-a-manager (Part 1); https://softwareleads.substack.com/p/maintaining-context-as-a-manager-35c (Part 2) | **Added:** 2026-09-16 | **Source:** James Samuel (softwareleads.substack.com)

**What it's about:** A manager's practice for tracking projects/people/decisions at scale without producing an unusable archive: a 3-part Capture Filter (enables future action / reveals a pattern over time / helps someone else), a Logseq-based Projects/People/Tasks/Misc structure, and a 3-file AI-agent pattern (`projects.md`/`people.md`/`daily.md`) enforcing signal-over-coverage, P0–P3 priority tiers, evidence-required wording (reported concern vs. confirmed issue), and dedup across sources — run as a scheduled 8am weekday agent task in the original.

**What we added:**
- Skill: `skills/synthesizing-daily-briefings/SKILL.md` — the Capture Filter, `.claude/memory/manager/{projects,people}.md` file structure, and the 5-phase workflow (load/bootstrap → gather signal → filter/tier/dedup → write briefing → update ledger). Never fabricates ledger entries on an empty first run — asks interactively instead.
- Command: `commands/kiro/daily-briefing.md` (`/kiro:daily-briefing`) — the invokable pipeline; headless runs skip with a one-line note rather than bootstrap unattended.
- Routine: `scripts/routines/daily-briefing-runner.sh` + `daily-briefing-prompt.md`, wired into `scripts/orchestration/daily-orchestrator.sh`. Deterministic (no LLM call) ledger-bootstrap guard — writes a `<date>-SKIPPED.md` note directly if the ledger doesn't exist yet, rather than spending an LLM call on a task that must no-op.
- `skill-eval-gate` returned **FAIL/INCONCLUSIVE** (0/3 net lift — see `reports/skill-curation-report.md`'s 2026-09-16 override entry): the 3 scenarios authored for this skill turned out to test generic judgment a capable baseline model already has, not the skill's actual structural contribution. Installed anyway on an explicit, logged override; scenario replacement is open follow-up work for the next eval-gate re-run.

---

## Claude API Anti-Patterns and Effort Calibration
**URL:** https://archive.codenewsletter.ai/2097369738968195513 | **Added:** 2026-09-17 | **Source:** Code Newsletter (archive.codenewsletter.ai)

**What it's about:** Prompt-instruction anti-patterns that inflate Claude API cost/latency without improving output (verification rituals, thoroughness boosters, scratchpad scaffolds), plus an effort-calibration methodology — grid search over model × effort on a train split, confirmed on a held-out test split — for picking the cheapest configuration that still meets a quality bar.

**What we added:**
- Skill: `skills/claude-api/SKILL.md` — fills a pre-existing gap named by `cma-advisor/SKILL.md` and `cma-outcomes/SKILL.md` (both referenced a `claude-api` skill that didn't exist yet). Contains the anti-pattern table, the effort-calibration/hillclimb methodology, and a "Prompt Caching Cost Note" written to match specific TTL/cost figures already cited (as a forward reference) in `skills/context-optimization/SKILL.md`.

---

## Portal by Spotify: Cutting Claude Code Token Usage by 90%
**URL:** https://engineering.atspotify.com | **Added:** 2026-09-17 | **Source:** Spotify Engineering Blog

**What it's about:** How Spotify's internal Portal tool cut Claude Code token usage by delegating bulk, low-judgment multi-file reading/extraction work to a cheap-tier model instead of doing it inline at the primary model's rate.

**What we added:**
- Skill: `skills/cheap-model-delegation/SKILL.md` — when/how to delegate bulk multi-file extraction (skim N files, pull one fact each, no deep per-file reasoning) to a haiku-tier subagent with a tight tool allowlist. Deliberately scoped to exclude single large-file reads, which `hooks/claude/lean-ctx-nudge-hook.sh` already hard-gates separately.
- Hook: `hooks/claude/cheap-model-delegation-hook.sh` (PreToolUse, matcher `Bash`) — advisory-only nudge (never blocks) firing when a `cat`/`head`/`tail` command reads 6+ files in one call, pointing at the new skill. Registered in `templates/settings.json.template` and the live project `.claude/settings.json`.

---

## Prompt Evals Are Useless (Mostly)
**URL:** https://chrismdp.com/prompt-evals-are-useless/ | **Added:** 2026-09-17 | **Source:** Chris Parsons (chrismdp.com)

**What it's about:** Argues most "eval" effort for LLM-product code should go into build-time regression tests, not after-the-fact production grading — a 3-layer pyramid (deterministic unit tests for everything that doesn't touch the model, replay cases that diff structurally rather than string-for-string against stored real request/response pairs, seeded-determinism scenario tests for anything with randomness) plus a narrowly-scoped narrative-quality judge as the exception, not the default.

**What we added:**
- Skill: `skills/evaluation/harness-testing-pyramid/SKILL.md` — new sub-skill in the `evaluation` family, distinct from `micro`/`macro`/`funnel`/`long-trajectory` (which all grade runs that already happened). Documents the 3+1 layer pyramid and a decision table for choosing the right layer for a new test.
- Skill enhancement: `skills/evaluation/SKILL.md` — added `harness-testing-pyramid` row to the sub-skill router table.

---

## Loops, Graphs, and Harnesses: Getting Quality Out of a Software Factory
**URL:** https://ivokund.com/loops-graphs-harnesses-getting-quality-out-of-a-software-factory/ | **Added:** 2026-09-17 | **Source:** Ivo Kund (ivokund.com)

**What it's about:** Running a batch of independent tickets through a dependency graph — layered by unmet dependencies, fanned out to capped-concurrency worktrees per layer, each writer paired with an independent reviewer before merge to an integration branch, verified by a prod-equivalent smoke test once the whole batch lands.

**What we added:**
- Skill enhancement: `skills/loop-patterns/SKILL.md` — new named loop, "Ticket-Graph Batch Loop" (#12), composing primitives the skill already documents (capped-concurrency DAG fan-out from the higher-order-workspace pattern, writer/reviewer split from `gitnexus-pr-review`, the same Loop Guardrails already named elsewhere in the file) rather than introducing new principles — the missing piece was sequencing them across a whole batch of tickets at once.

---

## RRSI: Regularizing Recursive Self-Improvement Search
**URL:** https://regularized-rsi.com/ | **Added:** 2026-09-24 | **Source:** Google Cloud AI Research, UNC-Chapel Hill, Stanford, Washington University in St. Louis

**What it's about:** Self-improving agent harnesses that recursively edit themselves against a benchmark tend to memorize that benchmark instead of genuinely improving. RRSI regularizes the *search* rather than the harness — pairing proposal-side controls (annealed edit budget, evidence-aware credit) with selection-side controls (a leakage critic screening benchmark-specific edits, a noise-adjusted floor requiring gains to exceed baseline variance, a cost rule tying token cost to measured gain). Reports +3.4 pts average gain on six held-out benchmarks never optimized against, vs. gains shrinking/vanishing for prior methods once the benchmark changes.
See also: [git/README.md](../git/README.md) (`langchain-ai/deepagents`'s `better-harness` example implements the analogous train/holdout split for harness optimization).

**What we added:**
- Skill enhancement: `skills/skill-eval-gate/SKILL.md` — new Phase 1d, "Reserve a Holdout Scenario." `skill-eval-gate` already had a noise floor (pass^3 splits scored INCONCLUSIVE) but nothing stopped an author from unconsciously tuning a skill's instructions to the exact scenarios used to certify it. Phase 1d requires at least one scenario be written after the skill is frozen and scored only once, at the final run.

---

## Harness Evals: Why Agent Builders Should Start Testing Early
**Added:** 2026-09-24 | **Source:** Hrushikesh (@Hrushikeshhhh) on X, via codenewsletter.ai archive (https://archive.codenewsletter.ai/2099590015336808865)

**What it's about:** A practitioner essay arguing agent builders should invest in harness evals early even if imperfect. Defines agent = Model + Harness, and a vocabulary (task/trial/transcript/outcome/grader/suite) for eval design. Key claims: 20-50 hand-written tasks pulled from real failures is a fine start; evals need a "negative half" testing over- as well as under-triggering, not just success cases; grade outcomes not exact tool-call sequences; LLM-as-judge needs calibration against human judgment and runs ~30-50% of inference cost.

**What we added:**
- Skill enhancement: `skills/skill-eval-gate/SKILL.md` — new Phase 1a, "Include a Negative (Non-Trigger) Scenario," plus a matching hard-gate row in the Phase 5 verdict table. The gate already measured whether a skill helps (with-vs-without lift); it had no check for a skill wrongly firing on adjacent prompts it shouldn't touch — the essay's "negative half" point named that exact gap.

---

## How We Made claude.ai 3x Faster in Two Weeks
**URL:** https://claude.dev/blog/how-we-made-claude-ai-faster/ | **Added:** 2026-09-30 | **Source:** Wang, Attard, G. (claude.dev, 2026-09-23)

**What it's about:** A two-week, Claude-driven sprint cut latency 3.1x (geometric mean) across 13 user journeys. Each deterministic proxy benchmark (Valgrind `Ir`, React commit counts, layout counts) first had to show it moved real wall-clock time, and then was ratcheted: a PR that raised a count failed CI, and a daily job lowered each ceiling to the new value.
See also: [x/README.md](../x/README.md) (eric zakariasson's token-efficiency audit prompt, same batch, points at the same "static tokens" metric).

**What we added:**
- Augmentation: `scripts/routines/startup-payload-audit.sh` — the fixed 8000-token budget only caught growth past 8000; creep below it went unnoticed. It now keeps a per-repo ceiling that only goes down, flags growth above it (`over_ceiling`, `delta`) and accepts intended growth with `--rebaseline`. The token estimate is deterministic (chars/4), which is the proxy-plus-ratchet shape the article describes. Warns rather than blocks — this harness has no CI. New `startup-payload-audit.test.sh`; the dashboard's Startup Payload card shows the ceiling and delta.

**Rejected:** the web-performance techniques themselves (domain knowledge for web apps; the harness is shell and Python), Valgrind `Ir` as a CI gate (no CI), feature-flag ramps (no product surface).

---

## Build an Agentic Software Factory: Deep Dive
**URL:** https://www.youtube.com/watch?v=pNmfMi-yjZk | **Added:** 2026-09-30 | **Source:** Steve (Builder.io), full transcript via youtube-transcript-api

**What it's about:** A local scheduled loop — collect → babysit PR → review against an approval policy → ship, with anything outside policy going to a human. Two extras: a "watchdog" that runs ~4×/day and nudges work that was actionable but never reached its next step, and a weekly lookback for issues that came back after a fix.

**What we added:**
- Augmentation: `skills/synthesizing-daily-briefings/SKILL.md` — Phase 2b, a stalled-work sweep: open PRs with no activity for `STALL_DAYS`, and drafts still carrying the Evidence placeholder `scripts/pr/detect_base_and_create.sh` writes. Reads structured `gh pr list --json` fields only. The briefing only reported what *changed*, so stopped work was invisible by construction — the source's point: "I've wasted a lot of time … thinking something was worked on when it wasn't." `scripts/routines/daily-briefing-prompt.md` runs it on the existing schedule.

**Rejected:** the weekly lookback (`tool-failure-capture.sh` already reopens recurring ledger entries; no product telemetry for the rest), the YAML triage policy (`issue-triage-routing`, `jira-solve`), auto-merge approval policy (`pr-risk-tier-hook.sh`).

---

## Getting the Most Out of Opus 5.5 in Claude and Claude Code
**URL:** https://claude.dev/blog/getting-the-most-out-of-opus-5-5/ | **Added:** 2026-09-30 | **Source:** Anthropic (claude.dev). WebFetch returned a condensed version, so wording here is paraphrase, not quotation.

**What it's about:** Opus 5.5 always reasons before replying, so "think carefully" lines only slow it down, and requests to show its reasoning in the reply can be flagged and declined. Say what "done" looks like, which pauses you want, check subagent evidence, and keep a task file that survives compaction.

**What we added:**
- Augmentation: `hooks/claude/prompt-quality-check.sh` — two new anti-patterns, `think-instruction` and `show-reasoning-request`, on every Agent spawn. The existing `mandatory-scratchpad` check only caught scratchpad wording. The same edit rewrote the hook's four regex checks as literal token matching and removed it from `scripts/utils/no-regex-debt.txt` (score-identical to the old version on 94 real Agent prompts). New `prompt-quality-check.test.sh`. Hit rate is unmeasured: the log stores only a prompt hash.

**Rejected:** "keep going when a step doesn't need me" as a template rule (clashes with the human review gate), a compaction-proof task file (`planning-with-files`, `feature-list-primitive`), a Blocked/Changed/Found closing format (the Post-Task Convention covers it).

---

## What a Task Costs on Opus 5.5
**URL:** https://claude.dev/blog/what-a-task-costs-on-opus-5-5/ | **Added:** 2026-09-30 | **Source:** Addy Osmani (claude.dev, 2026-09-25)

**What it's about:** You pay per task, not per token: turns × resent context, cache share, output (5× input, thinking included) and model. Opus 5.5 defaults to medium effort and thinks more than Opus 5 at the same level; ~20K extra thinking tokens cost about what a ten-turn retry loop does. Lists what breaks the cache in Claude Code.

**What we added:**
- Correction: `skills/model-tiers/SKILL.md` — removed "this harness runs `high`" (no effort setting exists in global settings, project settings or the template). Now: the harness sets none, Opus 5.5 defaults to `medium`, raise effort only when it saves a retry.
- Augmentation: `skills/auditing-token-spend/SKILL.md` — Phase 3 rows for a low cache-read share (the Claude Code cache-breakers: pause past the cache lifetime — 1h on a subscription, 5m on an API key — model switch, MCP connect/disconnect, effort change via a gateway, first fast-mode use, compaction) and for large output on a small change. `skills/context-optimization/SKILL.md` now says its 5-minute TTL is the API default, not the subscription one.

**Rejected:** price tables and calculators (go stale, no behavior), subagent `model:` advice (`model-tiers`, `cheap-model-delegation`), agent teams / opusplan (experimental).

---

## Automating Eval Design and Hillclimbing with Claude
**URL:** https://claude.dev/blog/automating-eval-design-and-hillclimbing/ | **Added:** 2026-09-30 | **Source:** Lance Martin (claude.dev, 2026-09-28)

**What it's about:** Four properties of a good eval (tasks reflect production, stronger models score higher, headroom at the frontier, low variance) and the `/claude-api build-eval` and `hillclimb` workflows: cheapest grader, validate the grader, run it twice, separate infrastructure noise; random train/test split, keep a patch only if both improve.

**What we added:**
- Augmentation: `skills/skill-eval-gate/SKILL.md` — (a) a run that timed out, hit an API/tool error or was cut off is a *missing* run: re-run it, record it `ERRORED`, never score it as a FAIL in `pass^3`; two errors on one scenario stop the gate with an infrastructure report. (b) Phase 3b judge validation adds a self-consistency check — score one output twice, discard the run if the verdict flips.

**Rejected:** train/test split (already `claude-api` Hillclimb Methodology and `skill-eval-gate` Phase 1d), "stronger model scores higher" (Phase 1b), a new build-eval skill (duplicates `skill-eval-gate`, `raindrop-eval-loop`, `macro-eval-sweep`).

**Open finding (unverified):** the harness's own `skills/claude-api` has the same name as Anthropic's `claude-api` skill, which the article says provides the `build-eval` / `hillclimb` / `prompt-audit` sub-commands. The local skill may shadow the official one; not checked.

---

## How We Engineer Safer Agents
**URL:** https://www.perplexity.ai/hub/blog/how-we-engineer-safer-agents | **Added:** 2026-10-01 | **Source:** Perplexity (2026-09-29)

**Fetch status:** partial. WebFetch got a 403 and the Wayback Machine is blocked for that tool; lean-ctx `ctx_url_read` retrieved the article.

**What it's about:** "Accidental meltdowns": an agent with a legitimate goal hits friction and crosses security boundaries to get past it. Examples are the July 2026 Hugging Face breach and agents probing Data USA with SQL injection. The fix is defense-in-depth under three rules: layers fail independently; at least one layer is deterministic code below the agent ("a safeguard the agent can decline to invoke, or reconfigure, is not a safeguard"); and signals can only reduce authority. Portable Computer's sandbox fails closed — if the sandbox is unavailable, the harness disables itself.

**What we added:**
- Fix: fail-closed on the guard's own failures in `hooks/claude/git-destructive-guard-hook.sh` and `hooks/claude/ledger-append-only.sh`. A missing `python3` or a malformed event used to collapse to `exit 0`. Each hook now blocks when the raw event touches what it protects.
- Bug fix: `hooks/claude/agent-behavior-guard.sh` enforce mode never blocked. Its trailing `exit 0` discarded the Python's `sys.exit(2)`. The fix was found while auditing for the fail-closed rule. New `agent-behavior-guard.test.sh` (13 cases); 13 new cases across the three suites fail on the old hooks.
- Augmentation: `skills/hook-design/SKILL.md` — new "Fail Closed, Narrow Only" section. The canonical stdin-parsing snippet, which itself taught the fail-open `except: print('')` / `|| echo ""` idiom, is corrected.

**Rejected:** the six-layer product stack (SPACE, BrowseSafe, Numbat are Perplexity infrastructure; independent verifier already in `agent-permissions-design` / `secure-agent-design`), the improvement loop (`harness-fix-agent`, `skill-augment-agent`).

**Open:** `hooks/claude/scan-pii.sh` reports "No PII detected" when `opf` errors or its output fails to parse (`|| true`, `except: print(0, "", 0)`). Not fixed: `opf` is not in the lean-ctx shell allowlist, so its clean-file output contract could not be checked.

_Last synced: 2026-10-01