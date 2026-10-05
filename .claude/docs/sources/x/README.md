# X (Pasted Text)

Content passed directly as pasted text to `/skill-extraction` — not a URL, repo, or file path. Ordered by date added.

---

## @nityeshaga — Trust Battery Design
**URL:** https://x.com/nityeshaga/status/2044864114682741134 | **Added:** 2026-04-21 | **Source:** @nityeshaga (X post)

**What it's about:** Design for a "trust battery" system inspired by Tobi Lütke's Shopify mental model. Core insight: separate the Judge (scores behavior, proposes nothing) from the Reflector (improves, reads Judge's drains) — if one agent both scores and improves, it optimizes for score not quality. The sharpest practical signal: every re-explained preference is a memory the agent should have saved but didn't.

**What we added:**
- Script: `scripts/detect_reexplanation.py` — Haiku detector scanning session user turns for drain signals (re-explanation, frustration) and charge signals (explicit approval). Runs at session end via stop-hook; appends at most one `[memory-gap]` and one `[session-charge]` per calendar day
- Script: `scripts/trust_score.py` — score math: ±4.5%/day cap, [0,100] clamp, idempotent per calendar day, rewrites hot-memory.md scoreboard header
- Agent: `agents/kiro/session-judge.md` — adversarial Haiku scorer; emits verdict JSON only, hard constraint: never proposes fixes
- Command: `/kiro:daily-maintenance` — nightly orchestrator: Judge → Reflect → Housekeeping → Score → Alert
- Rule: `kiro/settings/rules/session-quality-rubric.md` — charges (+1, max 5/day) and drains (-2, max 5/day) with evidence requirements. Trust Score is observability only — never gates harness behavior.
- Hook enhancement: `hooks/claude/stop-hook.sh` — calls detector after each session
- Docs: `docs/trust-battery/README.md`

---

## @sukh_saroy — Skill Augmentation from Session Learnings
**URL:** https://x.com/sukh_saroy/status/2046177... | **Added:** 2026-05-06 | **Source:** @sukh_saroy (X post)

**What it's about:** Pattern of automatically encoding session friction signals back into skill files — closing the loop between nightly judge verdicts and the skill library. Each drain maps to a skill domain; each drain should produce an anti-pattern note in the relevant SKILL.md.

**What we added:**
- Agent: `agents/kiro/skill-augment-agent.md` — reads judge verdict + today's observations, appends anti-patterns/learned patterns to up to 3 skills per run. Append-only, 150-char limit per addition, citation required. Domain mapping: memory-gap→memory-systems, silent-failure→error-handling-patterns, gate-bypass→verification-before-completion, rule-skip→systematic-debugging, stale-context→context-management
- Command enhancement: `commands/kiro/daily-maintenance.md` — Step 6 added after score step
- Routine: nightly CCR registered at 9:47 PM daily

---

## Verifying Agentic Development at Scale (Pasted article)
**Added:** 2026-06-02
**Source / Author:** Pasted article — Cognition (Devin team) blog post on autonomous end-to-end testing

**What it's about:** How Cognition's Devin agent approaches self-verification after completing async work. Core insights: (1) test plans must be grounded in actual source code before testing begins — models hallucinate paths that don't exist when they work from assumptions; (2) assertions committed *before* performing an action (annotation-first, like TDD) make it much harder to rationalize unexpected results as passes; (3) repeated setup steps (login flows, env setup, dependency installs) extracted to deterministic scripts reduce flakiness and token cost dramatically; (4) async agents are only trustworthy if they return verifiable proof, not just a success claim.

**What we added:**
- TDD augmentation: `tdd-workflows-tdd-red` — step 0 "Source scan first" inserted before test identification. Read `git diff HEAD` for bug fixes, spec + existing interfaces for new features. Only write tests for paths that actually exist; flag assumed paths `# TODO: verify path exists`.
- TDD augmentation: `tdd-workflow` — "Source Grounding" block added to section 3 (RED Phase Principles): scenario → what to read first lookup table (bug fix → diff, new feature → spec + interfaces, refactor → current implementation).
- Hook: `setup-buffer-hook.sh` (PostToolUse Bash) — accumulates setup-like commands (pip/npm/yarn/pnpm/brew installs, docker, db migrations, `.env` exports, `git clone`, `make setup/init`) to `.claude/memory/.setup-session-buffer.log` during a session.
- Hook augmentation: `stop-hook.sh` — added "Setup sequence capture" section: at session end, if buffer has ≥2 setup entries, writes a dated `bash` code block to `.claude/memory/setup-knowledge.md` (project-level memory), then clears the buffer.
- `settings.json` updated: `setup-buffer-hook.sh` registered in PostToolUse Bash hooks array.

---

## Claude Code Token Reduction (YouTube transcript)
**Added:** 2026-06-02 | **Source / Author:** Pasted transcript — YouTube video on 4 strategies to reduce Claude Code token usage by up to 90%

**What it's about:** Four complementary token reduction strategies for Claude Code: (1) CodeGraph — semantic codebase indexing via SQLite graph so Claude finds files by natural language instead of grep loops; (2) RTK (Rust Token Killer) — CLI proxy that compresses Bash output 60–90% before it enters context; (3) Caveman — per-session mode that compresses Claude's own response text 65% on average; (4) Session hygiene — `/compact`, `/clear`, `/model` switching, plan mode first. Each strategy targets a different layer and carries distinct trade-offs (staleness, lossy compression, quality degradation at ultra levels).

**What we added:**
- Tool: `rtk` v0.42.0 installed at `~/.local/bin/rtk` — replaced `ztk`; global PreToolUse hook (`rtk hook claude`) wired in `~/.claude/settings.json`
- Tool: `caveman` installed — SessionStart hook auto-activates lite mode every session; default set in `~/.config/caveman/config.json`; user can run `/caveman full`, `/caveman ultra`, or `normal mode` to adjust
- Propagation: `update.sh` run for all 3 registered projects — rtk docs synced, ztk docs replaced

---

## SOUL.md Anatomy + Orchestration Tax (Pasted thread)
**Added:** 2026-05-31
**Source / Author:** Pasted text — X thread on SOUL.md + Google I/O 2026 panel essay (Seroter, Hammerly, Jaspan)

**What it's about:** Two complementary frameworks for building better agentic systems. (1) SOUL.md: an 8-section identity file format for AI agents that sits at the top of the system prompt before memory/skills/tools — identity, core truths, worldview, voice, expertise, boundaries, memory policy, pet peeves. Key claim: "be helpful and professional" changes nothing; specificity (30–80 lines) is the only thing that compounds. (2) Orchestration Tax: human attention is the GIL of multi-agent systems — the single serial resource all agent work must route through. Amdahl's Law applied to review throughput explains why adding more agents grows queue depth without increasing output. Five practical rules for designing around this constraint.

**What we added:**
- Skill: `agent-identity` — new skill implementing the 8-section SOUL.md framework. Mode A (full SOUL.md) for agent design; Mode B (reduced identity check) auto-triggered from skill-extraction and skill-creator to validate skill identity sharpness at creation time.
- Enhancement: `skill-extraction` Phase 5c — mandatory identity alignment check (invokes `agent-identity` Mode B) before any new skill is logged to the sources index.
- Enhancement: `skill-creator` Phase 4c — same identity check wired into the harness skill-creator before installation.
- Enhancement: `multi-agent-patterns` Orchestration Tax section — GIL analogy, Amdahl's Law framing, and 5 design rules (scale fleet to review rate, sort work, batch reviews, spend the lock on judgment, protect serial time).

---

## Claude Code Feedback Loops — Self-Verification Patterns (Pasted article)
**Added:** 2026-06-07
**Source / Author:** Pasted text — Anthropic/Claude Code team article on self-verification and workflow bundling

**What it's about:** How to encode domain-specific manual checks as verification skills so Claude self-verifies mid-work without prompting. Core insight: the gap between Claude's first response and your final result is filled by checks only you were running. Three patterns: (1) `<domain>-verify` skills encode manual inspection steps as executable phases; (2) bundling skills into a composite pipeline (simplify → verify → design → PR → CI watch) lets Claude complete entire feature workflows autonomously; (3) second-agent review before merge provides unbiased quality gating from a fresh context.

**What we added:**
- Skill: `verification-skill-authoring` — meta-skill for creating `<domain>-verify` companion skills; includes domain defaults (frontend, API, data pipeline, CLI) and rubric pattern for qualitative checks
- Skill augmentation: `skill-extraction` Phase 5d — after identity check on any new skill, automatically asks if a companion verify skill is warranted; invokes `verification-skill-authoring` if yes
- Skill augmentation: `skill-creator` Phase 4d — same companion check wired into the skill-creator workflow
- Command augmentation: `kiro:ship` Step 0 — `/simplify` added before the verify pipeline, matching the described composite pipeline pattern

---

## State of Memory in Agent Harnesses (Pasted article)
**Added:** 2026-06-07
**Source / Author:** Pasted text — comparative analysis of memory architectures across 6 major AI coding harnesses

**What it's about:** Systematic comparison of how Claude Code, Anthropic Managed Agents, OpenAI Codex, GitHub Copilot, OpenClaw, and Hermes Agent implement memory. Introduces a 3-tier taxonomy (working/external/parametric) and the memo ceiling theorem (arXiv:2604.27707: Ω(k²) vs O(d)). Covers each harness's retrieval mechanism, persistence model, hard limits, and key shortcoming. Only Copilot has published real-world A/B data (83%→90% PR merge rate lift). Key patterns: Copilot citation-schema at write time + JIT expiry, Codex two-phase consolidation (6hr idle gate → locked merge), Hermes 80% utilization threshold trigger.

**What we added:**
- Skill augmentation: `agent-memory-systems` — added "Production Memory Ceiling" section (3-tier taxonomy + memo theorem) and "Harness Comparison" table (6 systems, retrieval mechanisms, limits, shortcomings, published A/B metric).
- Skill augmentation: `agent-memory-discipline` — added "Citation Schema (Write-Time)" section: Copilot-inspired structured frontmatter (`citation: {file, line, symbol, verified_at}` + `expires_at`) enabling mechanical validation of code-citing memories. Pairs with existing CLAUDE.md read-time check.
- Skill augmentation: `agent-memory-consolidation` — added "Consolidation Timing Patterns" section: Pattern A (Codex two-phase idle-gate + locked merge) and Pattern B (Hermes 80% utilization-gauge trigger), with guidance on which to use per failure mode.

---

## Braintrust Topics — Continuous Trace Intelligence at Scale (Pasted article)
**Added:** 2026-06-07
**Source / Author:** Pasted text — Braintrust engineering blog post on their "Topics" trace intelligence pipeline

**What it's about:** How Braintrust built active observability for production LLM agent traces — finding patterns you didn't know to look for. Core insight (from Anthropic's Clio paper): instead of embedding raw multi-million-token traces, ask an LLM to summarize the trace along one dimension in 1-2 sentences, then embed that summary. The same pipeline works for any analytical dimension (task, issues, sentiment, custom). Key optimizations: batch multiple facets into one LLM call (trace tokens paid once), hard-cap preprocessed input at 128K tokens, use HDBSCAN + UMAP without prespecifying cluster count, and classify via nearest-centroid lookup (~100ms, no LLM at classify time).

**What we added:**
- Skill: `active-observability` — full workflow for batch pattern discovery in Raindrop Workshop traces; 4-phase pipeline (collect → batch-facet → LLM-cluster summaries → report); adapted from Braintrust's 6-stage pipeline using LLM-as-judge clustering instead of ML deps
- Hook: `raindrop-best-practices.sh` (PreToolUse `mcp__raindrop__`) — soft gate injecting 5 key patterns as context whenever any Raindrop MCP tool fires; registered in `~/.claude/settings.json` and `templates/settings.json.template`
- Skill augmentation: `raindrop-eval-loop` — added Phase −1 "Pattern Discovery" block pointing to `active-observability` for use before writing eval assertions

---

## Orchestrator/Executor Routing — Rubric-Based Model Tier Selection (Pasted article)
**Added:** 2026-06-08
**Source / Author:** Pasted text — "15 prompts that cut my Coding bill from $7,800 to $129" — AI workflow cost optimization article

**What it's about:** Orchestrator/executor model split pattern: use reasoning-tier models (Opus) for planning, judgment calls, and quality review; use execution-tier models (cheap parallel agents) for batch work with clear output specs. Core insight: the routing decision reduces to one question — "can you write a rubric that a machine could grade?" If yes → execution tier. If no → reasoning tier. Article includes 15 prompt templates for the plan→rubric→execute→review→assemble workflow cycle.

**What we added:**
- Skill augmentation: `model-tiers` — added "The Rubric Test" section between Decision Heuristics and Practical Patterns: the semantic routing test (can you write a machine-gradable rubric?) with two worked examples showing where it catches what complexity-signal scoring misses.
- Skill augmentation: `multi-agent-patterns` — added "Rubric-First Dispatch" subsection in Skill Routing Quality: 3-field rubric structure (pass/fail/failure-modes), handoff sequence diagram, and the TDD-red-first analogy for pre-commitment; paired with the existing post-condition coupling pattern.

---

## Dynamic Workflow Patterns — 6 Patterns and 14 Steps (Pasted article)
**Added:** 2026-06-08
**Source / Author:** Pasted text — "How to master Dynamic Workflows in Claude Code" — movez.substack.com, ~Jun 2026

**What it's about:** Deep explainer on Claude Code's Dynamic Workflow feature (shipped May 28, 2026). Covers the mental model (Claude writing its own JS harness using `agent()`, `parallel()`, `pipeline()`), the 3 failure modes workflows fix (agentic laziness, self-preferential bias, goal drift), the 6 composable patterns (classify-and-act, fan-out-and-synthesize, adversarial verification, generate-and-filter, tournament, loop-until-done), pattern composition recipes for 9 real use cases (migrations, research, sorting, triage, evals, etc.), workflow controls (`/goal`, `/loop`, token budgets), quarantine pattern for prompt injection in untrusted input, and how to save/ship workflows as Skills.

**What we added:**
- Skill augmentation: `multi-agent-patterns` — added "Dynamic Workflow Patterns" major section covering: the 3 failure modes and which pattern fixes each; all 6 patterns with when-to-use rules and code examples; composition matrix (use case → pattern combination); workflow controls (`/goal`, `/loop`, token budgets); quarantine pattern (read-only reader agent for untrusted input); workflow-as-Skill packaging; and 8-item common-mistakes table. Also updated description frontmatter to include dynamic workflow triggers. Version bumped 1.3.0 → 1.4.0.

---

## Loop Engineering — Designing Systems That Prompt Agents
**Added:** 2026-06-11
**Source / Author:** Pasted text — article on loop engineering (@bcherny head of Claude Code, @steipete framing)

**What it's about:** Framework arguing the leverage point in AI coding has shifted from writing good prompts to designing loops that self-prompt agents. Five building blocks: automations (heartbeat), worktrees (parallel isolation), skills (encoded knowledge), connectors (MCP), sub-agents (maker/checker). Plus a 6th: the external state file as loop spine. Key insight: three failure modes worsen *as the loop improves* — cognitive surrender (accepting loop output without judgment), comprehension debt (mental model gap grows as loop ships code you didn't write), and self-preferential maker (loop's learning pipeline grades its own output). Article argues designing the loop is harder than prompt engineering, not easier; the leverage point moved.

**What we added:**
- Script change: `scripts/routines/daily-maintenance-prompt.md` Step E — changed "apply up to 3 updates" to goal-driven: "address all [seed-target:] observations from today, max 5 as circuit breaker." Closes the arbitrary count → verifiable stop condition gap.
- Script addition: `scripts/routines/daily-maintenance-prompt.md` Step F — adversarial checker agent spawned after Step E; verifies each [skill-update] against the gap that motivated it; emits [skill-update-verified] or [skill-update-flagged]. Implements maker/checker split in the loop's own learning pipeline.
- Hook change: `hooks/claude/stop-hook.sh` — added loop-debt detection block: Python inline script checks if [skill-update] entries are ≥3 days old with no [session-charge] since; appends [loop-debt] observation if so. Cognitive surrender proxy.
- Script change: `scripts/session/trust_score.py` — added [loop-debt] tag to `_tally_tags` and `cmd_auto_score`; penalty of −1 per occurrence (max −2/day) in delta calc. Loop health now a scored signal.
- Skill augmentation: `skills/multi-agent-patterns/SKILL.md` — added "Loop Health" section with three named failure modes (cognitive surrender → [loop-debt], comprehension debt → keep-rate, self-preferential maker → Step F), harness detection for each, and counter-moves. Version bumped 1.4.0 → 1.5.0.

---

## Building a Good Vertical Agent — Context as a Layered Cache (Pasted text)
**Added:** 2026-06-15
**Source / Author:** Pasted text — Peter Wang (@BrainsAndTennis), builder of the Shortcut spreadsheet agent

**What it's about:** Argues a good agent is a faithful compression of its task distribution, and that context (system prompt + tools + artifacts) should be structured like a CPU memory hierarchy. Users bring a long-tailed task distribution; the objective is to minimize context spent per task averaged over that distribution. L1 = always-resident bread-and-butter ops (the 80%), made token-compressed and consequence-reporting. L2 = curated, gotcha-aware English specs fetched in one discovery step (the ~15%). L3 = the raw complete substrate on disk plus a short skill that teaches the agent to mine it with grep (the long tail). Placement is the craft: resident tokens are paid every task, discovery tokens only on a miss. The boundary slides down a tier as models strengthen, but the hierarchy never disappears. Also argues for one `execute_code` tool over many (covered already by `tool-design`).

**What we added:**
- Skill augmentation: `agent-harness-design` — added "Context as a Layered Cache (L1/L2/L3)" subsection to the 𝒞 (Context Constructor) component: a placement decision rule (minimize resident + discovery cost over the task distribution), a tier table mapping L1/L2/L3 onto harness artifacts (CLAUDE.md + SKILL.md bodies / `resources/` + deferred tools / on-disk references + grep-recipe skill), the consequence-reporting property for L1 ops, and the "tiers slide with model strength" note. Cross-referenced `context-optimization` (intra-tier mechanics) and the existing temporal-scaling tiers (when interventions act vs. where capability lives). Rejected: one-tool (dup of `tool-design`), read/write compression specifics (spreadsheet-domain), and a standalone skill (would add a 4th overlapping context-* skill).

---

## Self-Improvement Loop for Skills (Warp blog, pasted text)
**Added:** 2026-06-18
**Source / Author:** Pasted text — Warp blog on self-improvement loops for Skills (issue-triage example, Oz cloud agents)
See also: [git/README.md](../git/README.md) — companion repo https://github.com/warpdotdev-demos/issue-triage-loop

**What it's about:** A two-loop pattern for skills that improve from external feedback. Inner loop applies a skill and records every run (file/trace/external system). Outer loop runs on a schedule, observes the inner runs — especially where a human *overrode* the agent's output and said why — and diffs the SKILL.md to fix it. Thesis: the human override is the gold training signal; an automated grader can substitute only when the goal is machine-gradable.

**What we added** (one augmentation — the harness already implements the two-loop architecture via `daily-maintenance` Step E + `skill-augment-agent`; everything else rejected as duplicate):
- Agent augmentation: `agents/kiro/skill-augment-agent.md` — new Step 1.5 loads today's `type: feedback` memories (user corrections) as a first-class evidence source, **auto-qualifying (2/2) and drafted before LLM judge-drain evidence** — encodes Warp's "human override outranks the grader" thesis. Closes the gap where skill-shaped user corrections lived only in memory and never reached the skill diff. Rides existing nightly automation; no new hook/routine. Mirrored to README.md, SDD-USAGE.md, scheduled-tasks/README.md.
- Rejected: two-loop framework (already = Step E + augment agent), GitHub Action inner-loop trigger (no issue-tracker goal), scheduled outer agent (= daily-maintenance), `improve-triage-skill`/`triage-issue` skills (domain-specific), human-correction capture hook (noisy detection; capture already exists via `detect_reexplanation.py` + `type: feedback` memories — only routing was missing).

---

## The Stanford STORM Method (Pasted text, Nav)
**Added:** 2026-06-21
**Source / Author:** Pasted text — popularization of Stanford OVAL's STORM (Synthesis of Topic Outlines through Retrieval and Multi-perspective Question Asking, NAACL 2024)

**What it's about:** A 4-prompt research workflow that compresses PhD-style topic research: simulate 5 adversarial expert perspectives (Practitioner/Academic/Skeptic/Economist/Historian) → map their contradictions (incl. unanimous agreement = likely true, unaddressed = field blind spot) → synthesize a reliability-ranked briefing → adversarially peer-review the briefing for bias. Stanford's published result: multi-perspective articles ~25% more organized, ~10% broader than single-prompt.

**What we added:**
- Skill: `storm-research` — the 4-phase method as a harness-native skill. Default **Workflow mode** fans the 5 personas out as *parallel* agents (no voice contaminates another) then runs contradiction → synthesis → adversarial peer-review as serial downstream stages; **Inline mode** runs the 4 verbatim prompts sequentially for quick passes. Verbatim prompts live in `resources/prompts.md`. Adds a grounding rule (personas don't invent citations — ground empirical claims via WebSearch) the source lacked.
- Rejected: augment `multi-agent-brainstorming` (sharp identity = review of a *proposed design*, not unknown-topic research — would blur it); augment `deep-research` (external paid Gemini API, opaque internals); `/storm` command (Skill is the right surface); hook (no lifecycle event / on-demand only); routine (not scheduled); dashboard widget (no persistent metric).

---

## Hermes Agent — Self-Improving Loop (Pasted text)
**Added:** 2026-06-21
**Source / Author:** Pasted text — "Hermes Agent FULL GUIDE: Architecture, Setup, and the Self-Improving Loop" (Nous Research)

**What it's about:** A cloud-resident messenger agent whose self-improving loop (trigger system → background review agent → curator) maintains its own skill library. The curator's distinguishing mechanic is a hidden **usage log** — load count + last-use timestamp per skill — feeding a token-free mechanical pass that deprecates agent-generated skills unused >30d and archives those unused >90d (with pinning), *before* any LLM review.

**What we added** (one integration — the harness already implements the loop via reflect/evolve/skill-augment/skill-curator/agent-memory-consolidation + daily-orchestrator; only the usage-evidence layer was a genuine gap):
- Hook: `skill-usage-tracker.sh` (`PostToolUse` matcher `Skill`, silent, zero tokens) — logs `{ts,skill}` per skill invocation to global `logs/skill-usage.jsonl`. Closes the gap where `skill-curator` claimed to prune "unused" skills but had no usage data (it guessed from file mtime).
- Curator augmentation: new **Phase 1.5 — Usage Evidence Audit** in `skill-curator-prompt.md` + report **Usage Evidence** section; `skill-curator` SKILL.md Delete criterion now requires evidence-backed cold status (no 30d use) and protects `pinned: true`.
- Dashboard: `render_skill_usage()` in `dashboard.py` adds hot/cold stats (total/30d invocations, skills used, cold count), a top-skills bar chart, and a deprecate-candidate list to the **Skill Changes** tab.
- Rejected: self-improving loop / triggers / curator (core of harness already); model routing for aux tasks (`model-tiers` + `background-work-routing`); skill-struggle real-time trigger (covered by `skill-augment-agent` + `action-capture` struggle detection); cron `-no-agent` token-free scripts (daily-runner already does this); curator auto-backup + rename map (git = backup, human-approval gate exists); webhooks/Kanban/goal-judge/terminal-backends/memory-engines (cloud-agent architecture, N/A to local harness).

---

## Your AI's Memory Is Quietly Making It Dumber (I Cut Mine to 6 Files) (Pasted text)
**Added:** 2026-07-02
**Source / Author:** Pasted text — Matt Van Horn (@mvanhorn)

**What it's about:** Push-memory (CLAUDE.md + native auto-memory) injects into context every session and *past a threshold reduces instruction adherence* rather than adding knowledge — the author's memory index hit 218 files / 46 KB and the harness was silently dropping half of it. Central thesis ("skills over memory"): most "memory" is a **misfiled skill lesson** — a fix tied to one skill belongs *in that skill* (as a PR, version-controlled, helping everyone who uses it), not journaled in a global store where it rots. Bucket every entry as trash (git has it) / skill-tied (route to skill) / genuinely cross-cutting (keep). Also: archive-before-delete, keep entry files <200 lines, and the push-memory (always-loaded) vs pull-memory (queried on demand) distinction.

**What we added** (two augmentations — the harness is already the *opposite* of the cautionary tale: global CLAUDE.md 1 line, project 52, MEMORY.md 16 entries; and `instruction-architecture`/`skill-curator`/`housekeeping-agent`/`skill-augment-agent`/`skill-extraction` already cover the CLAUDE.md-leanness, skill-pruning, archive-before-delete, push-lessons-into-skills, and >2×→make-a-skill hacks. The one genuine gap was the misfiling *decision at the memory-write gate*):
- Augmentation: `learn-eval-agent` + `learn-eval` command gain a fourth verdict **Route** (alongside Save/Absorb/Drop). When a pattern's evidence is tied to exactly one existing skill, it is NOT written to `patterns.md` — it is routed to `skill-augment-agent` to push into that skill's SKILL.md. Prevents skill-tied lessons being misfiled into global memory at intake. `daily-maintenance` Step 6 now consumes Route markers as the handoff.
- Augmentation: `housekeeping-agent` gains **Step 6.5 — Flag Skill-Tied Entries**, the backfill complement: it flags already-saved memory entries that belong in a skill and recommends routing them (recommend-only, archive-first discipline — never auto-moves).
- Rejected: `<200-line CLAUDE.md`/lost-in-middle (covered by `instruction-architecture`); skill pruning + description budget (`skill-curator`); archive-before-delete/prune (`housekeeping-agent`); >2×→make-a-skill (`skill-extraction`/`skill-creator` + Rule of Three in CLAUDE.md); push-vs-pull memory framing + retrieval-store tools gbrain/mem0/supermemory (conceptual, harness memory already lean push; hollow); disable native auto-memory `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` (N/A — harness uses bespoke *gated* file memory, not native auto-memory); `@AGENTS.md` import / `ln -s AGENTS.md CLAUDE.md` (real but low value — only pays off running Codex/Cursor against this repo; flagged not built).

---

## Cloud Software Factory — Spec-Driven Development (Pasted text)
**Added:** 2026-07-02
**Source / Author:** Pasted text — Warp blog, "How to build a cloud software factory" (part 2, spec-driven development). See also: [git/README.md](../git/README.md) — `github.com/warpdotdev-demos/cloud-factory-demo`

**What it's about:** Part 2 of Warp's automated-SDLC "cloud factory" series. Adds a **triage gate** to a Triage→Implementation pipeline: a triager classifies each incoming GitHub issue by roadmap/vision fit + complexity (>~few hundred LOC) + ambiguity (multiple materially-different viable implementations needing human choice), then applies one of four labels — `ready-to-implement` (one-shot), `ready-to-spec` (Spec agent produces PRODUCT.md + TECH.md), `needs-info` (clarify), `wait-to-implement` (defer). Spec agent runs via a label-triggered GitHub Action (Docker + Warp's Oz cloud); specs are checked into a `specs/<issue>` dir; interactive `/grill-me`-style refinement precedes implementation.

**What we added** (one integration — this harness *is* a local SDD harness and already ships the whole spec pipeline: `spec-init→requirements→design→grill→tasks→impl`, `spec-quick`, `spec-grill` = grill-me, `validate-impl`/`validate-adversarial` = validate-changes-match-specs, `.claude/steering/` = roadmap/vision. The only genuine gap was the *upstream routing decision*):
- Skill: `issue-triage-routing` — source-agnostic decision gate applied *before* spec/impl. Classifies a raw issue on roadmap-fit → ambiguity → complexity and routes to DEFER / CLARIFY / SPEC / ONE-SHOT (the four labels, mapped to local entrypoints). Falsifiable thresholds; sharp When/When-NOT triggers for auto-firing. Wired as a pre-gate into `jira-solve` routing and as a pre-check in `spec-quick` so trivial work isn't over-spec'd and off-roadmap work is caught early.
- Rejected: PRODUCT.md/TECH.md split (= requirements+design+tasks, more granular); `/grill-me` (= `spec-grill`); validate-changes-match-specs (= `validate-impl`/`validate-adversarial`); roadmap.md/vision.md docs (= `.claude/steering/`, which the triage skill reads); GitHub Actions + Docker + Oz cloud infra (N/A to a local-first harness); label mechanics (the decision logic behind them is what the skill captures).

---

## Your AI's Memory Is Quietly Making It Dumber (Pasted text)
**Added:** 2026-07-02
**Source / Author:** Pasted text — Matt Van Horn (@mvanhorn)

**What it's about:** Memory files and CLAUDE.md both inject every session and rot; past ~200 lines more text means worse adherence, not more knowledge. Bucket every memory entry as *trash* (git has it) / *skill-tied* (belongs in the skill as a PR) / *cross-cutting* (keep). Audit instruction files ruthlessly: flag files >200 lines, anything the model can infer from the repo (stack, generic filler), and project files that duplicate instructions instead of importing (`@AGENTS.md`). Also draws the push-memory (always-loaded) vs pull-memory (queried on demand) distinction.

**What we added** (one integration — the harness already covers archive-before-delete via glacier, nightly line-caps via housekeeping, and a write-time quality gate via memory-discipline-hook; and auto-memory is load-bearing here so the article's "turn it off" advice was rejected):
- Command: `/claudemd-review` (`commands/global/claudemd-review.md`) — **fills a dangling reference**: `session-start-hook.sh` fires `/claudemd-review` bi-weekly per repo but no command implemented it. Audits the current repo's `CLAUDE.md`/`AGENTS.md` against a lean-context rubric (200-line size budget, "inferable from the manifest" filter, generic-filler test, `@AGENTS.md` import/dedup, stale model-assumption + over-constraining checks), rates each file clean/minor/needs-update, writes `.claude/memory/claudemd-review-report.md`, and stamps `.claude/memory/.last-claudemd-review`. Propose-only by default; `--apply` for low-risk fixes. Distinct from the harness-wide `harness-health-runner` CLAUDE.md pass (all repos → `docs/claudemd-review-report.md`).
- Rejected: disable auto-memory (load-bearing — `skill-augment-agent` reads `type: feedback` auto-memories as its highest-trust signal); archive-before-delete (glacier already does this); memory/CLAUDE.md size-budget hook (housekeeping caps memory nightly; the CLAUDE.md size check folded into the command's rubric); standalone memory-bucketing skill (write-side = the proposed-but-declined learn-eval routing; prune-side = housekeeping); "anything done twice → skill" (skill-extraction + skill-creator already embody it); push-vs-pull external memory tools gbrain/supermemory/mem0/Letta (infrastructure choices, not a harness capability).
- Also proposed but user-declined this run: skills-over-memory routing (a `[seed-target:]` verdict in `learn-eval-agent` to divert skill-tied lessons to `skill-augment` instead of `patterns.md`).

---

## "Continual Learning for Agents" — Replit / pirroh@repl.it
**Added:** 2026-07-08 | **Source:** Pasted text (Replit Engineering)

**What it's about:** Three-layer improvement model for agents that can't touch model weights (model / harness / context). Introduces ViBench (benchmark built from production PRDs + natural-language test plans), Telescope (trace clustering system), and a self-improvement loop (trace clusters → hypothesis → candidate PR → measurement → ship/drop). Identifies three human taste gates — hypothesis selection, eval curation, launch approval — as irreplaceable.

**What we added:**
- Skill augmentation: `loop-patterns` — new Loop 11 "Harness-Improvement Loop" with two mandatory human gates (hypothesis selection and ship decision). Closes the gap where `/kiro:macro-eval-sweep` produces a ranked report that has no defined consumer.
- Skill augmentation: `evaluation/macro` — new Phase 0 "Construct the Population from Traces" (mine production PRDs, pair with natural-language test plans, maintain as living document). Previous skill assumed the benchmark already existed.
- Skill augmentation: `evaluation/macro` — new "Human Handoff — Hypothesis Selection Gate" section after Phase 5. Surfaces top 3 cluster candidates in a structured format and waits for human selection before proceeding to the Harness-Improvement Loop.
- Skill augmentation: `agent-harness-design` — new "Improvement Layer Decision" subsection in Phase 1. Routes diagnosis through harness-layer → context-layer → model-layer in that priority order before assuming weight-level fixes are needed.
- Skill augmentation: `evaluation/micro` — eval-curation note added to Rubric Design section. Makes explicit that rubric dimension/weight choices are product decisions requiring product-owner sign-off, not engineering checkboxes.

---

## "What The New 100x Agentic Engineer Looks Like In The Era Of Fable & GPT 5.6" — @systematicls
**Added:** 2026-07-08 | **Source:** Pasted text (@systematicls on X)

**What it's about:** Argues that agent capability is no longer the bottleneck — human preference-articulation is. Introduces a 2×2 framework (declarative vs. imperative × strategic vs. tactical) for expressing preferences to agents. The core claim: agents default to lowest-common-denominator solutions when preferences are unstated; the 100x engineer knows how to constrain the solution space through explicit preference expression and Socratic dialogue.

**What we added:**
- Command: `/kiro:pref-elicit` (`commands/kiro/pref-elicit.md`) — pre-spec Socratic elicitation command. Runs a structured 5-question protocol to surface declarative/imperative and strategic/tactical preferences before spec-init. Writes `prefs.md` to the spec directory. Wired as mandatory Step 1.5a in `spec-quick` Interactive Mode; recommended pre-check in `spec-init` standalone usage.
- Skill augmentation: `prompt-engineering` — new "Declarative vs. Imperative Preferences" subsection with the 2×2 matrix and routing rules. Maps onto the existing "degrees of freedom" section.
- Skill augmentation: `instruction-architecture` — new "Preference Origin: Strategic vs Tactical Routing" subsection. Maps preference origin onto the existing MUST/SHOULD/CONTEXT tier structure. Tactical preferences in the entry file = primary source of instruction bloat.

---

## "Agentic Autonomy Levels" — @addyosmani
**Added:** 2026-07-28 | **Source:** https://x.com/addyosmani/status/2072885435312042327 (Addy Osmani)

**What it's about:** A two-axis model of agentic autonomy (Agency × Orchestration) across six levels/three eras, with a warning that higher autonomy isn't inherently better — match autonomy to task risk, reversibility, and available verification ("calibrated autonomy"). Introduces the per-run "contract" and four autonomy anti-patterns.

**What we added:**
- Skill augmentation: `skills/agent-harness-design/SKILL.md` (Governance) — "Agent-Run Contract" (goal/scope/tools/stopping/evidence/escalation/budget) + the three questions before granting high autonomy (how fast do problems surface, how cleanly can work be undone, what independently verifies success).
- Skill augmentation: `skills/multi-agent-patterns/SKILL.md` — "Autonomy Anti-Patterns" (autonomy-as-status, permission laundering, summary substitution, fleet cosplay).

---

## "A Field Guide to Fable: Finding Your Unknowns" — @trq212
**Added:** 2026-07-28 | **Source:** https://x.com/trq212/status/2073100352921215386 (Thariq Shihipar, Anthropic)

**What it's about:** "The map is not the territory" — the map is your prompts/skills/context, the territory is the real codebase, and the gap is unknowns. Argues output quality is now bottlenecked by the operator's ability to surface their own unknowns before execution, using the four-quadrant frame (known/unknown × known/unknown) and concrete pre-execution discovery moves.

**What we added:**
- Skill (NEW): `skills/surfacing-unknowns/SKILL.md` — a pre-execution discovery routine with six named techniques (blind-spot pass, design-prototype for tacit taste, architecture interview one-question-at-a-time, reference-as-map, implementation-deviation logging, pre-merge quizzing), a "When NOT to Use" section, and explicit exclusions. Fires before a spec/design/large change; skips well-specified mechanical tasks.

---

## How to Write a Good Skill — SkillsBench + SkillComposer Findings (Pasted text)
**Added:** 2026-07-30
**Source / Author:** Pasted text — citing SkillsBench (arXiv:2602.12670) and SkillComposer (arXiv:2606.06079 skill evolution, arXiv:2606.32025 generative composition)

Five measured findings on skill authoring: (1) models cannot reliably self-author skills — self-generated skills scored 1.3pts *below* no-skill baseline; (2) focused 2-3 file skills beat exhaustive ones — comprehensive skills scored below no-skill baseline too; (3) loading whole skill libraries hurts accuracy AND cost — a 196-skill dump scored 16pts worse at +23% tokens vs a selected subset; (4) skill portability is format-only, not behavioral — gains ranged 4.1–25.7pts by harness, Codex CLI often ignored skills outright; (5) skills should target model-weak domains, not crowded ones — 4.5pt lift in software-eng vs 51.9pt in healthcare, yet 38% of public skills still target the low-yield software-eng trade. Core methodology: A/B test with-vs-without the skill using automated pass/fail scoring, gate any skill change on measured pass-rate improvement rather than authorial confidence.

- New skill: `skills/skill-eval-gate/SKILL.md` — operationalizes finding (1) as a mandatory gate: define ≥3 scenarios, run no-skill baseline vs with-skill treatment, score deterministically, verdict PASS/FAIL/INCONCLUSIVE blocks finalization. Invoked from `skill-creator` Phase 4b and `skill-extraction` Phase 5b.
- Augmentation: `skill-extraction` Step 3e Value Critic Gate — added 6th check "domain-saturation" operationalizing finding (5): candidates targeting crowded software-eng patterns now face a higher bar than candidates targeting model-weak domains; note the trade-off explicitly in the proposal instead of defaulting to more software-eng tooling.
- Findings (2) and (3) reinforce existing harness conventions already in place (module cap ≤3 in `skill-creator` Key Principles, selective skill loading over full-library dumps) — no new artifact needed, cited inline as evidence for those existing rules.
- Rejected: standalone skill-portability tooling for finding (4) — the harness only targets Claude Code, cross-harness portability is out of scope.

---

## "Own the loop / agent-as-a-judge" — @aparnadhinak
**Added:** 2026-07-28 | **Source:** https://x.com/aparnadhinak/status/2073079029624943040 (Aparna Dhinakaran, Arize AI)

**What it's about:** Two adjacent Arize themes: (1) evaluate agent *trajectories*, not just final outputs — agents fail in sequences (stuck loops, dropped context, broken tool calls hidden behind a plausible final answer); and (2) tight model-harness coupling that feels magical is also a portability risk, locking you to one vendor's models/prices.

**What we added:**
- Resource augmentation: `skills/evaluation/resources/benchmark-construction.md` — trajectory evaluation (grade intermediate steps; a valid agent can take different paths to the same goal).
- Skill augmentation: `skills/agent-harness-design/SKILL.md` (Memory) — one-line "harness↔model coupling is portability risk" framing.

---

## "Model and effort in Claude Code: knowing more vs. trying harder" — @claudedevs
**Added:** 2026-07-28 | **Source:** https://x.com/claudedevs/status/2074900291062034618 (Claude Code team — Lydia Hallie)

**What it's about:** Two Claude Code dials that both seem to "make the answer better" do different things: the model changes what Claude *knows* (knowledge, reasoning ceiling); effort changes how much *work* it does before checking back (files read, tests run, verification loops). Diagnostic: when Claude is wrong, ask "did it not know enough, or did it not try hard enough?"

**What we added:**
- Skill augmentation: `skills/model-tiers/SKILL.md` — "Model vs. Effort — Which Dial to Turn" diagnostic (not-knowing → bump the model; not-trying → raise effort; fix context first).

---

## Husband's scope redirect — automate session-handoff, PR-babysit, self-improving review before touching the GitHub-repo listicle (Pasted text)
**Added:** 2026-07-29 | **Source:** direct instruction, not a URL

**What it's about:** While reviewing a skill-extraction proposal for a list of 10 external GitHub repos, Husband redirected scope with a 4-point directive: (1) session handoff between agents/compaction must be fully automatic, never manually invoked — build it as a hook; (2) PR babysitting must auto-open a PR against the real base branch (any base, not just main/dev) on push or on PR mention, then run automatically; (3) automatic PR creation now gives a real feedback signal, so build the previously-shelved self-improving code review as a hook/scheduled routine that adjusts memory or methodology; (4) only after 1-3 are built and tested, fan out 10 sub-agents with `skill-extraction` over the original 10-repo listicle.

**What we added:**
- New: `scripts/session/write_handoff.py` — parses the live JSONL transcript into a structured markdown handoff brief.
- Hook augmentation: `hooks/claude/compaction-discipline-hook.sh` and `hooks/claude/gbrain-agent-spawn.sh` — both now write a fresh handoff snapshot (`--trigger precompact` / `--trigger agent-spawn`) before their existing soft-nudge text.
- Hook augmentation: `hooks/claude/session-start-hook.sh` — surfaces a `[SESSION-HANDOFF-AVAILABLE]` sentinel when a <24h-old handoff snapshot exists, closing the write→read loop with no manual step.
- New: `scripts/pr/detect_base_and_create.sh` — auto-detects the real fork-point base branch (most-recent merge-base wins, no hardcoded branch list) and idempotently opens a draft PR via `gh`.
- New hooks: `hooks/claude/pr-auto-create-hook.sh` (`PostToolUse:Bash`, fires on a successful plain `git push`) and `hooks/claude/pr-mention-nudge.sh` (`UserPromptSubmit`, fires when a PR is mentioned in conversation).
- Config change: `templates/settings.json.template` — allow plain `git push`, deny only `--force`/`-f`, so the push-triggered hook can actually observe a successful push.
- New: `scripts/routines/code-review-learning-prompt.md` + `scripts/routines/code-review-learning-runner.sh` — weekly-paced sweep comparing pr-babysit's logged reviews against real human PR review activity; writes low-risk findings straight to `.claude/memory/`, reports higher-risk methodology changes for human approval in `docs/code-review-learning-report.md`. Wired into `scripts/orchestration/daily-orchestrator.sh`.
- Deferred: item 4 (the 10-repo listicle fan-out) — not started; will get its own `docs/sources/git/README.md` entries per repo once run.

---

## "Compound Engineering v3.20" update (Pasted text)
**Added:** 2026-07-30 | **Source:** direct paste, not a URL

**What it's about:** A ~July 22, 2026 update on compound-engineering practice — background CI/review babysitting on auto-created PRs (`ce-babysit-pr`), decision-cost-sized PR descriptions instead of diff-sized ones, cross-model judge panels, and a predict-then-reveal teaching mode for closing comprehension debt on agent-written diffs. Audit found most headline items (session handoff, self-improving code review, PR auto-creation) were already built two days earlier (see 2026-07-29 entry above); model-routing and judge-panel proposals were rejected as already covered by the `Agent` tool's `model` param and the `Workflow` tool's judge-panel pattern respectively.

**What we added:**
- New: `skills/pr-babysit/SKILL.md` — Monitor-tool background watch of a PR's CI/reviews after auto-create (replacing `iterate-pr`'s blocking `gh pr checks --watch` loop), plus a branch-currency check (`git merge-base` staleness) and an explicit authority boundary (fix/commit/push/reply pre-authorized; merge/rebase/force-push/CI-approval never authorized). `better-call` verdict: MERGE against incumbent `iterate-pr` — its 9-step CI/feedback triage is referenced/invoked from within `pr-babysit` rather than duplicated.
- New: `skills/diff-teach/SKILL.md` — two-turn predict-then-reveal drill for diffs/commits/time-windows: show the change, get the user's prediction, end the turn, then name exactly what the prediction missed. `better-call` verdict: MERGE against incumbent `code-documentation-code-explain` — that skill stays the single-turn explainer; `diff-teach` is used only when the user wants to be tested, not told.
- Hook/script augmentation: `scripts/pr/detect_base_and_create.sh` — redirected its post-create babysitting pointer from `iterate-pr` to `pr-babysit`; added a `PULL_REQUEST_TEMPLATE.md`-aware instruction to rewrite the PR body sized to reviewer decision cost (not diff size) before `gh pr edit --body`.
- Synced both new skills to `~/.claude/skills/` for immediate use; both passed the SkillOS Quality Gate and the `agent-identity` Mode B check with no fixes needed on first pass.
- Rejected: cross-model judge-panel skill (`ce-pov`) — hollow wrapper around the `Workflow` tool's existing judge-panel pattern. Cross-model routing skill — hollow wrapper around the `Agent` tool's `model` param. Four other candidates rejected as already covered by existing harness work (see 2026-07-29 entry) or too thin to justify a new artifact.

---

## Context Engineering Rules for Claude 5 Models (Pasted article)
**Added:** 2026-07-30 | **Source:** Pasted text — Anthropic-style article on system-prompt minimization, rules-over-examples, progressive disclosure, auto-memory, and rich references

**What it's about:** Guidance for context engineering under Claude 5: minimize system-prompt bulk and trust model judgement over enumerated rules; prefer interfaces/examples over long prose; disclose detail progressively rather than front-loading it; let auto-memory replace CLAUDE.md-as-memory-dump; and prefer rich, executable references (tests, reference implementations) over plain markdown specs when correctness criteria are clear.

**What we added** (two augmentations — the harness already implements most of the article's advice: bi-weekly `claudemd-review` (both the harness-source `commands/global/claudemd-review.md` and the untracked, hook-triggered `~/.claude/skills/claudemd-review/SKILL.md`) covers system-prompt minimization and progressive disclosure; weekly `skill-curator` covers example-over-rule pruning; `skill-extraction`'s own SkillOS Quality Gate covers compression; and the harness's auto-memory system already exists and is lean. Only two narrow gaps survived the Value Critic Gate):
- Audit check: **Conflicting Instructions** — added to both `commands/global/claudemd-review.md` (new rubric row, needs-update if load-bearing / minor if cosmetic) and `~/.claude/skills/claudemd-review/SKILL.md` (new Phase 2 subsection). Flags pairs of lines — within one CLAUDE.md, or across CLAUDE.md/AGENTS.md/skills — that pull in opposite directions on the same decision, forcing the model to arbitrate every time instead of acting directly. Closes a detection gap the existing rubric didn't cover (stale/over-constraining/duplication were covered; direct contradiction was not).
- Guidance: **Rich references over plain markdown** — one line added to `templates/CLAUDE.md.template`'s spec-gate rule: prefer an executable spec (failing test suite) or reference implementation over plain markdown when the feature has clear correctness criteria; markdown stays the default for open-ended/UX work. Ships to every new/registered project.
- Noted, not fixed: pre-existing drift where `~/.claude/skills/claudemd-review/SKILL.md` (the live, hook-triggered version) has no harness-source counterpart and won't propagate via `update.sh` — flagged to the user, out of scope for this extraction.
- Rejected: system-prompt minimization framing (harness CLAUDE.md files are already lean per prior `claudemd-review` runs and the memory-quietly-making-it-dumber extraction); rules→judgement / examples→interfaces (already the SkillOS Quality Gate's content-quality dimension); progressive disclosure (already `skills/<name>/resources/` pattern + `ctx_read` mode selection); auto-memory replacing CLAUDE.md-as-memory (harness already uses auto-memory as primary store, CLAUDE.md is instruction-only, not a memory dump).

---

## Loop vs Graph (marketing edition) — Pasted text
**Added:** 2026-07-30 | **Source:** Pasted text — marketing-oriented explainer of agent loops vs agent graphs

**What it's about:** Framing for when to structure agent work as a loop vs a graph: a loop is agent-owned path within a fixed goal/bar; a graph is a pre-drawn state machine of steps and routes, worth the setup on recurring jobs with validation gates, fixed routes, and a clear failure point. Illustrated with a 3-session marketing pipeline (research → landing page → content) with a critic loop nested inside one node.

**What we added** (ran `better-call` — `loop-patterns`' existing "when a loop should become a graph" gut-check covered >70% of this already; verdict: AUGMENT INCUMBENT, gap = 1 dimension, complementarity delta):
- Augmentation: `skills/loop-patterns/SKILL.md` — gut-check extended from 4 questions/0-4 scoring to 6 questions/0-6 scoring. Added two triggers the existing checklist lacked: (5) repeating cadence, not a one-off; (6) need to see the exact step something broke on, not just re-run the whole loop. Also added a pointer that a graph promotion should land on the `Workflow` tool's `pipeline`/`parallel`/`phase` primitives rather than hand-rolled state logic.
- Rejected: "who decides the path" framing (redundant with incumbent's existing "a loop is one node in a graph" line); n8n analogy and the marketing pipeline example (off-domain for a software-engineering harness).

---

## Self-Improving Code Review Agent — Schema-Controlled review.json + Two-Job Least-Privilege Action (Pasted article)
**Added:** 2026-07-30 | **Source:** Pasted text — article on a self-improving code review agent (schema-controlled `review-pr` skill, two-job GH Action, `improve-review-pr` outer loop)

**What it's about:** Three-part mechanism for automated PR review: (1) a schema-controlled review skill emitting `review.json` (verdict, structured body, comments array with diff-line-annotation-only placement and 4 severity prefixes), never posting directly; (2) a two-job least-privilege GitHub Action splitting read-only review generation from write-permission publishing, so a prompt-injection payload in PR content can't reach posting credentials; (3) an outer loop that learns from the gap between logged reviews and real human review activity. Husband approved items 1-2 as proposed and redirected item 3: reject the from-scratch-skill framing, compose it from existing skills instead. Also requested dashboard visibility for the new mechanism.

**What we added:**
- Script: `scripts/pr/log_review.sh` — headless review driver (PR number → `.claude/memory/pr-reviews/pr-<n>.md` + `.review.json`), reusing the `code-review-learning-runner.sh` headless-Claude invocation pattern.
- Wiring: `scripts/pr/detect_base_and_create.sh` — backgrounded call to `log_review.sh` right after PR auto-creation (`nohup ... &`), replacing the old advisory-only echo pointing at a skill the user had to remember to invoke.
- Skill augmentation: `code-review-learning-prompt.md` Phase 2 — added a finer 4-way tag (validated/corrected/refined/ambiguous) and a decision tree mapping each tag combination to `no_changes` / `update_review_pr_local` (memory write) / `update_review_pr` (pending human approval) / `both`. This is item 3's outer loop, composed from the harness's *existing* `code-reviewer` skill + `code-review-learning-*` sweep rather than a new skill — satisfying Husband's redirect directly.
- Skill augmentation: `skills/gitnexus-pr-review/SKILL.md` — new "Structured Output Contract (review.json)" section: full schema, 4 severity prefixes, diff-line-annotation-only placement rule, fix-validated-against-real-tools rule, validator invocation, and an explicit never-runs-`gh pr review`/`gh pr comment`/`gh api .../reviews` boundary. This is item 1, landed as an augmentation of the existing review skill rather than a new one — following the same "compose from existing" principle Husband applied to item 3.
- Script: `scripts/pr/validate_review_json.py` — stdlib-only schema validator; exits 1 with one `error:` line per failure, silent exit 0 on success.
- Workflow: `.github/workflows/review-pull-requests.yml` — item 2's two-job least-privilege Action (`review`: `contents:read`/`pull-requests:read`, produces the artifact; `publish`: needs `review`, `pull-requests:write`, re-validates then posts via `gh api`). Coexists with the existing `claude-code-review.yml` (different mechanism, not a replacement).
- Dashboard: `scripts/utils/dashboard.py`'s `render_automation_audit()` — added a 5th event source scanning `.claude/memory/pr-reviews/pr-*.md` and `docs/code-review-learning-report.md` sweep sections into the existing unified timeline, closing Husband's visibility ask without a new widget.

**better-call verdicts (item 3 — composing the review mechanism from existing skills):**
- vs. `.github/workflows/claude-code-review.yml` (official plugin, opaque, no schema) → **COEXIST** — different mechanism (structural/schema-controlled vs. black-box plugin), both ≥18/30, complementarity delta 5.
- vs. `skills/gitnexus-pr-review/SKILL.md` (blast-radius/impact-analysis review) → **AUGMENT INCUMBENT** — challenger's schema/safety-boundary contract grafted on; its review *mechanism* (graph impact analysis) stays gitnexus's own, unreplaced.
- vs. `~/.claude/skills/code-reviewer/SKILL.md` (global generic checklist, not harness-owned) → out of scope for augmentation (not harness-owned); left untouched, cited as the SKILL.md's documented CI fallback instead.
- Rejected: a fourth standalone `review-pr` skill built from scratch — the from-scratch framing itself, per Husband's explicit redirect.

---

## Claude Code native subagent `memory:` frontmatter (Pasted text)
**Added:** 2026-07-30 | **Source:** Pasted text — Claude Code docs excerpt on the `memory:` subagent field (`user`/`project`/`local`)

**What it's about:** Claude Code's native per-subagent auto-memory: adding `memory: project` (or `user`/`local`) to a subagent's frontmatter creates `.claude/agent-memory/<name>/MEMORY.md`, auto-injected on every invocation of that subagent — no manual save/recall calls needed. Husband's follow-up directive after approving the initial 2-item proposal broadened scope: fix three side gaps found while auditing the harness's *existing* memory loop (`ctx_knowledge`, `hot-memory.md`/`patterns.md`), and make the fix genuinely mechanical — "memory needs to be DYNAMIC and NOT triggered manually," not dependent on an LLM remembering a prose step.

**What we added:**
- Frontmatter: `agents/kiro/harness-fix-agent.md` and `agents/kiro/skill-augment-agent.md` — both gained `memory: project`, giving each a persistent `.claude/agent-memory/<name>/MEMORY.md` auto-loaded on every future invocation instead of starting cold each run.
- Hook (NEW): `hooks/claude/agent-trace-hook.sh` (`PostToolUse`, matcher `Agent`) — `trace.log` had zero reliable producers (population was a prose instruction buried in two rarely-run commands, so the file never got created). This hook writes a trace entry on every subagent spawn — agent name, model tier, derived outcome (`pass`/`error`/`dispatched` for backgrounded spawns), duration-hint — deterministically, with self-archival past 200 lines to `glacier/`. Registered in `templates/settings.json.template`.
- Hook augmentation: `hooks/claude/stop-hook.sh` — learnings.jsonl promoter block. `reflect-agent`'s Step 6 told the LLM to append a curated learning at the end of a 6-step agent; attention decayed and the file was never created. This mechanically promotes today's highest-signal `observations.md` entry (ranked by tag: judge > skill-update > skill-update-flagged/-repair > seed-target > memory-gap > loop-debt > stale-action-item, else skipped as noise) into `learnings.jsonl`, but only if reflect-agent's own manual write for today doesn't already exist — the human/LLM-curated entry always wins.
- Hook augmentation: `hooks/claude/stop-hook.sh` — stale-action-item escalator block. `action-items.md` due-dates were never mechanically checked; items sat silently past due until a human happened to re-read the file. This parses the `- [ ] <desc> | due:YYYY-MM-DD` format and, once per day, appends a `[stale-action-item]` observation for the most-overdue item, routing it through the same review loop as every other signal.
- Verification: both `stop-hook.sh` and `agent-trace-hook.sh` pass `bash -n` and are `chmod +x`'d.
- Rejected: nothing — the two-item native-memory proposal was approved as-is; the three side-gap fixes were an explicit follow-on directive, not independently critic-gated candidates.

---

## PostHog — "How much can you delegate to agents?" (Pasted text)
**Added:** 2026-07-30 | **Source:** Pasted text — PostHog engineering blog on a 2-axis agent-delegation framework

**What it's about:** A checkability-axis × reversibility-axis framework for deciding how much to delegate to an agent: is the work easy or hard to verify deterministically, and is a mistake cheap or costly to undo. Four resulting levels (L0 agent-as-assistant, L1 human-in-the-loop, L2 agent-delegation — default ceiling for most dev work, L3 self-driving) each carry distinct "how to level up" moves. Ran `better-call` against incumbent `agent-permissions-design` (existing reversibility-only write-class table + 7-rung Ladder of Agency): verdict **AUGMENT INCUMBENT** — the challenger's checkability axis and per-level level-up moves were genuinely new; its scope-inheritance/SoR/audit machinery was pure overlap.

**What we added:**
- Skill augmentation: `skills/agent-permissions-design/SKILL.md` — new "Delegation Ceiling (Checkability × Reversibility)" section inserted before the existing Ladder of Agency. 2x2 quadrant table crossing the new checkability axis with the existing reversibility axis; per-quadrant level-up moves (L0 decompose, L1 LLM-as-judge + scoped success contracts, L2 guardrails-as-code citing already-shipped `hooks/claude/agent-behavior-guard.sh` + the destructive-git/gh block hook, L3 sharpen scout-trigger signal citing already-shipped `daily-orchestrator.sh`/`pr-babysit`/`background-work-routing`); closing note reconciling the new 2x2 with the existing 7-rung ladder rather than leaving two disconnected models in one file.
- Rejected: Scouts/self-driving background-agent pattern (already `daily-orchestrator.sh` + `ScheduleWakeup`/`CronCreate` + `pr-babysit` + `background-work-routing`); domain-specific verifier models + expert context banks (already `rag-architect`/`agent-memory-systems`/`context-driven-development`); guardrails-as-code as a new hook (already shipped, cited as evidence instead); standalone "delegation ceiling" skill (would duplicate `agent-permissions-design`'s territory almost entirely — augmentation was the correct verdict, not coexistence).

---

## Cache-dominated API costs in long orchestrator sessions (Pasted text)
**Added:** 2026-07-30 | **Source:** Pasted text — article on Claude Code API costs, long orchestrator sessions

**What it's about:** In long-running Claude Code orchestrator sessions, 84% of API cost after compaction can come from `cache_read`/`cache_creation` tokens rather than fresh input/output — compaction only briefly resets the ratio before it climbs back up.

**What we added:**
- Dashboard: `scripts/utils/dashboard.py` — new "% cost from cache" stat.
- Hook augmentation: `hooks/claude/stop-hook.sh` — captures `transcript_path` from Stop-hook stdin; detects when cache tokens (read+write) hit ≥70% of total session tokens *and* the transcript shows ≥1 compaction (`isCompactSummary`/`compactMetadata`). When tripped, automatically calls `write_handoff.py --trigger cache-cost` to write a resumable session snapshot to `.claude/memory/handoff/latest.md` — unconditionally, not gated on the user reading/acting on a nudge.
- Script augmentation: `scripts/session/write_handoff.py` — extended `--trigger` choices with `cache-cost`; docstring updated to mention the new Stop-hook caller. `scripts/README.md` updated to match.

**Rejected:**
- Standalone skill teaching Claude to watch for cache-cost dominance — rejected per Husband's explicit pushback ("might get pulled" doesn't fix a mechanically-detectable condition); a hook enforces every time a skill doesn't.
- Blocking Stop hook (`decision: block`) forcing `/compact` the instant the ratio trips — rejected as disproportionate: overrides the user's explicit stop intent across every project/session, and the source article itself notes compaction only briefly resets the ratio, so the fix is marginal against a large blast radius.

---

## "The harness is all you need (mostly)" — @burkeholland (Pasted text)
**Added:** 2026-08-02 | **Source:** Pasted text — GitHub Copilot workflow post (prototype → plan → autopilot → review → rubber duck → commit)

**What it's about:** An 8-step GitHub Copilot workflow: pick a tool, enable YOLO/allow-all mode, generate cheap tangible prototypes before committing to a design, plan methodically (grill-me-style clarifying questions), implement via an autopilot loop with automatic subagent routing, human-review and iterate, get a "rubber duck" review from a model of a different AI family before committing (different training data catches different blind spots), then commit.

**What we added** (one augmentation — the harness already covers steps 1/2/4/5/6/8 via existing YOLO-mode permission settings, `brainstorming`/`spec-grill`/`pref-elicit`, native subagent routing + `TaskCreate`/`spec-impl`, and ordinary iteration. Ran `better-call` on the rubber-duck step against incumbent `/kiro:validate-adversarial`; verdict AUGMENT INCUMBENT (challenger's only edge was genuine cross-model-family diversity vs. validate-adversarial's same-family three-pass). Husband overrode even that: this harness has no funded/available path to a non-Claude model, so a cross-model loop is moot regardless of the verdict — rejected outright):
- Agent augmentation: `agents/kiro/idea-refine-agent.md` Step 3 (Divergent Exploration) — added a "concrete-over-abstract rendering" rule: when the idea is visual/interface-shaped or has many plausible shapes, render the 2-3 approaches as a Mermaid diagram instead of plain bullets, since tangible comparisons surface reactions abstract text doesn't. Judgment-gated — skipped for backend-logic/single-path ideas.

**Rejected:**
- Cross-model "rubber duck" review loop (augment `blockrun` with an iterate-to-convergence pattern) — `better-call` verdict AUGMENT INCUMBENT vs. `/kiro:validate-adversarial` (13/30 vs 22/30, complementarity delta the only edge), then rejected outright by Husband: no non-Claude model is actually reachable in this harness, so the augmentation would be dead documentation.
- YOLO/allow-all mode, methodical planning, autopilot loop, human review/iteration, "pick a tool" framing — already covered by existing harness mechanisms (see reasoning above); no artifact needed.

---

## "7 rules for self-improving agent loops every AI engineer should know" — agents-cli (Google Cloud ADK) (Pasted text)
**Added:** 2026-08-16 | **Source:** Pasted text — article by @Saboo_Shubham_ and @secchi_elia introducing `agents-cli`

**What it's about:** A self-improving agent loop (propose fix → grade against an author-owned metric → revise from the reason) built on Google's `agents-cli`/ADK: `eval generate` → `eval grade` → `eval compare`. Argues the loop can automate everything except defining what "good" means — that definition has to stay a human-authored, versioned metric the loop cannot move. Lists 7 discipline rules: start with one case not a suite; make judges explain themselves; use code for deterministic checks; score behavior not exact trajectory; treat a flaky case as a finding, not noise to delete; never let the proposer move the bar (lowered threshold / edited expected output / dropped case) — guard with a held-out slice; auto-optimize prompts once, at the end, not in a loop.

**What we added** (ran `better-call` — challenger 17/30 vs incumbent `iterative-repair-loop` 26/30; verdict AUGMENT INCUMBENT: incumbent wins on structure/automation but 2 ideas were genuinely absent):
- Skill augmentation: `skills/iterative-repair-loop/SKILL.md` — added a **held-out set** rule to Phase 0 (reserve ~20% of validation cases untouched during iteration, re-run only after convergence; a visible-case pass that fails held-out cases is a gamed rubric, scored FAIL not a partial win) and a **flaky-result rule** to Phase 3 (a case flipping pass/fail across two identical re-runs stops the loop and is reported as a finding — non-determinism in the artifact or judge, not something to average away). Report template's Outcome line extended with the two new stop conditions.

**Rejected:**
- The `agents-cli` CLI / ADK eval generate-grade-compare tooling itself — Google-specific infra for ADK-built agents; nothing in this harness builds ADK agents, no wiring path.
- Rule 2 (judges explain themselves) — already covered by `skill-eval-gate` Phase 5 and `iterative-repair-loop`'s existing `remaining_delta.description`/`fix_direction` fields.
- Rule 3 (code for deterministic checks) — already covered by `skill-eval-gate` Phase 1's "deterministic pass/fail check" requirement.
- Rule 4 (score behavior not exact trajectory) — already covered in spirit by `agent-evaluation`'s "Output String Matching" anti-pattern.
- Rule 7 (auto-optimize once, at the end) — already covered by `iterative-repair-loop`'s default `max_iterations = 3` + stall detection.
- "Custom metric as durable, human-owned artifact" — already covered by `skill-eval-gate`'s rubric-authoring step and `agent-evaluation`'s general framing.
- Production monitoring via the same metric over live traces (BigQuery) — infra-specific to Google Cloud, no equivalent surface in this harness; overlaps `raindrop-eval-loop`/`active-observability` territory without a concrete wiring path here.

---

## Subagents on Subagents: How Many Layers Deep Is Too Many? (Pasted text)
**Added:** 2026-08-16
**Source / Author:** Pasted text — article on multi-agent delegation depth

**What it's about:** Argues nesting depth is the wrong risk signal for recursive subagent delegation — the real signal is graph position/blast radius: how many downstream nodes inherit an artifact if it's wrong. Frames this as "dependency engineering" — deciding what's allowed to depend on what, and gating high-fan-out nodes (verifier, structured output, evidence-travels-with-conclusion, N-way independent production, human approval) proportionally to their downstream influence rather than applying uniform depth-based caution.

**What we added:**
- Skill augmentation: `skills/multi-agent-patterns/SKILL.md` (Skill Routing Quality section, after "Post-condition coupling pattern") — added "Scale scrutiny to blast radius, not nesting depth" heuristic. Ran `better-call` first (challenger 19/30 vs incumbent 25/30, complementarity delta 4/5) → verdict AUGMENT INCUMBENT: incumbent already covers error propagation, post-condition coupling, and summary-substitution, but lacked an explicit rule tying gate strength to a node's downstream fan-out count. Extracted just that idea; rest of article (graph-vs-tree framing, generic error-propagation discussion) already redundant with existing content.

**Rejected:**
- Standalone skill on "graph engineering" / dependency engineering — full article content ~70%+ subsumed by `multi-agent-patterns` (hierarchical layering, error propagation, post-condition coupling, summary substitution anti-pattern); `better-call` verdict was AUGMENT not COEXIST or ADOPT.
- Hook or dashboard integration — this is a design-time judgment call made when architecting a multi-agent system, not a runtime-detectable condition; no automation path exists (correctly "not automatable — contextual" per Step 3d).

---

## Seven X posts from the 2026-08-26 sweep (via archive.codenewsletter.ai mirrors)
**Added:** 2026-08-26
**Source / Author:** Seven posts, all reached through `archive.codenewsletter.ai` mirrors rather than x.com directly (recourse-ladder rung 5). The mirror reproduces post text and metadata; treat as archive, not primary.

**What they're about:**

1. **`@trq212` — the ELI5 command.** An internal Anthropic favorite: `/eli5 <topic>` explains "to someone who knows nothing about this topic, using a HTML artifact with big pictures and few words." Installable as `eli5@claude-community`. Used for explainers and incident digs.
2. **`@kunchenguid` — "Your AGENTS.md is a Neural Net"** plus the open-sourced **backpass** (`npx -y backpass`). Thesis: user-level AGENTS.md is hand-written; project-level should never be authored directly — the tool samples prior agent sessions in a repo, distills learnings and losses, and generates "a gradient descent step as a proposal" for human review.
3. **`@Sambhav_Gandhi` — ownership erosion.** Teams accept thousands of generated lines they barely read, the repo becomes "a black box," and engineers are later asked to debug systems they never understood.
4. **`@GoogleCloudTech` — four pillars of token engineering.** Cache and prune agent loops rather than resending tool output (up to 75% off cached input); batch async work for a flat 50%; set explicit thinking budgets ("3,000 reasoning tokens on a task needing 50"); right-size the model via continuous evals and route through a gateway.
5. **`@AndrewYNg` — AI Engineering Skills Map.** Six sub-skills for building/deploying AI apps; central claim is that reliable systems get built from unreliable components, and that the disciplined evals-and-error-analysis loop is "the most important trait" separating strong AI builders.
6. **`@noahinsf` — agenttrail** (`github.com/sodiumsun/agenttrail`). Local observability map for Claude Code, Codex, and Cursor: live plan, tool calls, file changes, and drift from the original plan, born from "coming back 30 minutes later with no idea where they were in the repo."
7. **`@_avichawla` — TrueForge** (TrueFoundry, MIT). Cut agent tokens 2.7×. Five levers: deferred skill and tool-schema loading, offloading large tool responses to a sandbox file leaving a preview plus path, subagent summarization, Code Mode, and compaction past 50k. Benchmarked on 14 Enterprise-Bench tasks: tied on task success against Claude Managed Agents and deepagents, ~40% of the former's tokens, 40 minutes per run vs 63. "A better reasoning model cannot stop the harness from sending a 50,000-token payload sixteen times."

**What we added:** nothing from any of the seven.

**Rejected, individually:**
- **ELI5** — hollow. `/eli5 how does this module work` is a prompt, not a capability; `diff-teach`, `gitnexus-exploring`, and `tutorial-engineer` already hold this ground.
- **backpass** — ~80% covered by `evolve-agent` (rules audit plus alignment analysis plus prompt-diagnosis over `trace.log`), `reflect-agent`, `learn-eval`, and `code-review-learning-runner.sh`, every one of which already runs sample → distill → propose-under-human-approval on a daily cadence. Its one distinct edge, sampling raw session transcripts to propose CLAUDE.md edits, lands within reach of `evolve-agent`'s existing trace analysis. Vendoring an `npx` tool to duplicate a loop that already runs is negative value.
- **Ownership-erosion post** — commentary with no mechanism to extract.
- **Token engineering** — pillars 1 and 4 are `lean-ctx` and `model-tiers` / `context-budget`. Pillars 2 and 4's gateway are **structurally unavailable**: batch API and gateway routing need an API key, and every call here rides the Claude subscription. Proposing them would fabricate a capability.
- **AI Engineering Skills Map** — a taxonomy. Documentation only, hollow by the Step 3e check.
- **agenttrail** — ~70% covered by `.dashboard/`, `agent-trace-hook.sh`, `action-capture.sh`, and `active-observability`. Its distinct feature is *live* drift-from-plan mid-run; adopting it means a second daemon and a second UI beside an existing dashboard, which the maintenance-cost check does not justify for a single-user setup.
- **TrueForge** — the harness already runs its two headline levers, which is why this is a skip rather than a gap. lean-ctx's firewalled-output-plus-archive-path *is* offloading; `ctx_call`'s ~66 trimmed tool schemas *are* deferred loading; `compaction-discipline-hook.sh` covers compaction. Adopting a second harness underneath this one is a rewrite, not an extraction. The closing line is worth keeping as a principle: cost is a runtime property, not a model property.

See also: [git/README.md](../git/README.md) and [articles/README.md](../articles/README.md) — same 13-source sweep, 2026-08-26.

---

## Sweep of three codenewsletter-archived X posts (2026-08-30)
**Added:** 2026-08-30
**Source / Author:** Three `archive.codenewsletter.ai/<id>` URLs. **Classification note worth keeping:** despite the newsletter domain and the campaign names in their tracking params, these are **not** newsletter issues — the numeric path segment is an X status id and each page is a mirror of a single post. Two of the three were fetched twice with different prompts and the passes agreed, so the content is confirmed rather than confabulated. Direct `x.com/i/status/<id>` returns 403 unauthenticated; the archive mirror is the working rung.

**What it's about:**

1. **Arpit Bhayani — choose speed vs. comprehension file by file** (`2092103965655867831`, ~60.7K views). ~200 words arguing that when agents write large portions of a codebase, the speed/comprehension tradeoff should be picked per file rather than globally. Speed-first for one-off scripts, internal tooling, prototypes; scrutiny-first for auth, payments, data integrity, where agent output is "a first draft" only — review line by line, trace interaction with the wider system, require justification before merge.
2. **Viv Trivedi (LangChain) — how we build agent environments & tasks** (`2092266609838604368`). A practitioner writeup on manufacturing eval benchmarks at scale, packaged as an installable `eval-engineering` skill. Two-stage pipeline: generate a markdown *spec* first, compile it into an executable Harbor task second, so humans review markdown instead of raw code and mock data. Splits a **world spec** (knowledge constant across the dataset) from **task specs** (per-task environment, inputs, rubric). Bootstrap is iterative, not top-down: build task #1 by hand, have the agent write the world spec *from* it, then build task #2 in a fresh thread using only that world spec to validate it.
3. **Mehul Mohan — AI-era engineering interviews** (`2092966591495573717`, 5-post thread). Bug-spotting puzzles are obsolete because fixing is no longer the scarce skill; noticing something needs fixing is. Replacement format: short background/opinions intro, then whiteboard a real company problem while thinking aloud.

**What we added:**
- Augmentation: `skills/skill-eval-gate` Phase 1b — **model-tier difficulty calibration** (from source 2). Before trusting any with/without lift number, run the baseline on a *strong* model and the treatment on a *weak* one, and read the result as a property of the **scenario**, not the skill: strong-baseline passes → too easy, any later lift is noise; weak-treatment fails everywhere → too hard, lift is unmeasurable; strong scoring *worse* than weak → a reward hack or a broken checker, stop and fix the check. This plugs a real hole: the gate already said "scenarios must vary in difficulty" but gave **no mechanism to verify** that spread, so it measured lift on scenarios of unknown difficulty — which is precisely how a gate manufactures a fake pass. The harness's own `model-tiers` made the two-point probe free to implement. Two new INCONCLUSIVE rows were added to the Phase 5 verdict table so a skipped calibration cannot silently pass.

**Rejected:**
- **Source 1, in full.** Its one idea — tier review depth by stakes — is already the harness's default posture, and implemented more rigorously: `CLAUDE.md`'s blast-radius and reviewer-model-mismatch rules, `model-tiers`, the mandatory GitNexus `impact` call (real call-graph analysis, not an exhortation to "trace the change"), plus `code-review-checklist`/`architect-review`/`validate-adversarial`. A "high-stakes path map" hook (`auth/`, `payments/` → forced deep review) was the closest thing to a gap — `protected-path-hook.sh` guards secret *files* but not high-stakes *source* directories — and was rejected anyway, because the post proposes no such mechanism (it would have been my invention), the path list is per-project curation that decays into noise, and dynamic `impact` risk scoring strictly dominates a static glob list. Also rejected as actively harmful to encode: "deliberately don't understand throwaway code" is a blanket comprehension waiver an agent could self-apply to anything it labels internal tooling.
- **Source 3, in full.** An opinion thread on running job interviews, with no technical mechanism and no overlap with an agent harness's domain. "Reasoning traces" appears in its plain-English human sense, not the LLM-trace sense — reading it otherwise would be a homonym error. Every conceivable candidate (an `/interview` command, a detect-before-fix hook, a stale-model-ID checker) required supplying the entire mechanism myself, which is a rejection, not a candidate.
- **From source 2:** world-spec/task-spec separation as a new skill — the harness already has this structure under different names (`.claude/steering/` is the world spec, `specs/` is the per-task spec, skill `resources/` is the shared-knowledge split); renaming an existing structure is not a capability. Spec2Task and the Harbor task format (`task.toml`, container network policies, verifier images) — enormous Docker machinery for a repo whose test convention is `*.test.sh` and throwaway-tree runs. Synthetic environment construction (mock Salesforce/Notion/Gong) — pure LangChain-shop domain; nothing here to mock. Trace-mining to derive eval scenarios — already covered twice, by `evaluation/micro`'s Error-Analysis Bootstrap and `raindrop-eval-loop` Phase −1, both more specific. Leaky-abstraction trajectory review — real but thin; it collapses into "read the trajectory," which `evaluation/long-trajectory` already prescribes, and it is one bullet inside the calibration augmentation rather than its own artifact. The `eval-engineering` skill itself — **skills.sh truncates its body behind a "Show more" gate and it was never read**; recommending installation of a skill whose contents are unseen would violate the no-blind-adoption rule.

See also: [git/README.md](../git/README.md) and [articles/README.md](../articles/README.md) — same 8-source sweep, 2026-08-30.

---

## Three codenewsletter-archived X posts (2026-09-06 six-source batch)
**Added:** 2026-09-06
**Source / Author:** Three `archive.codenewsletter.ai/<id>` URLs, all arriving with `openai-s-new-model-has-breakthrough-3d-modeling-powers` campaign params. Same classification as the 2026-08-30 sweep above: the numeric path segment is an X status id and each page is a mirror of a single post, not a newsletter issue. All three fetched at rung 1.

- `2095460192871698728` — **Matt Pocock** (@mattpocockuk), 2026-09-03, ~617.7K views
- `2094955084123910297` — **Dan McAteer** (@daniel_mac8), 2026-09-02, ~158.2K views, crediting @RLanceMartin
- `2095549949870289285` — **Google Cloud Tech** (@GoogleCloudTech), 2026-09-03, ~24.9K views, written by Praveen Rajasekar (@tweetpraveen)

**What it's about:**

1. **Pocock — the `show-me` skill.** A short recommendation post calling dexhorthy's `show-me` "a phenomenal skill" that "Makes PR descriptions extremely easy to read," described as "Basically a toolbox of 'nice ways to look at code'." Links `humanlayer/skills` at `plugins/show-me/skills/show-me/SKILL.md`. The skill itself is a format-selection table — pseudocode for algorithms, call trees for runtime flow, component trees for UI, shallow file trees for refactors, Mermaid for interaction, `diff` blocks for changes against existing shape, a single focused HTML file for dense visual comparison — under the rule "Pick the smallest view that makes the key point clear."
2. **McAteer — five Fable 5.1 optimizations.** A numbered list of cost optimizations. **Low-fidelity retrieval:** the page fetched, but the five items came back condensed into command names that do not resolve against any documented command, so the wording is not trustworthy and is not quoted here.
3. **Google Cloud Tech — six ways to test AI agents with eval engineering.** Argues deterministic unit tests fail once an agent acts autonomously, because in multi-turn workflows "every single error compounds exponentially across turns" and an agent can report success while having taken an unauthorized action. Proposes moving from "vibe checks" to "Eval engineering… treating evaluations as automated, measurable CI/CD test suites." The six: deterministic schema checks (`tool_call_valid`, `tool_parameter_kv_match`); mathematical groundedness scoring (attribution 0–1, reject under a strict threshold); execution-trajectory inspection (`trajectory_in_order_match` — did it check permissions before changing state); AutoRaters on 1–5 rubrics that must emit step-by-step reasoning *before* the score; pairwise AutoSxS tournaments with **answer order swapped between runs to neutralize position bias**; and golden regression fixtures in CI, where a prompt edit that drops the win rate turns "the PR check red." Cites the Firebase team going from "31.7% to a 78.0%" pass rate through evaluation and scaffolding.

**What we added** — all three from source 3; sources 1 and 2 contributed nothing:
- Augmentation: `skills/better-call/SKILL.md` **Step 2b** — score the incumbent/challenger pair a second time with the incumbent presented first, and return the new `INCONCLUSIVE` verdict when the two passes disagree. Directly from AutoSxS's order-swapping. The skill already guarded *defender* bias and said nothing about *position* bias; the two are independent, and `better-call` is the gate every extraction routes through, so its bias decides what enters the harness. Same principle as the median-of-3 judge decision of 2026-09-03: one presentation order is one sample. `skills/skill-extraction/SKILL.md` Phase 3f gained the matching row.
- Augmentation: `skills/skill-eval-gate/SKILL.md` **Phase 6** + `hooks/claude/skill-validate-hook.sh` — from the golden-regression-fixtures method. On PASS the gate writes `eval-verdict.json` beside the `SKILL.md` carrying the **sha256 of the instructions it measured**; the hook hashes every incoming `SKILL.md` write and warns on mismatch, on a missing verdict for an already-existing skill, and on a verdict file that is unreadable or hashless. Hash and not date: a skill edited an hour after its eval has a same-day verdict that means nothing. Silent for a `SKILL.md` that does not yet exist, so it does not fire on every new skill and train itself out. Tests grew 8 → 14.

**Rejected:**
- **Source 1, the `show-me` skill.** Overlaps `diff-teach`, `mermaid-expert`, `artifact-diagramming` and `gitnexus-exploring` four ways, in the saturated software-engineering domain (SkillsBench: 4.5pt lift there vs 51.9pt in model-weak domains). If wanted later it should be installed as the upstream `humanlayer/skills` plugin, which costs this repo no maintenance. Provenance note: its own text was never retrieved — WebFetch declined to reproduce it and returned a structural description, so this rejection rests on a summary, not the file.
- **Source 2, in full.** Retrieval was low-fidelity and the items map to commands the built-in `claude-api` skill already owns. Its one usable datum — switching effort levels mid-conversation loses the cache — duplicates a point in the same batch's video source, and is knowledge rather than behavior.
- **Four of source 3's six methods.** Deterministic schema checks, groundedness scoring, and AutoRater rubrics are already covered by `skill-eval-gate` (`pass^3` against a no-skill baseline) and `session-judge` (median of 3); groundedness has no surface in a harness that retrieves nothing. Trajectory-order assertions are a genuine gap but low-yield here — there are no tool-call fixtures to assert against, and building them would be the larger half of the work. Only the two methods naming a mechanism this harness lacked were taken.

See also: [articles/README.md](../articles/README.md) — the full six-source batch these three arrived in, including the funes and DX Core 4 sources and the `cleanupPeriodDays` and `claudemd-edit-notice.sh` items. [git/README.md](../git/README.md) — the two repositories these sources pointed at.

---

## Three codenewsletter-archived X posts (2026-09-09 eleven-source batch)
**URLs:**
1. https://archive.codenewsletter.ai/2095890279865721217 — Andrew Ng (@AndrewYNg), "AI Engineering Skills Map: Using coding agents", 2026-09-04
2. https://archive.codenewsletter.ai/2094871453879402747 — Ramp Labs (@RampLabs), "Building the missing layer between AI spend and ROI", by René Sultan
3. https://archive.codenewsletter.ai/2096439998539321653 — Prasenjit Sarkar (@stretchcloud) on Spotify's "Portal", 2026-09-06

**Added:** 2026-09-09

**Retrieval:** All three via `ctx_url_read` rung 1. Sources 1 and 2 returned the full article text; source 2's tail was truncated after the substance. **Source 3 is secondhand** and is treated that way throughout: it is an X post *about* a Spotify engineering article that was never retrieved, so its headline figure is a claim in a summary rather than something this repo read.

**What they're about:**
1. **Ng — AI Engineering Skills Map.** A workflow (plan → execute → deploy and monitor) and five skill areas: directing the workflow, enabling agent autonomy, reviewing work, customizing the agent and its environment, and coding-agent foundations. Closes on a corrective: social media oversimplifies long-horizon autonomous runs, and "most effective coding agent use is a complex, highly iterative process" where the ability to intervene with judgement beats letting an agent burn tokens for hours.
2. **Ramp — the semantic layer between AI spend and ROI.** Telemetry says which models ran and what they cost, not what the agents were asked to do. Ramp reconstructs a "complete run" by following a root session through its child sessions, then extracts **work items** — one objective, one outcome — labels each in free-form language deliberately without a preset taxonomy, and only afterwards groups those 1.4M labels into 30 shared categories. Reported scale: ~200,000 runs, ~250,000 work items over three weeks; 75% of merged PRs at Ramp came from the agent; 17% of runs held more than one work item and those accounted for 58% of model spend. It then cut extraction cost by compressing traces 74,000 → 19,000 tokens, and **validated the compression by first measuring uncompressed run-to-run variance** and requiring the compressed-vs-uncompressed difference to stay inside it.
3. **Sarkar on Spotify "Portal".** Two cheaper assistant models handle file opening and boilerplate; the expensive model only touches novel reasoning. Files over 350 lines are *blocked* from the expensive model, not steered away from it. The stated lesson is that rules-as-instructions did not hold — "engineers (and the model) found ways around soft rules" — and hard blocks at the routing layer did, with a claimed 90% token reduction.

**What we added:** nothing from any of the three.

**Rejected — source 1, in full.** Every one of the five areas maps onto something the harness already runs. Good framing, no new behavior — a hollow addition by the critic gate's own definition.

**Rejected — source 2, both halves.** Work-item extraction with cost attribution is enterprise ROI reporting across 200-plus internal agents and thousands of engineers; this harness is single-operator, and `auditing-token-spend` plus `utils/token-forensics.py` already measure spend against sessions. The genuinely strong idea — validating a compression change against the baseline noise floor rather than against intuition — is the same principle as the median-of-3 trust-score decision, and **already landed** in commit 618e34f as `skill-eval-gate`'s compression-regression scenario. Independent arrival at the same method is worth recording; it earns no second implementation.

**Rejected — source 3.** Its philosophy is already this harness's philosophy: enforcement lives in hooks, not in prompts, which is why `hooks/claude/` has 50-plus files. The mechanism needs a per-tool-call model-routing proxy that Claude Code does not expose, so it cannot be built here. The nearest implementable shape, a read-size gate pushing large reads to `ctx_read` map or signatures mode, is already occupied by `lean-ctx-nudge-hook.sh`. Secondhand as well, so the 90% figure was never treated as measured.

See also: [articles/README.md](../articles/README.md) — the full eleven-source batch and the two integrations it produced. [git/README.md](../git/README.md) — the three repositories from the same batch.

---

## @ericzakariasson — "Improve this agent harness's token efficiency" audit prompt
**URL:** https://archive.codenewsletter.ai/2102853511637774551 (mirror of x.com/ericzakariasson/status/2102853511637774551) | **Added:** 2026-09-30 | **Source:** eric zakariasson (Cursor)

**What it's about:** An 8-step audit prompt aimed at price-weighted token cost per completed task: baseline by source × billing type, prune the system prompt, offload tools, lay out cache breakpoints, file-back large tool outputs, keep compaction short, fit instructions to the model, A/B test. One named trap: injected extras such as lint output after every edit.

**What we added:**
- Augmentation: `scripts/utils/token-forensics.py` — per-hook injection tables read from transcript `attachment` lines, ranked by amplified cost (chars × requests that followed). Two classes, never summed: `hook_additional_context` (confirmed to reach the model) and `hook_success` stdout (reaches it only for some events, so an upper bound). Before this the script had no hook attribution at all. First real run (7 days, this repo): `UserPromptSubmit prompt-hook.sh` injected 424k chars over 50 prompts, far above any other hook. `token-forensics.test.sh` 14 → 19 cases; `skills/auditing-token-spend/SKILL.md` gained the matching signal and a "one hook dominates" diagnosis row.

**Rejected:** cache-breakpoint layout (Claude Code owns prompt assembly), tool offloading (already deferred), online A/B testing (nothing here to A/B), porting the prompt as a command (repeats `auditing-token-spend` and `/context-budget`).

See also: [articles/README.md](../articles/README.md) — same 18-source batch, 2026-09-30. Two other X posts in the batch (Cua jev-use, OpenRouter's Jev Router) yielded nothing and are recorded in `docs/skills/skill-extraction/extraction-history.md`.

---

## @tvykruta — 10 design principles as hard rules for Claude Code
**URL:** https://archive.codenewsletter.ai/2105302045573959697 (archived copy of x.com/tvykruta/status/2105302045573959697)
**Added:** 2026-10-01
**Source / Author:** Tomas Vykruta (@tvykruta), X thread, 2026-09-30. Fetch partial: posts 8–10 absent from the archive page.

**What it's about:** Old C/C++ design principles from Microsoft and Google turned into AGENTS.md rules for an Opus-written app. An audit found pricing logic spread across 32 files, with copies that disagreed; one pricing bug ate ~30% of PRs, including a discarded 89-file rewrite. The central rule is "two steps, never one": refactor with behavior unchanged and tests green, then build the feature. The other rule is "gates, not promises" (enforce rules in CI or hooks). The author reports PRs 7× cheaper, 5× faster and 9× fewer tokens. These are the author's own numbers and haven't been verified elsewhere.

**What we added:**
- Augmentation: `kiro/settings/rules/tasks-generation.md` — "Refactor first, then build": when design.md chose *refactor* or *hybrid* for an area, the feature task there is preceded by its own behavior-preserving refactor task (tests green, own commit, `_Requirements:_` = the IDs of the feature it enables). Design-discovery already made the refactor decision; nothing turned it into task order, and `/kiro:simplify` only runs after the fact, on request. `better-call`: MERGE (25 vs 21, reversed pass 24 vs 21), applied by hand because `better-call` is not installed in `~/.claude/skills`.

**Rejected:** one-owning-module / no-duplication / outside-module counters (guardrails-agent's import-linter, eslint-plugin-boundaries, jscpd already cover them), "gates, not promises" (already how the harness works).

See also: [git/README.md](../git/README.md) — same 4-source batch, 2026-10-01 (pi, open-dot).
