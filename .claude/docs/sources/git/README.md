# Git Repositories

GitHub repositories that were passed to `/skill-extraction` and turned into harness skills, scripts, or tooling. Ordered by date added.

---

## github.com/karpathy/autoresearch
**URL:** https://github.com/karpathy/autoresearch | **Added:** 2026-03-30

**What it is:** Andrej Karpathy's autonomous ML research agent that iterates on a training script overnight — reads a research brief (`program.md`), proposes one hypothesis per loop, edits `train.py`, runs a 5-minute wall-clock-bounded experiment, keeps improvements via git commit, reverts failures via `git checkout`, and loops.

**What we added:**
- Command: `/kiro:autoresearch-init` — interactive 8-question interview that generates all 3 required files (`program.md`, `train.py`, `prepare.py`) from a user description
- Command: `/kiro:autoresearch [N]` — runs the autonomous experiment loop (N iterations or continuous)
- Agents: `autoresearch-init-agent` (interview → file generation), `autoresearch-agent` (experiment loop executor)
- Docs: `docs/research/autoresearch/README.md` — loop diagram, file roles, use cases, example `program.md`

---

## github.com/abhigyanpatwari/GitNexus
**URL:** https://github.com/abhigyanpatwari/GitNexus | **Added:** 2026-04-16

**What it is:** Zero-server code intelligence engine that builds a knowledge graph (symbols, dependencies, call chains, execution flows) from a codebase and exposes it via MCP tools.

**What we added:**
- Integration: PreToolUse hook (`hooks/claude/pre-tool-use-gitnexus.sh`) — automatically enriches every file read/edit with 360-degree symbol context (callers, deps, blast radius). Zero agent configuration needed.
- Integration: Post-commit hook auto-reindex — `hooks/git/post-commit` re-indexes the repo after every commit so the knowledge graph stays fresh. Its own `docs: auto-sync` commits are exempt: a self-commit guard at the top of the hook exits before the reindex stage.
- Integration wired into: `verify-agent` (Stage 0 risk detection), `spec-impl` (blast radius scan before TDD), `debug-agent` (Step 2 call chain tracing), `skill-extract-agent` (Leiden community cluster seeding)
- Command: `/kiro:gitnexus-explore` — launches GitNexus Web UI (localhost:4747) for visual repo exploration
- Docs: `docs/integrations/gitnexus/README.md`

---

## github.com/nidhinjs/prompt-master
**URL:** https://github.com/nidhinjs/prompt-master | **Added:** 2026-05-06

**What it is:** Active prompt factory with 30+ tool profiles, 14 templates, 38 anti-patterns, and native JSON-structured input support. Core insight: models guess when dimensions (tone, format, audience, length) are unspecified — JSON eliminates the guessing surface.

**What we added:**
- Skill: `prompt-master` v1.7.0 — JSON input detection, 9-dimension extraction framework, 30+ tool profiles across 11 categories, 14 templates, 38 anti-patterns (including 7 agentic credit-killers). Activates when ≥3 dimensions unspecified in a prose prompt.
- Lazy-load references: `references/templates.md` + `references/patterns.md` (loaded on demand to avoid context bloat)
- Docs: `docs/prompts/prompt-master/README.md` — JSON vs prose decision matrix, before/after gallery, key behaviors, tool profiles table

---

## github.com/openai/privacy-filter
**URL:** https://github.com/openai/privacy-filter | **Added:** 2026-05-06

**What it is:** OpenAI's Privacy Filter (OPF) — an 8-layer bidirectional transformer with sparse MoE FFN (128 experts, top-4) and Viterbi CRF decoder for character-level PII span extraction. ~2.8GB model weights auto-download on first use.

**What we added:**
- Hook: `hooks/claude/scan-pii.sh` — pre-commit git hook that runs OPF on staged files; exits 2 (warn, don't block) when OPF is missing; exits 1 on high-severity PII detection
- Docs: `docs/security/privacy-filter/README.md` — architecture diagram, graceful degradation pattern, output modes, Python API, performance tuning (`OPF_TORCH_COMPILE=1` for ~20% speedup), troubleshooting table. Links to: `secrets-management`, `gdpr-data-handling`, `security-sast`

---

## github.com/EveryInc/proof-sdk
**URL:** https://github.com/EveryInc/proof-sdk | **Added:** 2026-05-06

**What it is:** SDK for Proof — a collaborative markdown review tool that starts a local server, publishes a document to a live shareable URL, lets stakeholders comment and edit inline, and returns the finalized version. Designed for async multi-stakeholder review workflows.

**What we added:**
- Skill: `proof-collaborative-review` — SDD phase-gate integration: publishes spec/design/task-list artifacts to a live Proof document, waits for human review/adjustment, retrieves the final version, tears down the server. Used at spec phase transitions requiring stakeholder sign-off where "approve/reject" is insufficient.

---

## github.com/pbakaus/impeccable
**URL:** https://github.com/pbakaus/impeccable | **Added:** 2026-05-07

**What it is:** Design quality system for detecting AI-generated UI fingerprints and enforcing professional design standards via 27 deterministic rules across 7 domains.

**What we added:**
- Skill: `impeccable-audit` — 27 deterministic anti-pattern rules across typography, color/contrast, spatial design, motion, interaction, responsive, and UX writing. AI fingerprint detection flags 7 patterns that reliably mark AI-generated UIs: gradient text, glassmorphism, colored left borders, gradient backgrounds, nested cards, identical card grids, pure white backgrounds. Output: structured audit with PASS / NEEDS WORK / BLOCK verdict + file:line references.
- Hook: `hooks/claude/impeccable-detect-hook.sh` — detects when a prompt targets frontend/UI work and injects a reminder to run `impeccable-audit` before shipping.

---

## github.com/codejunkie99/ztk
**URL:** https://github.com/codejunkie99/ztk | **Added:** 2026-05-07

**What it is:** Quality metrics toolkit for measuring code authorship and survival patterns in git repositories. The key insight extracted: measuring the survival rate of generated code (% of lines still in HEAD after N days) provides a lagging quality signal independent of test passage.

**What we added:**
- Skill: `keep-rate` — calculates % of Claude-authored lines (grep: `Co-Authored-By: Claude` or `noreply@anthropic`) still present in HEAD after 7+ days. Thresholds: >80% strong, 60–80% normal, 40–60% warning, <40% alert. Auto-appends kaizen observations below 50%. Recommended cadence: Monday/Thursday evenings via CCR routine.

---

## github.com/garrytan/gbrain
**URL:** https://github.com/garrytan/gbrain | **Added:** 2026-05-12

**What it is:** GBrain — a pattern library for agent harnesses covering model tier selection (haiku/sonnet/opus routing), compiled-truth memory structure (State zone rewrite-in-place, Evidence zone append-only), memory-first lookup chains, and background task routing. Key principle: protocols should fire at the moment they're needed, not be manually invoked.

**What we added:**
- Hook: `hooks/claude/gbrain-agent-spawn.sh` (PreToolUse `Agent`) — injects model-tier guidance (haiku=classification, sonnet=generation/subagents, opus=deep-reasoning-only) + background-routing pain signals before every Agent spawn
- Hook: `hooks/claude/gbrain-memory-write.sh` (PreToolUse `save_observation`) — enforces compiled-truth two-zone structure (State at top rewrite-in-place, Evidence append-only at bottom) before every memory write
- Hook: `hooks/claude/gbrain-external-search.sh` (PreToolUse `WebFetch|WebSearch`) — injects memory-first lookup reminder (search → get_observations → timeline) before reaching external APIs
- Skill: `agent-memory-consolidation` — episodic-first architecture, 3 consolidation failure modes (misgrouping, interference, overfitting), audit checklist
- Docs: `docs/gbrain-patterns/gbrain-patterns.md`

---

## github.com/melagiri/code-insights
**URL:** https://github.com/melagiri/code-insights | **Added:** 2026-06-11

**What it is:** Local session analytics tool that parses AI coding sessions (Claude Code, Cursor, Copilot), extracts structured knowledge (decisions, learnings, cross-session patterns), scores prompt quality across 6 LLM-derived dimensions, and serves a React dashboard at localhost:7890 backed by a local SQLite DB. Privacy-first: no cloud sync, uses your own API key.

**What we added:**
- Hook: `hooks/claude/prompt-quality-check.sh` (PreToolUse `Agent`) — scores every agent spawn against 6 PQ dimensions (context_provision, request_specificity, scope_management, information_timing, correction_quality, overall) using fast Python heuristics; emits scored feedback to Claude's context; appends to `~/.code-insights/pq-log.jsonl`
- Hook extension: `hooks/claude/session-start-hook.sh` — added PQ baseline block that reads last 14 log entries and emits quality average + weakest dimensions at every session start
- Dashboard: `render_prompt_quality()` in `scripts/utils/dashboard.py` — new ✨ Prompt Quality sub-tab under Session Health; shows dimension bars, rolling trend, summary strip; reads `~/.code-insights/pq-log.jsonl`
- Skill: `prompt-quality-assess` — 6-dimension cognitive rubric with per-dimension rewrite patterns; pre-flight checklist to apply before writing agent prompts
- Docs: `docs/prompt-quality/README.md` — full system reference: dimensions, log format, files, hook output examples

---

## github.com/yvgude/lean-ctx
**URL:** https://github.com/yvgude/lean-ctx | **Added:** 2026-05-18

**What it is:** Single Rust binary MCP server exposing 51 `ctx_*` tools for token-efficient file reads and code analysis. `ctx_read(path, "signatures")` returns only exported symbols instead of full file (~400 tokens vs 12,000 for a typical file). Re-reads of cached files cost ~13 tokens regardless of size.

**What we added:**
- Integration: lean-ctx MCP server registered in harness `settings.json` template; complements RTK (RTK handles Bash output, lean-ctx handles file reads and AST analysis)
- Hook: `hooks/claude/lean-ctx-nudge-hook.sh` (UserPromptSubmit) — detects when a prompt involves file reading/analysis and suggests `ctx_read` modes over raw Read calls
- Docs: `docs/context-management/lean-ctx/README.md` — 10 read modes, ctx_graph vs gitnexus decision table, ctx_shell warning (don't use — RTK handles it), installation guide

---

## github.com/raindrop-ai/workshop
**URL:** https://github.com/raindrop-ai/workshop | **Added:** 2026-05-18

**What it is:** Raindrop Workshop — agent trace capture, replay, and evaluation platform. Records full execution traces from Claude agents, enables replaying specific runs, and provides an MCP server for querying spans and payloads programmatically.

**What we added:**
- Skills: `raindrop-instrument-agent`, `raindrop-eval-loop`, `raindrop-agent-replay` — instrument agent, run eval loops, and replay captured runs
- Dashboard: Raindrop Workshop tab added to `scripts/dashboard.py` (accessible at localhost port)
- Per-repo tracing configured across all 3 registered projects
- Docs: `docs/raindrop/README.md`

---

## github.com/bendc/frontend-guidelines
**URL:** https://github.com/bendc/frontend-guidelines | **Added:** 2026-05-27

**What it is:** Benjamin De Cock's opinionated HTML/CSS/JS style guidelines. Concise rules focused on correctness, brevity, and maintainability — semantic HTML, CSS structural patterns (selectors, specificity, units, composited-only animations), and JS idioms (pure functions, array methods over loops, composition, const-first).

**What we added:**
- Skill: `frontend-code-quality` — three-section code quality checklist (HTML, CSS, JS). Invoked during frontend code review. Pairs with `frontend-performance` for full coverage: `frontend-performance` handles architecture decisions, `frontend-code-quality` handles per-file patterns.

---

## github.com/hhhuang/CAG
**URL:** https://github.com/hhhuang/CAG | **Added:** 2026-05-27

**What it is:** Reference implementation of Cache-Augmented Generation — preloads knowledge documents into a HuggingFace model's KV cache once, persists the cache state to disk, and reuses it across queries. Eliminates real-time retrieval for bounded, stable knowledge bases. Accompanies arXiv:2412.15605.

**What we added:**
- Skill: `cag-implementation` — decision matrix (CAG vs RAG), three-phase implementation pattern (preload → persist → query loop), HuggingFace `past_key_values` implementation, and integration with the existing `rag-architect` skill routing decision. See also: [papers/README.md](../papers/README.md) for the accompanying paper.

---

## github.com/steipete/agent-scripts (skills/skill-cleaner)
**URL:** https://github.com/steipete/agent-scripts/blob/main/skills/skill-cleaner/SKILL.md | **Added:** 2026-05-31

**What it is:** Peter Steinberger's Codex/OpenClaw skill-cleaner — audits skill libraries for prompt-budget cost (descriptions as % of context window), duplicate skills, and unused skills. Includes a TypeScript analyzer that reads Codex session logs and models_cache.json.

**What we added:**
- Skill: `skill-curator` — local post-weekly-sweep curation step. The TypeScript script and Codex-specific paths (models_cache.json, history.jsonl) were not portable, but the core concepts were: description-budget analysis (chars per description, 150/200-char thresholds, grammar compression heuristics) were absorbed into Phase 2 of the skill. Phase 1 reads `docs/skill-curation-report.md` from the weekly skill-curator scheduled-tasks sweep; Phases 3–5 handle consolidated proposal + human-approved execution (merge/compress/delete). Also fixed a gap: the weekly sweep referenced `skill-curator` in its "How to use" but the skill didn't exist.
- Updated: `docs/scheduled-tasks/README.md` — corrected weekly routine's "How to use" to reference `/skill-curator`, added Known Limitation note about the sweep not accessing `~/.claude/skills/` directly.

---

## github.com/rtk-ai/rtk
**URL:** https://github.com/rtk-ai/rtk | **Added:** 2026-05-27

**What it is:** RTK (Rust Token Killer) — a Claude Code PreToolUse hook that intercepts Bash commands and rewrites them to compressed equivalents, achieving 60–90% token reduction on common dev operations (git, pytest, jest, tsc, eslint, Docker, kubectl, AWS CLI). Output semantics are preserved; only verbosity is removed.

**What we added:**
- Skill: `rtk-token-reduction` — guidance on when to use `rtk proxy` (exact raw output for piping/debugging), `rtk gain` (check cumulative savings), and how to configure exclusions for commands where compression would break downstream parsing.

---

## github.com/anomalyco/models.dev
**URL:** https://github.com/anomalyco/models.dev | **Added:** 2026-05-31 | **Live API:** https://models.dev/api.json

**What it is:** Open-source database of AI model specifications, pricing, and capabilities maintained by Anomaly. Covers 4,700+ models across 120+ providers (Anthropic, OpenAI, Google, Mistral, DeepSeek, xAI, Cohere, Amazon Bedrock, Azure, Groq, Perplexity, and many more). Per-model data includes context window, per-million-token pricing (input, output, cache read/write), input modalities, capability flags (tool use, vision, reasoning, structured output), and supported parameters. Published as a flat JSON blob at `https://models.dev/api.json` with per-provider nesting.

**What we added:**
- Dashboard section: **💰 Model Cost** (`scripts/dashboard.py`) — tracks Claude Code session token usage from `~/.claude/projects/*/` JSONL files, computes USD costs using historically-accurate pricing (snapshot closest to the session date), and renders: all-time + 30-day cost stats, 90-day daily cost chart, scrollable sessions table with per-project filter, and a cross-provider "What if?" switcher (cascading provider → model dropdowns covering all 12 featured providers, 394 priced models). A ⚠ icon flags sessions where pricing has changed since they ran.
- Artifact: `.dashboard/models-pricing-history.json` — local pricing snapshot store. Fetches `models.dev/api.json` on first run and bi-weekly thereafter; appends a new snapshot only when prices change (de-duplication by value comparison), so historical cost calculations remain accurate as the market moves.
- Functions added to `scripts/dashboard.py`: `load_or_refresh_pricing_history`, `get_pricing_at`, `gather_usage_data`, `_parse_session_file`, `compute_session_cost`, `render_model_cost`.
- Constants: `FEATURED_PROVIDERS` (12-provider curated list), `PROVIDER_DISPLAY` (human-readable names), `PRICING_HISTORY`, `PRICING_MAX_AGE`, `CLAUDE_PROJECTS`.

---

## github.com/multica-ai/andrej-karpathy-skills
**URL:** https://github.com/multica-ai/andrej-karpathy-skills | **Added:** 2026-05-31

**What it is:** A single-skill harness plugin packaging Andrej Karpathy's observations on LLM coding pitfalls into four behavioral principles: Think Before Coding (surface assumptions, present alternatives, ask when confused), Simplicity First (minimum code to solve the problem, nothing speculative), Surgical Changes (only touch lines traceable to the user's request, clean up only your own orphans), and Goal-Driven Execution (transform imperative tasks into verifiable test-first goals). Originally a `CLAUDE.md` snippet; the multica-ai fork ships it as a proper `SKILL.md` plugin.

**What we added:**
- Skill: `karpathy-guidelines` — 4-principle behavioral checklist that fires before every coding task (writing, editing, reviewing, refactoring). Description deliberately broad so the `using-superpowers` skill auto-invokes it on any coding request. Cross-references `questions`, `refactoring-safely`, and `test-driven-development` for deeper process support.

---

## github.com/affaan-m/ECC
**URL:** https://github.com/affaan-m/ECC | **Added:** 2026-05-31 | **Updated:** 2026-06-02

**What it is:** ECC ("harness-native operator system for agentic work") — a cross-harness AI agent operating system with 63 agents, 249 skills, and 15+ hook events, built over 10+ months of daily production use. Supports Claude Code, Cursor, OpenCode, Codex, Copilot, and Zed. Notable patterns: observer loop prevention (5-layer re-entrancy guard), hook profile switching via env vars, and instinct-based pattern extraction with confidence scoring.

**What we added:**
- Skill (augmentation): `hook-design` — added "Observer Loop Prevention" section (env-var sentinel, matcher specificity, state-file lock patterns) and "Session Profile Switching" section (`SDD_HOOK_PROFILE=off|minimal|standard` preamble pattern). Fills a gap: none of the existing harness hooks had re-entrancy guards despite firing on Write/Edit events that can themselves produce writes.
- Daily maintenance Step D (pattern scoring) — ECC's instinct-based pattern extraction with confidence scoring adapted as a new phase in `.claude/scripts/daily-maintenance-prompt.md`. Each morning the runner scans observations added since the last `[judge]:` line, scores each 1–5 for reusability, and appends PROMOTE-scored entries (4–5) to `.claude/memory/forward-patterns.md` as one-line reusable heuristics. The weekly skill-curator then picks up `forward-patterns.md` and decides what to graduate to full skills. Pattern score summary written as `[pattern-score]` observation. Docs updated: `docs/memory/README.md` (new file in tree + data flow).

---

## github.com/MemoriLabs/Memori
**URL:** https://github.com/MemoriLabs/Memori | **Added:** 2026-06-02

**What it's about:** Agent-native memory infrastructure built around the principle "memory from what agents DO, not just what they say." Provides a cloud MCP server + Python/TS SDK, but the valuable extract is its design patterns: entity/process/session attribution hierarchy, 8 augmentation layers (attributes, events, facts, preferences, relationships, rules, skills, people), and automatic memory capture from agent actions rather than conversational summaries. Achieved 81.95% on the LoCoMo benchmark at only 4.97% of full-context token footprint (~67% leaner than competing systems).

**What we added** (local patterns only — no cloud service, no API key required):
- Hook: `action-capture.sh` (PostToolUse, Bash, soft gate) — fires after `git commit`, failing `pytest` runs, and deploy commands; prompts Claude to consider saving a memory observation from the outcome. Implements the "memory from what agents do" principle locally without any external dependency.
- Skill enhancement: `agent-memory-discipline` — added "Memory Body Sub-Types" section (Memori's 8 augmentation layers mapped onto the 4 existing memory types for more precise body structure) and "Session Attribution" section (optional `session:` frontmatter field keyed by git branch or task descriptor for targeted retrieval).
- Docs updated: `docs/hooks/README.md` (new hook entry + wiring table), `docs/memory/README.md` (sub-types table + session attribution convention).

---

## github.com/agentscope-ai/ReMe
**URL:** https://github.com/agentscope-ai/ReMe | **Added:** 2026-06-01
**Source / Author:** AgentScope (Alibaba)

**What it's about:** ReMe is an AI-agent memory framework addressing limited context windows and stateless sessions. Beyond conversation memory it maintains **procedural memory** (task success/failure experiences) and **tool memory** (tool-usage experience + parameter optimization), plus file-based ("memory as files") and vector-based stores with hybrid retrieval. The procedural/tool-memory concept — an agent remembering which tool calls fail and avoiding the repeat — is the subset extracted here.

**What we added (a closed capture→recall→review loop for failing tool calls):**
- Hook: `tool-failure-capture.sh` (PostToolUseFailure, matcher `Bash|mcp__.*`) — records every failing Bash/MCP call into a per-repo ledger `.claude/memory/tool-failures.jsonl`, keyed by a normalized command *signature* (paths/numbers/hashes/strings collapsed) so the *same kind* of failure clusters and its `count` climbs. Re-opens a resolved entry if the failure recurs.
- Hook: `tool-failure-recall.sh` (PreToolUse, matcher `Bash|mcp__.*`, soft/advisory) — before a call runs, warns if that signature has failed ≥2× and is still open, showing the last error and any recorded remedy. Once-per-session-per-signature dedupe + 45-day recency gate keep it quiet; never blocks.
- Routine: `tool-failure-review` — `commands/kiro/tool-failure-review.md` + `scripts/tool-failure-review-runner.sh` (MIN_GAP_DAYS=3 self-pacing, no-ops unless the ledger has a promotable entry) wired into `daily-orchestrator.sh` `run_one()`. Diagnoses *why* recurring failures happen and promotes the durable lessons into memory (+ `ERRORS.md`), marking entries resolved/promoted. This is where the system learns from its mistakes.
- Skill: `tool-failure-memory` — the judgment layer: signature model, how to respond to a recall warning (don't reflexively re-run), and how to record a remedy / promote to memory.
- Config: registered the two hooks in `templates/settings.json.template` (PostToolUseFailure + PreToolUse) so the loop ships to every repo; runner added to install/update chmod lists.

---

## github.com/ulyssestenn/omt
**URL:** https://github.com/ulyssestenn/omt | **Added:** 2026-06-07
**Source / Author:** Bethany Hunt (ulyssestenn)

**What it's about:** Zero-dependency Python CLI (stdlib only) for running a prompt N times against any local Ollama model and saving every response to disk. Outputs are keyed on a SHA256 prompt hash so multi-model comparison runs land in the same folder automatically. Captures token counts, timing, and temperature per run in JSON metadata alongside Markdown response files.

**What we added:**
- Script: `ollama_model_test.py` — full OMT script added to `scripts/`; propagates to every project's `.claude/scripts/` via update.sh. chmod +x wired into install.sh and update.sh.
- Skill: `local-llm-eval` — guided workflow for offline prompt evaluation: pre-flight check, parameter selection, single/multi-model run patterns (shell loop), result interpretation, and prompt iteration loop. Fills gap between abstract `llm-evaluation` skill and actual local execution.
- Docs: `docs/evaluation/local-llm-eval/README.md` — quick start, flag reference, output schema, common patterns (model comparison, variance audit, temperature sweep), related skills.

---

## github.com/anthropics/defending-code-reference-harness
**URL:** https://github.com/anthropics/defending-code-reference-harness | **Added:** 2026-06-07
**Source / Author:** Anthropic

**What it's about:** Anthropic's reference implementation for autonomous vulnerability discovery and remediation using Claude. Provides a 7-stage pipeline (recon → find → grade → judge → report → patch) with gVisor sandboxing and an egress-allowlist proxy, plus a set of interactive Claude Code skills (threat-model, vuln-scan, triage, patch) for the human-in-the-loop path. Key design patterns: `<untrusted_data>` wrapping to mitigate prompt injection, separate find/verify agent isolation (only the PoC crosses), serial judge for dedup race prevention, and egress-only network policy.

**What we added:**
- Skill: `ai-security-workflow` — 5-phase interactive security pipeline (threat-model → vuln-scan → triage → patch → close) with standardized artifact handoffs (THREAT_MODEL.md → VULN-FINDINGS.json → TRIAGE.json → PATCHES/ → SECURITY-REPORT.md). Distinct from existing scattered SAST skills: this is a choreographed, phase-gated process that gets from "do a security pass" to "here are my patches" with human review gates between each phase.
- Skill: `secure-agent-design` — 5-pattern checklist for building security-hardened Claude agents: `<untrusted_data>` prompt injection mitigation, find/verify isolation architecture, serial dedup judge, egress control, and credential handling. Fires when building any agent that processes untrusted input or runs in parallel.
- Runner: `security-report-runner.sh` + `security-report-prompt.md` — daily headless safety scan (MIN_GAP_DAYS=1) wired into `daily-orchestrator.sh`. Scans git changes from the last 24h for OWASP patterns, hardcoded secrets, injection sinks, and missing auth checks; writes `.claude/reports/security/<date>-security-report.md`.
- Dashboard: added `security-report` entry to `_scheduled_task_registry()` in `dashboard.py` so the Scheduled Tasks tab shows daily scan status and last report artifact.
- Hook: `frontend-security-nudge.sh` (UserPromptSubmit, global) — detects when a prompt combines "build/create/add" + frontend/design keywords and injects a reminder to invoke `secure-agent-design` skill before building. Registered in `~/.claude/settings.json` and `templates/settings.json.template`.

---

## github.com/github/spec-kit
**URL:** https://github.com/github/spec-kit | **Added:** 2026-06-07
**Source / Author:** GitHub

**What it's about:** Open-source Spec-Driven Development toolkit with a 7-phase workflow (Constitution → Specify → Clarify → Plan → Tasks → Validate → Implement) and slash commands for 30+ AI agents. Key patterns: `[NEEDS CLARIFICATION]` markers to force explicit ambiguity surfacing, constitutional gates (Simplicity/Anti-Abstraction/Integration-First) as Phase -1 pre-implementation checklists, and parallel task group identification. The harness already has a full SDD pipeline (Kiro); these three patterns filled concrete gaps.

**What we added:**
- Augment: `agents/kiro/spec-requirements.md` — `[NEEDS CLARIFICATION]` discipline: step 3 now marks every assumed value, unstated intent, or unclear constraint with an explicit inline marker + a step 4 that surfaces all markers to the user for resolution before requirements are approved.
- Augment: `agents/kiro/spec-tasks.md` — Output Description now includes "Parallel Execution Groups" section listing safe concurrent invocation groups, first sequential gate after each group, enabling users to dispatch parallel `/kiro:spec-impl` calls without reading the dependency chain.
- Augment: `commands/kiro/spec-impl.md` — Phase -1 Pre-Implementation Gates added before TDD subagent invocation: Simplicity Gate (≤3 components, no speculative tasks), Anti-Abstraction Gate (direct framework use, single model), Integration-First Gate (contracts in design.md, integration test task before unit-only tasks). Soft gate — user can override.
- Docs: `docs/workflow/kiro/README.md` — updated command table entries for `spec-requirements`, `spec-tasks`, and `spec-impl` to reflect new behaviors.

---

## github.com/tinyfish-io/bigset
**URL:** https://github.com/tinyfish-io/bigset | **Added:** 2026-06-07
**Source / Author:** TinyFish

**What it's about:** Self-hosted AI platform that takes a natural-language dataset description, infers a schema via Claude Sonnet, fans out parallel agents to research entities from live public web sources, deduplicates results, and exports a structured CSV/XLSX. Uses Mastra for workflow orchestration, TinyFish APIs for web access, Convex as the database, and Clerk for auth. Requires Docker + 3 free API keys.

**What we added:**
- Skill: `structured-web-dataset` — implements BigSet's pipeline natively in Claude Code (no Docker, no external APIs). Covers both Web mode (parallel Agent() fan-out per entity → verified rows from live sources) and Synthetic mode (schema + distribution rules → generated rows with controlled edge cases). Fills the gap between `deep-research` (prose reports) and raw data needs — output is always a typed, deduplicated table/CSV.
- Docs: `docs/research/structured-web-dataset/README.md` — workflow diagram, design decisions, related skills.

---

## github.com/chopratejas/headroom
**URL:** https://github.com/chopratejas/headroom | **Added:** 2026-06-08
**Source / Author:** Tejas Chopra

**What it's about:** Content-aware prompt compression layer for LLM agents and Claude Code. Compresses tool outputs, RAG chunks, file contents, and conversation history at the messages/prompt layer (60-95% token savings) using 6 algorithms: SmartCrusher (JSON), CodeCompressor (AST), Kompress-base (ML), log, search, and HTML extractors. Reversible Compression (CCR) stores originals locally; `headroom wrap claude` intercepts all context before it reaches the model with zero code changes. Distinct from RTK (shell output compression): headroom operates on the full prompt layer, not just Bash stdout.

**What we added:**
- Script: `headroom-setup.sh` — idempotent install script: installs `headroom-ai` globally (uv tool → pipx → pip --user, first that works) + per registered-repo virtualenv (mirrors raindrop-setup.sh pattern), installs headroom as a persistent service (launchd on macOS, systemd user service on Linux) with a retry on first-start readiness miss, then wires Claude Code to route through the proxy via `headroom init --global --memory claude` — but only after confirming the proxy's `/readyz` endpoint is healthy, so `ANTHROPIC_BASE_URL` never points at a dead proxy. Superseded the old approach of adding `alias claude='headroom wrap claude'` to `~/.bashrc` (bash-only; never loaded under macOS's default zsh) — legacy aliases are now actively removed from `~/.bashrc`/`~/.zshrc` on re-run.
- Wired: `install.sh` `install_globals()` and `update.sh` tail both call `headroom-setup.sh` alongside `raindrop-setup.sh` — propagates to all machines on next install/update run.

---

## github.com/run-llama/liteparse
**URL:** https://github.com/run-llama/liteparse | **Added:** 2026-06-08
**Source / Author:** LlamaIndex (run-llama)

**What it's about:** Local-first PDF/document parser built in Rust (Apache 2.0). Extracts text with bounding boxes via PDFium, runs OCR via bundled Tesseract or pluggable HTTP servers (EasyOCR, PaddleOCR), generates page screenshots for LLM vision workflows, and supports PDF natively plus DOCX/XLSX/PPTX via LibreOffice and images via ImageMagick. Python, Node.js, Rust, and WASM bindings ship with a `lit` CLI. Fills the gap between raw documents and RAG pipelines without any cloud dependency.

**What we added:**
- Skill: `document-parsing` — 6-phase workflow (install → format selection → CLI patterns → Python API → RAG handoff → cloud escalation decision). Fires before any RAG pipeline or document processing task; bridges raw documents into `rag-implementation`.
- Hook: `doc-parse-nudge.sh` (UserPromptSubmit, global) — detects action verbs combined with document/RAG keywords and injects a reminder to invoke `document-parsing` before building. Registered in both `~/.claude/settings.json` and `templates/settings.harness.json.template`.
- Wired: `install.sh` and `update.sh` both call `scripts/setup/liteparse-setup.sh` — owns liteparse in `.venv-tools`; tries `uv venv --python '>=3.10' --seed` first (resolves a working interpreter automatically), falls back to system `/usr/bin/python3.*` (skipping uv-managed standalones whose stdlib is unreachable outside uv), and health-checks for broken uv-standalone venvs on each run — removing and rebuilding rather than failing silently. Exits 1 with hint (`uv python install 3.12`) if no working interpreter found.
- Docs: `docs/skills/document-parsing/README.md` — supported formats table, activation instructions, related skills.

---

## github.com/Kaelio/ktx
**URL:** https://github.com/Kaelio/ktx | **Added:** 2026-06-11
**Source / Author:** Kaelio (YC-backed)

**What it's about:** Open-source context layer for AI data agents (Apache 2.0). Sits between a warehouse and AI agents — ingests dbt, LookML, and BI tool definitions into git-reviewable YAML/Markdown, then exposes an MCP server that resolves grain, joins, and business metric logic and compiles safe SQL. Prevents LLMs from guessing schema. Supports Snowflake, BigQuery, Redshift, PostgreSQL, Databricks, and ClickHouse; integrates with Claude Code, Cursor, Codex, and LangChain.

**What we added:**
- Skill: `ktx-data-context` — 4-phase workflow (install/connect → build context → wire MCP server → review loop). Decision matrix for ktx vs raw SQL vs RAG. Full context file shape reference. Fires when building data agents or adding analytics capability to a skill (e.g. `cfo-insights/`).
- Config: commented-out `mcpServers.ktx` block appended to `templates/settings.json.template` — uncomment and merge to enable `ktx_query` / `ktx_schema` / `ktx_metrics` tools in any project.
- Docs: `docs/skills/ktx-data-context/README.md` — problem framing, activation conditions, MCP config snippet, warehouse/source support table, related skills.

---

## github.com/metareflection/guardians
**URL:** https://github.com/metareflection/guardians | **Added:** 2026-06-17
**Source / Author:** metareflection (Erik Meijer et al.)

**What it's about:** Python library (~1900 LOC core, MIT) that verifies an AI agent's *entire planned* tool-call workflow **before** any tool executes. Three independent static checks over the workflow AST: taint analysis (does untrusted data flow from a source to a forbidden sink?), security automata (does the tool-call sequence reach an error state?), and Z3 theorem proving (do pre/post/frame conditions hold?). `verify_first=True` guarantees flow checks run before execution. Canonical demo: an injected instruction inside an email tells the agent to forward the inbox to an attacker — the source→sink flow is rejected and the workflow never runs. The transferable insight is the **plan-the-whole-workflow → statically verify → execute** paradigm + taint source→sink; the Z3/automata/pydantic machinery is library-specific deployment detail.

**What we added** (one augmentation — the default-skip gate rejected everything else):
- Skill augmentation: `secure-agent-design` — new **Pattern 6: Plan-then-Verify — Static Taint & Data-Flow Guards**. Captures the design pattern: emit the full tool-call plan upfront, tag tool params with taint labels (sources) + sink params (forbidden destinations), statically check source→sink reachability + allowlist + forbidden data-flow rules, and reject the whole plan on violation before any tool fires. Complements `agent-execution-control` §2 (which gates one *runtime* action in isolation and is blind to cross-step data flow). Added a matching pre-ship checklist line. Guardians cited as reference implementation.

**Rejected:** standalone skill (better as augmentation), adopting guardians as a zora-repo dependency (product decision, heavyweight z3-solver), enforcement hook (no reliable static-analysis trigger), Z3/automata reference doc (hollow — text not behavior).

---

## github.com/s0xDk/ghostty-blackhole
**URL:** https://github.com/s0xDk/ghostty-blackhole | **Added:** 2026-06-17
**Source / Author:** s0xDk (MIT)

**What it's about:** A Ghostty fragment shader that renders a ray-traced black hole sized to the live Claude Code context-window fill. A single Python script, wired as a `statusLine` plus `SessionStart`/`SessionEnd` hooks, reads the session JSON Claude pipes on stdin, computes context fill (`context_window.used_percentage`), and encodes it into the **cursor color** via an `OSC 12` escape (amber base + 16-bit signature/checksum so a theme color can't accidentally drive the hole). `blackhole.glsl` decodes `iCurrentCursorColor` every frame and sizes the hole; cursor color is per-surface so each split/window gets its own. Requires Ghostty 1.3+. Ships a macOS Swift/SwiftUI tuner app (ignored — macOS-only).

**What we added** (implemented as fully opt-in per user request — two gates: not template-wired + `SDD_BLACKHOLE` env):
- Script: `scripts/integrations/blackhole/blackhole-cursor.py` — the encoder (statusLine + SessionStart/SessionEnd, self-routed on `hook_event_name`). `apply()` no-ops unless `SDD_BLACKHOLE=1`, so hooks/statusLine stay dormant by default and the statusLine still prints its readout without touching the cursor.
- Resource: `scripts/integrations/blackhole/blackhole.glsl` (verbatim, `SIZE_MODE MODE_TOKENS`) + `SETUP.md` (Ghostty `custom-shader` line, the exact `settings.json` snippet to paste, the `SDD_BLACKHOLE=1` toggle). Deliberately **not** added to `templates/settings.json.template` — a `statusLine` entry would replace Claude's default status line for every repo.
- Docs: `docs/integrations/blackhole/README.md` — what it is, data-flow diagram, the two-gate opt-in design, files, requirements, caveats.

**Rejected:** auto-wiring `statusLine` into the settings template (breaks opt-in — would replace everyone's default status line); the Swift/SwiftUI tuner app (macOS-only, no WSL/Linux home); a dashboard context-fill widget (the hole already *is* the live gauge — redundant); a separate bash SessionEnd clear-hook + SessionStart augmentation (the one Python script already self-routes all three roles — fewer moving parts).

---

## github.com/DietrichGebert/ponytail
**URL:** https://github.com/DietrichGebert/ponytail | **Added:** 2026-06-17
**Source / Author:** Dietrich Gebert

**What it's about:** A cross-agent (~13 hosts) skill/ruleset plugin (JS ~93%, Python ~6%) that constrains AI coding agents to write minimal code. Two Node.js lifecycle hooks **re-inject** a YAGNI "ladder" ruleset (does it need to exist? → stdlib? → native platform feature? → already-installed dependency? → one line? → minimum) on **every turn** at intensity levels `lite`/`full`/`ultra`/`off`, while protecting validation/error-handling/security from being stripped. Separately, deliberate shortcuts get a `ponytail:` inline comment marker, collected on demand by **manual** commands (`/ponytail-review`, `/ponytail-audit`, `/ponytail-debt`). Claimed: "80-94% less code · 3-6× faster · 42-75% cheaper." The default-skip gate rejected almost everything — the harness already enforces this thesis hard (CLAUDE.md AI-Legible Code every session, CAVEMAN every-turn injection, `karpathy-guidelines`, `simplify`).

**What we added** (two items — the minimalism ruleset itself was already covered):
- Skill augmentation: `karpathy-guidelines` §2 "Simplicity First" — a **deliberate-deferral marker** rule. When a real need is *consciously* postponed under simplicity pressure, tag it inline `# DEBT: <what was skipped> — revisit when <trigger>` (never for genuine YAGNI). No ledger file: `git grep -nE "(#|//)\s*DEBT:"` lists them. This is the one gap — the harness forces "do less" but had no way to track conscious tradeoffs. See also the prior `karpathy-guidelines` entry (2026-05-31).
- Dashboard widget: `count_debt_markers()` + a Maintenance Status banner in `scripts/utils/dashboard.py` — comment-anchored `git grep` (Markdown excluded so prose can't false-match; only tracked files), recomputed on **every dashboard launch**. **Improves on ponytail**, whose `/ponytail-debt` is a command you must remember — our count is always-current the next time you open the dashboard, with no command to run.

**Rejected:** the full YAGNI-ladder skill (>70% covered by `karpathy-guidelines`); a per-turn minimalism re-injection hook (duplicate of CLAUDE.md AI-Legible + CAVEMAN — noise/conflict); `/ponytail-debt` command + a dedicated daily runner (premature — scans nothing until markers accumulate; the dashboard line surfaces them for free, and `git grep` is the on-demand path); intensity levels (already modeled by CAVEMAN); dependency-bloat guard (covered by `karpathy-guidelines` §6).

---

## github.com/frankbria/ralph-claude-code
**URL:** https://github.com/frankbria/ralph-claude-code | **Added:** 2026-06-18
**Source / Author:** frankbria (MIT) — implements Geoffrey Huntley's "Ralph" technique

**What it's about:** A Bash harness (~9.4k stars) that runs the Claude Code CLI in a continuous autonomous loop until a project is finished, feeding `PROMPT.md`/`fix_plan.md`/`AGENT.md` context per iteration and parsing Claude's JSON output for a `RALPH_STATUS` block with an `EXIT_SIGNAL`. Its entire design targets the two failure modes of self-directed loops — **infinite looping** and **runaway API spend** — via intelligent exit detection plus rate limiting (`ralph_loop.sh`/`ralph_monitor.sh`/`ralph_queue.sh`, tmux orchestration, token budgeting, git backup, Docker/E2B sandboxes, multi-provider abstraction). The default-skip gate rejected nearly everything — the harness is already loop-saturated (`loop-patterns`, `iterative-repair-loop`, `/loop`, `kiro:autoresearch`, `goal-mode`).

**What we added** (one augmentation — the loop machinery itself was already covered):
- Skill augmentation: `loop-patterns` — new **Loop Guardrails** section. (a) **Dual-condition exit**: don't stop on the heuristic check alone; require an explicit one-line confirmation that the green output truly means the goal is met (guards stopping-too-early on flaky/partial passes). (b) **Circuit breakers** checked every pass, independent of the max cap: abort on 2 consecutive no-progress passes or 2 consecutive identical-error passes, then stop and report. This is the one gap — `loop-patterns` previously had only a one-line "use a cap to prevent runaway" note (`SKILL.md:234`), with no same-error/no-progress detection and a single-condition exit, which is exactly Ralph's thesis.

**Rejected:** `ralph_loop.sh`/`ralph_monitor.sh`/tmux orchestration (harness drives loops via `ScheduleWakeup`/`/loop` + `daily-orchestrator.sh`, not external bash spinning the CLI — would fork the loop model); `ralph-stats` metrics + token budgeting (dashboard + `Workflow` budget already cover); desktop notifications (`PushNotification`); git backup/rollback (atomic per-task commits already); Docker/E2B sandbox (infra decision, not a harness behavior); multi-provider abstraction (harness is Claude-only by design — anti-goal); log rotation + `PROMPT.md`/`fix_plan.md`/`AGENT.md` file conventions (harness uses `specs/`/`steering/`/`CLAUDE.md`).

---

## github.com/usestrix/strix
**URL:** https://github.com/usestrix/strix | **Added:** 2026-07-02
**Source / Author:** usestrix — autonomous AI pentesting platform

**What it's about:** An open-source autonomous pentesting platform that runs a graph of agents (recon → exploit → post-exploit) against live targets, validating findings with working PoCs. Its most reusable asset is not the code but its `strix/skills/` library: ~40 deep per-vulnerability-class knowledge packages (IDOR, SSRF, SSTI, GraphQL, race conditions) with attack-surface maps, bypass matrices, chaining logic, and false-positive catalogs. The default-skip gate rejected the whole platform — the harness has no live-exploitation sandbox — but the tool-agnostic *triage-reasoning* subset filled a real gap.

**What we added** (one augmentation — the platform and its agent orchestration were already covered):
- Skill augmentation: `ai-security-workflow` — new `references/vuln-class-heuristics.md` distilling per-class whitebox review heuristics (non-obvious cases: batch endpoints validating only `items[0]`, second-order injection, resolver-boundary GraphQL authz) plus a three-gate **false-positive discipline** (reachability → binding → prove-the-negative) and per-class FP catalog. Wired as two pointer lines into Phase 2 (Vuln Scan → "what to look for") and Phase 3 (Triage → the validation gate). Attacks the harness's weakest security link — triage noise — where the SKILL.md previously listed only class *names*.

**Rejected:** the full 40-file offensive library (~90% live-exploitation tradecraft presuming a running target + sandbox the harness lacks); Graph-of-Agents orchestration (covered by `multi-agent-patterns` + `secure-agent-design`); dynamic skill-injection (native Claude Code progressive disclosure + `skill-creator`/`skill-curator`); scan modes (overlap `model-tiers`/`progressive-complexity-ladder`); `source_aware_sast` (hard-requires semgrep/ast-grep/trivy — out of scope); headless CI mode (already have `security-report-runner.sh` + `pr-report`); nmap/nuclei/ffuf tooling playbooks (offensive CLI reference, out of scope).

---

## github.com/nadimtuhin/claude-token-optimizer
**URL:** https://github.com/nadimtuhin/claude-token-optimizer | **Added:** 2026-07-02
**Source / Author:** nadimtuhin

**What it's about:** A CLI (`cto`) that reframes token cost as a *documentation-structuring* problem — it minimizes the auto-loaded session-startup payload, then audits/compresses it deterministically with CI-friendly checks. Its optimization axis is **orthogonal** to existing harness tooling: RTK and lean-ctx attack *runtime* cost (command output, file reads) and Headroom attacks *API-context* cost, while `cto` attacks *startup* cost (what loads before you do anything). The default-skip gate rejected everything except that one uncovered axis.

**What we added** (one routine + one skill augmentation + one dashboard card):
- Routine: `scripts/routines/startup-payload-audit.sh` — deterministic (no LLM) daily audit measuring the fixed per-session token tax (`CLAUDE.md` + `@imports` + `.claude/rules/*` + auto-loaded `MEMORY.md`), flagging over-budget growth, stale files, and ghost references. Writes `.claude/reports/context/startup-payload.json`. Wired into `daily-orchestrator.sh` `run_one()` with `SDD_SKIP_STARTUP_AUDIT=1` opt-out + state-file cadence guard; registered in `_scheduled_task_registry()`; chmod-listed in install.sh + update.sh.
- Skill augmentation: `context-optimization` — new "Two Independent Token Axes: Startup vs Runtime" section naming the startup lever (which RTK/lean-ctx never see) and the inversion principle (*comprehensiveness → archive; essentiality → startup payload*).
- Dashboard widget: `_startup_payload_card()` in `scripts/utils/dashboard.py` — a Startup Payload card in the **Budget & Efficiency → Context Health** tab (tokens, budget status, stale/ghost counts, top files).

**Rejected:** `docs/learnings/` keyword-inject hook (covered by lean-ctx `ctx_knowledge wakeup` + MEMORY/hot-memory); framework `COMMON_MISTAKES.md` (covered by `ERRORS.md` + `tool-failure-memory`); `cto compress/prune` (overlaps `ctx_compress` — on-disk-doc part folds into the audit output); pre-tool read/bash blocking guards (RTK/lean-ctx already *reduce* > *block*); `cto watch` live dashboard (duplicates `rtk gain`); literal `.claudeignore` generation (harness auto-loads via `@import`/auto-memory — the audit *verifies* the principle instead).

---

## github.com/typedef-ai/fenic
**URL:** https://github.com/typedef-ai/fenic | **Added:** 2026-07-02
**Source / Author:** typedef-ai

**What it's about:** A PySpark-inspired DataFrame library that treats LLM inference as a first-class, typed, lazily-evaluated query operator (`semantic.extract/classify/predicate/reduce/join`, `sim_join`, `with_cluster_labels`), giving batch LLM work automatic caching, per-query cost accounting, and row-level lineage. The non-obvious insight — lazy eval is what unlocks the caching/cost/lineage layer — is covered by no existing harness skill (rag-architect = retrieval, structured-web-dataset = sourcing, csv-data-summarizer = post-hoc analysis, document-parsing = ingestion).

**What we added** (one pattern skill — the library itself is on-demand via `get-api-docs`):
- Skill: `semantic-data-pipeline` — a lean, library-agnostic **decision** skill: when batch LLM transformation over many rows needs reproducibility, model it as a lazily-evaluated query with a caching/cost/lineage layer, not an ad-hoc loop. Teaches the four-operator taxonomy (extract/classify/predicate/reduce) and the explore→pipeline→governed-tool graduation path, with fenic as the reference implementation. Explicit "Do NOT Activate" boundaries route sourcing → `structured-web-dataset`, retrieval → `rag-architect`, ingestion → `document-parsing`, analysis → `csv-data-summarizer` to prevent wrong-skill firing.

**Rejected:** a fenic-as-a-tool API tutorial skill (the "here's a library that exists" anti-pattern — `get-api-docs` handles docs on demand); `fenic check` linter as a harness pattern (harness authors no library agents call; ruff/serena/pytest already gate its own code); `fenic skill install` (duplicates `skill-creator`/`skill-curator`); operator taxonomy into `rag-architect` (adds vocabulary not decision value; bloats a mature skill); all hook/routine/dashboard/script/command angles (on-demand design decision, nothing to automate).

---

## langchain-ai/openwiki
**URL:** https://github.com/langchain-ai/openwiki
**Added:** 2026-07-08
**Source / Author:** LangChain

**What it's about:** A TypeScript CLI that auto-generates a codebase wiki in `openwiki/` by running an LLM over source files and appending stub instructions to AGENTS.md/CLAUDE.md pointing agents to consult it. Optional LangSmith tracing and CI/CD PR automation for doc updates.

**What we added:** Nothing — SKIP.

**Rejected (all candidates):** Auto-generated wiki content is equivalent to what `adapt-to-repo` derives on-demand from the live codebase. Appending to CLAUDE.md is the exact anti-pattern `instruction-architecture` guards against (uncontrolled growth). The `steering/`, `memory/`, and `specs/` layers cover curated documentation with provenance. CI/CD PR automation for docs conflicts with CLAUDE.md's "never commit SDD files" rule. External binary dependency with no freshness guarantees adds maintenance cost for zero capability gain.

---

## github.com/github/spec-kit
**URL:** https://github.com/github/spec-kit
**Added:** 2026-07-28
**Source / Author:** GitHub

**What it's about:** GitHub's official spec-driven development toolkit (constitution → spec → plan → tasks → implement, via a `specify` CLI). Heavy conceptual overlap with the local `kiro:` SDD suite (near 1:1 on init/requirements/design/tasks/impl). Only the genuine gaps were mined.

**What we added:**
- Command: `commands/kiro/converge.md` — from `/speckit.converge`. Reconciles the live codebase against an approved spec, classifying drift as {spec-ahead / code-ahead / contradiction} with a recommended action each. Reuses the existing `validate-impl-agent` in a CONVERGE mode (no new agent). Fills a real gap: kiro validated impl but had no spec↔code reconciliation.
- Agent augmentation: `agents/kiro/harness-validate-agent.md` — from `/speckit.analyze`. Added a cross-artifact spec-consistency check (spec ↔ requirements ↔ design ↔ tasks traceability: no task without a requirement, no requirement with zero tasks, no design element without a spec driver), with a scope guard to skip mid-authoring specs. Previously `harness-validate` did structural integrity only.

**Rejected:** wholesale adoption of the `specify` CLI + multi-agent integration layer (redundant with the kiro suite); `/speckit.taskstoissues` (complements `jira-solve` but lower priority — deferred).

---

## github.com/google/mantis
**URL:** https://github.com/google/mantis
**Added:** 2026-07-28
**Source / Author:** Google

**What it's about:** A model-agnostic framework letting AI coding agents autonomously find, reproduce, and patch software vulnerabilities via a ~15-stage pipeline of discrete skills that pass state through JSON files, executing generated code in Docker/gVisor sandboxes. Security-domain-specific, but two architectural patterns transcend the domain.

**What we added:**
- Skill augmentation: `skills/ai-security-workflow/SKILL.md` — "Architectural Patterns" section: JSON-state pipeline (stages pass machine-readable state for resumability + mechanical dedup) and sandboxed-reproduction verification (prove a finding by reproducing it in a sandbox, not by reasoning; false-positive filter before patching).
- Agent augmentation: `agents/kiro/reflect-agent.md` — "Cross-Run Learning Loop" (append-only `learnings.jsonl` of `{situation, insight, applies_when}` intended to feed a future planning step). NOTE: currently write-only — the read-back side is aspirational until a planning command is wired to consume it.

**Rejected:** wholesale adoption of the security pipeline (domain-specific; the harness already has `ai-security-workflow` + `security-review`); the meta-agent supervisor (covered by `multi-agent-patterns`).

## github.com/cursor/plugins — show-me-your-work SKILL.md
**URL:** https://github.com/cursor/plugins/blob/3fe2823ce17c1656c222d4b7c59d3f82fbf20143/pstack/skills/show-me-your-work/SKILL.md | **Added:** 2026-07-28

**What it's about:** A small, fully-formed Claude Code skill for long-running/autonomous work: an append-only TSV decision log (timestamp, phase, decision, rationale, evidence-pointer, result), a "one line or it's not crisp yet" brevity rule, an evidence-as-pointer requirement (commit SHA/file path/test result, never prose), and a cross-model review gate before declaring work complete that flags weak evidence in an "Attention" section.

**What we added:**
- Skill enhancement: `verification-skill-authoring` — added as a concrete optional template operationalizing the existing independence-budgeting concept (the decision log is the artifact, the cross-model review is the independence check).

---

## github.com/dinosn/raptor-loop-hunt
**URL:** https://github.com/dinosn/raptor-loop-hunt | **Added:** 2026-07-28

**What it's about:** An autonomous security-vulnerability-hunting Claude Code skill running a generate→judge→verify loop across multiple code altitudes, with a persistent state machine tracking every finding's lifecycle and evidence.

**What we added:**
- Skill enhancement: `loop-patterns` — new "Cross-run state" guardrail generalizing the disposition-ledger (candidate→verified/rejected state machine with evidence receipts, prevents re-litigating resolved findings) and monotonic-knowledge-base (persistent dedup across repeat runs) patterns beyond the security-audit domain.

---

## github.com/plasma-ai/fractal
**URL:** https://github.com/plasma-ai/fractal | **Added:** 2026-07-28

**What it's about:** A framework for hierarchical/recursive agent trees — parent nodes spawn child nodes for separable subtasks, each isolated in its own git worktree, with hard resource caps (iterations, depth, children, cost, time) enforced per node at spawn time.

**What we added:**
- Skill enhancement: `multi-agent-patterns` — new "Spawn-time resource caps" note: when a coordinator spawns nested sub-coordinators (not just flat workers), assign explicit max-iterations/depth/children/cost/time caps at spawn time as a circuit breaker against runaway recursion.

---

## github.com/humanlayer/12-factor-agents
**URL:** https://github.com/humanlayer/12-factor-agents | **Added:** 2026-07-28

**What it's about:** Influential guide applying "12-factor app" discipline to LLM agents — production agents as mostly deterministic software with strategic LLM call-outs, not autonomous loops.

**What we added:**
- Verification pass against `agent-harness-design` + `agent-execution-control` (most factors already landed via the 2026-07-14 triage). Two genuine gaps found and closed: Factor 5 "unify execution/business state" (added to `agent-harness-design`'s Orchestration Loop section) and Factor 12 "stateless reducer" — `(state, event) → new_state` framing (added to `agent-execution-control`'s Plan-Execute-Verify Loop section). Factor 9 "compact errors into context window" was already covered by Execution Trace Grounding — no edit needed.

---

## github.com/perplexityai/numbat
**URL:** https://github.com/perplexityai/numbat | **Added:** 2026-07-30

**What it's about:** Go-based AI-agent endpoint-monitoring tool. Runs a rule engine over both live events and forensic (post-hoc) events: network-indicator/SSRF detection, persistence-mechanism detection, and sequence/chained findings that correlate events across a session. Rules ship as versioned, signed bundles and support a monitor→enforce promotion model.

**What we added:**
- Hook: `hooks/claude/agent-behavior-guard.sh` — `PreToolUse` (matcher `Read|Bash|WebFetch|WebSearch`). Ported three of numbat's detections, scoped down for a single local harness (no rule files, no versioning, no signed bundles): `network_indicator` (cloud-metadata SSRF endpoints), `persistence` (crontab/rc-file/authorized_keys/systemd writes), and `chained_secret_egress` (a secret-bearing path accessed, then an egress call made later in the same session — correlated via a per-session ledger). Fills the gap `protected-path-hook.sh` doesn't cover: that hook only fires on `Write|Edit` and is stateless per call. Default mode is monitor-only (logs to `.claude/memory/agent-security-findings.jsonl`); `SDD_AGENT_GUARD_ENFORCE` promotes named rules (or `all`) to hard-block, mirroring numbat's monitor→enforce toggle without its rule-file machinery.
- Documented in `docs/hooks/README.md` (new `agent-behavior-guard.sh` section + Wiring Reference row).

**Correction (2026-10-01):** the monitor→enforce toggle above did not actually work as
shipped. The script ended in a bare `exit 0` that discarded the Python's `sys.exit(2)`,
so a rule named in `SDD_AGENT_GUARD_ENFORCE` logged `"mode": "enforce"` findings and
blocked nothing. The Python's exit code is now the verdict, and with any rule enforced
the guard also fails closed on its own failures (malformed event, `python3` missing,
crash). Monitor mode is unchanged — still silent, still exit 0. Covered by the new
`hooks/claude/agent-behavior-guard.test.sh` (13 cases). See the Perplexity "How we
engineer safer agents" entry in [articles/README.md](../articles/README.md), which is
where the audit that found it came from.

**Rejected:**
- Full rule-file/YAML DSL with versioning and cryptographic signing — massive overkill for a single-user local harness; three inline regex rules cover the real gap.
- Forensic/replay event-source scanning — numbat's live+forensic dual-source model doesn't map to Claude Code's tool-call event stream; not applicable here.
- Separate `numbat`-style CLI binary/daemon — the existing PreToolUse hook mechanism already provides the enforcement point; no separate process needed.

---

## github.com/mattpocock/skills — `wayfinder` skill
**URL:** https://github.com/mattpocock/skills (skill: `skills/engineering/wayfinder/SKILL.md`) | **Added:** 2026-08-02

**What it's about:** Plans oversized/ambiguous projects via a durable "map" — a single issue on the repo's issue tracker (GitHub/GitLab/local-markdown) indexing decision tickets (child issues). Tracks resolved decisions, fog-of-war (not-yet-specified), and out-of-scope, with blocking relationships and claim-by-assignment semantics across many async/concurrent sessions. Depends on companion skills (`/grilling`, `/domain-modeling`, `setup-matt-pocock-skills` onboarding, triage labels) for a whole adjacent SDD-style ecosystem.

**What we added (ran `better-call`: verdict AUGMENT INCUMBENT, 21/30 vs 25/30, kiro's idea-refine→spec-grill pipeline):**
- Skill: `issue-triage-routing` — added axis 4 "Scale" (feature vs program) and a new **PROGRAM** routing outcome (precedence: defer > program > clarify > spec > one-shot).
- Command: `idea-refine` — on PROGRAM route, charts `specs/_maps/<name>.md` (Destination/Decisions-so-far/Fog/Out-of-scope) via the existing idea-refine-agent, then auto-decomposes the first fog item into a slice and re-triages it — hands off to spec-quick/spec-init in the same pass, no standalone command.
- Command: `spec-tasks` — on tasks approval, auto-checks `specs/_maps/*.md` for a fog entry matching the completed feature and moves it to Decisions-so-far. Self-maintaining map, no manual upkeep.
- Template: `kiro/settings/templates/specs/map-init.md` — new map scaffold matching existing `{{PLACEHOLDER}}` template convention.
- Docs: `docs/harness-documentation/SDD-USAGE.md` — updated `idea-refine`/`spec-tasks` entries + Typical Workflow note.

**Rejected:**
- Whole wayfinder ticket/claim/tracker machinery — solves multi-contributor/concurrent-session claiming; this is a solo WSL harness with no issue tracker in use anywhere (confirmed via grep before proposing).
- `setup-matt-pocock-skills` onboarding skill — duplicates `.claude/steering/`, which already owns per-repo project config.
- Triage label vocabulary (`needs-triage`, `ready-for-agent`, etc.) — no `triage` skill installed, no team workflow to triage for.
- `wayfinder:<type>` ticket taxonomy and claim/assignment semantics — pure concurrency control for multiple humans/agents on the same tracker; not applicable to single-user sessions.

---

## github.com/braintrustdata/agentbehavior
**URL:** https://github.com/braintrustdata/agentbehavior | **Added:** 2026-08-02

**What it's about:** Open standard for `BEHAVIOR.md` — a durable spec of expected agent conduct across a whole trajectory (Intent/Evidence/Decision/Execution/Recovery/Failure modes), stored per-project next to the agent it describes. Deliberately not a runtime prompt: answer-key material a reviewer/judge uses to grade a completed trajectory, kept blind from the agent being evaluated. Ships a portable authoring skill, a structural CLI validator, and a `true/false/na` judging convention.

**What we added:**
- Skill: `writing-behavior-specs` — ports the authoring/calibration workflow (3-part worth-saving test, 4-fixture calibration) to `.claude/behaviors/<name>/BEHAVIOR.md`. Invoked only by the agent below, never by the user.
- Agent: `agents/kiro/behavior-spec-agent.md` — new subagent wired as Step 6b of `/kiro:daily-maintenance` (sibling to `skill-augment-agent`). Reads today's judge drains, `type: feedback` memories, and revert/drain observations; drafts/revises up to 3 BEHAVIOR.md specs/run when the same conduct class recurs ≥2 times (human feedback auto-qualifies at 1).
- Script: `scripts/validate-behavior-spec.py` — small stdlib-only structural validator (frontmatter, name-matches-directory), ported instead of adopting the upstream npm CLI. Called automatically by the agent above, never run by hand.
- Docs: added a `.claude/behaviors/` line to `templates/CLAUDE.md.template` and this repo's own `CLAUDE.md`; added step 8b to the pipeline description in `docs/harness-documentation/SDD-USAGE.md`; added a row to `scripts/README.md`.
- Eval-gate: ran `skill-eval-gate` (3 scenarios, baseline vs. treatment) before finalizing — PASS (treatment beat baseline on 2/3 scenarios: correct BEHAVIOR.md frontmatter schema, correct auto-qualify-at-1 rule for feedback memories; tied on an easy reject case).

**Rejected:**
- Session-start bi-weekly interactive nudge (claudemd-review pattern) — riding the existing unattended nightly `daily-maintenance` pipeline is strictly more automated (no live session required) and avoids a redundant cadence file.
- `tool-failures.jsonl` as the primary recurrence signal — too mechanical/syntactic (broken commands, not conduct); judge drains / feedback memories / revert observations are the right evidence source.
- Adopting the upstream npm `agentbehavior` CLI — harness has no Node/pnpm dependency for its own tooling; the structural checks are 3 simple assertions, covered by a small stdlib Python script instead.
- Dashboard widget and standalone drift-audit routine — no persistent metric or audit need yet; premature before any project has accumulated specs.

---

## github.com/fcakyon/claude-codex-settings
**URL:** https://github.com/fcakyon/claude-codex-settings | **Added:** 2026-08-02

**What it's about:** 30-plugin bundle of Claude Code / Codex / Cursor configs, hooks, skills, and subagents (Fatih Akyon). Mostly per-service skills (mongodb, stripe, azure, etc); the value here was in a handful of well-built hooks and two cross-model "second opinion" subagents.

**What we added:**
- Hook: `ai-writing-guard-hook.sh` (new) — `PreToolUse` deny on `Write|Edit|MultiEdit|Bash`. Blocks AI-sounding word/phrase patterns before they land in a file write/edit or a git-commit/gh message. Ported from the `humanize` plugin, with the em-dash check dropped (conflicts with this harness's own house style).
- Hook: `reject-feedback-hook.sh` (new) — `UserPromptSubmit`, soft. Detects a rejected/interrupted tool call and classifies the user's follow-up into a reject reason, appending a `[friction]` line to `observations.md` for actionable categories only. Ported from `claude-telemetry-hooks`'s `user_prompt_reject_feedback.py` with the OTel export dropped (no OTel backend configured here) — reuses the existing `observations.md` append convention instead of a new file/pipeline, and is a distinct signal from `tool-failure-capture.sh`/`tool-failure-recall.sh` (command *execution* errors, not user pushback), confirmed via audit before implementing.
- Augmentation: `compaction-discipline-hook.sh` — added 5 concrete fidelity sections (unanswered-question tracking, root-cause vs ruled-out-hypothesis separation, file importance tiers, subagent results as primary evidence, A-vs-B comparison preservation), via a `better-call` AUGMENT INCUMBENT verdict (25/30 vs 29/30) against the `intelligent-compact` plugin's `precompact_priorities.sh`.
- Augmentation: `git-destructive-guard-hook.sh` — now strips quoted segments before matching (kills a false-positive when a force flag is mentioned inside a quoted commit message) and blocks bare `git rebase`, via a `better-call` AUGMENT INCUMBENT verdict (26/30 vs 27/30) against the `ultralytics-dev` plugin's `block_force_push.py`.

**Rejected:**
- `simplify`, `github-dev`, `adhd-output-style`, `agent-browser` — each a duplicate of an existing harness skill or hook (own `simplify` skill; `git-pushing`/`create-pr`/etc.; the caveman-mode hook system; `playwright-skill`/`browser-automation`).
- `codex-advisor` / `fable-advisor` (cross-model second-opinion subagents) — genuinely different pattern, but `codex-advisor` needs an unconfirmed external `codex` CLI dependency; not proposing blind.
- `claude-telemetry-hooks`'s `session_start_chat_id.py` — pure OTel session-linking telemetry, dead code with no OTel backend configured.
- ~15 per-service skills (mongodb, supabase, stripe, polar, livekit, cloudflare, hetzner, dokploy, azure-tools, gcloud-tools, react-skills, python-skills, overleaf-skills, paper-search-tools, tavily-tools, openobserve-skills, web-performance-skills, frontend-design-skills, anthropic/openai-office-skills) — mostly duplicate the harness's existing catalog for services not confirmed in use; domain-saturation concern (generic dev-tooling, not an underserved domain).

---

## github.com/prime-radiant-inc/smevals
**URL:** https://github.com/prime-radiant-inc/smevals | **Added:** 2026-08-04

**What it's about:** External pip/uv CLI eval tool (180 stars, ~3 weeks old). Core model: Eval (dir) > Task (yaml prompt) x Config (yaml: runner script + model) → Run (immutable, disk-persisted via `SMEVALS_MODEL`/`SMEVALS_PROMPT`/`SMEVALS_RUN_DIR` env vars passed to any user-supplied Runner executable) → Grader (list of Checkers, built-in or custom, emitting JSON score/notes + exit code) → Grade (separate from Run, resumable/regradable). Generalizes generation to any CLI-wrapped model/agent via the Runner contract, not just one provider.

**better-call verdict:** AUGMENT INCUMBENT (19/30 challenger vs 21/30 incumbent `local-llm-eval`/`ollama_model_test.py`, source `github.com/ulyssestenn/omt`). Challenger scored higher only on generalization/coverage-breadth (Runner contract works for any CLI model, not just Ollama) and grading rigor (structured Checker/Grade split with pass/fail exit codes) — everything else (maintenance cost of a new external uv/pip dependency from a small, very new repo; harness fit; automation potential) favored the incumbent's zero-dependency stdlib design.

**What we added:**
- Augmentation: `scripts/utils/ollama_model_test.py` — grafted smevals' Runner and Checker/Grader contracts onto the existing script instead of adopting the tool wholesale.
  - `--runner PATH` (new): invokes an executable once per generation with the prompt on stdin and `OMT_MODEL`/`OMT_PROMPT`/`OMT_RUN_DIR` in its environment; stdout becomes the recorded response. Requires `--model`. Falls back to the existing Ollama HTTP path (`generate_once()`) when omitted — zero behavior change for existing callers.
  - `--checker PATH` (new): invokes an executable once after all generations for a model complete, with `OMT_RUN_DIR`/`OMT_MODEL` set. stdout must be JSON `{"pass": bool, "score": float, "notes": str}`; result is appended to a new `grades.json` in the run directory (parallel to `metadata.json`) and a PASS/FAIL line is printed.
  - Docs: `skills/local-llm-eval/SKILL.md` Phase 3 (runner example) + new Phase 6 (Grading); `docs/evaluation/local-llm-eval/README.md` flags table + `grades.json` schema.

**Rejected:**
- Wholesale adoption of `smevals` as a new external dependency — a small (180-star, 3-week-old) startup repo; the specific gap (generalized Runner + pass/fail Grader) was fully closeable as a targeted augmentation of the existing zero-dependency incumbent, per the `better-call` verdict.
- `skill-eval-gate` Runner backend integration — that skill's ad-hoc baseline/treatment subagent comparison for grading SKILL.md changes is a different eval shape (agentic scenario scoring, not raw prompt/model output grading); the Runner contract doesn't map cleanly onto it.
- Static-site report generation (`smevals serve`/`build`) — no persistent multi-eval-suite need yet; premature before the harness accumulates enough graded runs to warrant a report UI.
- Resumable `-n N` top-up semantics (`smevals run -n N` is idempotent, no-op once N successful runs exist) — the incumbent's `--runs N` always runs N fresh generations; adding idempotent top-up is a bigger behavior change than the augmentation warranted and wasn't part of the approved scope.

---

## github.com/herdrdev/herdr
**URL:** https://github.com/herdrdev/herdr | **Added:** 2026-08-16

**What it's about:** "The runtime your coding agents live on" — a Rust single-binary terminal multiplexer/runtime purpose-built to host coding-agent CLI sessions (Claude Code, Codex, Cursor, and others). Sessions persist through disconnects/restarts and are reattachable from any terminal; every pane's agent is classified `idle`/`working`/`blocked`/`done`/`unknown` via bundled detection. Exposes a `herdr` CLI + JSON socket API so scripts or other agents can split panes, start named agents, prompt them, wait on lifecycle-state changes, and read output.

**better-call verdict:** ADOPT CHALLENGER (20/30 vs 13/30 incumbent). Incumbent (`agent-manager-skill`, a thin wrapper pointing at a live `git clone` of a third-party repo, `risk: unknown`) doesn't reliably detect agent state at all — just tmux process liveness. Herdr is materially better built: purpose-made lifecycle detection, a documented CLI/socket surface, and an upstream `SKILL.md` with sharp gates already matching this harness's own skill-quality bar.

**What we added:**
- Augmentation: `skills/agent-manager-skill/SKILL.md` — rewritten with Herdr's command grammar verified against the real upstream `herdrdev/herdr` v0.8.0 skill (`HERDR_ENV=1` gate, `herdr agent start/prompt/wait/read/send-keys`, `herdr pane split/run/wait-output`), with the original tmux+python3 path preserved as an explicit fallback (`Path 2`) for sessions not running inside Herdr.

**Rejected:** new standalone `herdr` skill (would duplicate `agent-manager-skill` — augment, don't fragment, in an already-saturated multi-agent-orchestration domain alongside `dispatching-parallel-agents`/`multi-agent-patterns`/`loki-mode`/`using-git-worktrees`/`conductor-*`); hook wrapping `HERDR_ENV` checks (fails all 4 hook signals — the skill's own inline gate already enforces this, no lifecycle event applies); slash command wrapping the `herdr` CLI (hollow — the CLI is already the interface); scheduled polling routine (Herdr's own `wait`/`wait-output` are already event-driven, blocking calls — nothing to poll); dashboard widget (no dashboard system in this harness's installation to extend).

---

## github.com/mattpocock/skills — second pass (23 remaining skills)
**URL:** https://github.com/mattpocock/skills | **Added:** 2026-08-16

See also: [github.com/mattpocock/skills — `wayfinder` skill](#githubcommattpocockskills--wayfinder-skill) (2026-08-02) for the first extraction from this repo.

**What it's about:** Matt Pocock's personal Claude Code/Codex skill library (~218k stars, shipped as an official Claude Code plugin) — ~18 engineering skills and 7 productivity skills built around a linear grill → spec → tickets → implement → review flow, plus a router skill (`ask-matt`).

**What we added:**
- Skill: `skills/wizard/SKILL.md` + `template.sh` (new) — interactive bash setup-wizard generator for manual/human-click provisioning steps (stage-by-stage progress, cross-platform URL opening incl. WSL, idempotent `.env` upserts, `gh secret`/`gh variable` writes). Content verified against the real upstream `skills/engineering/wizard/` source, not paraphrased.
- Skill: `skills/prototype/SKILL.md` + `LOGIC.md` + `UI.md` (new) — throwaway-prototype methodology: a single shareable HTML demo for sanity-checking state/logic, or several structurally-different UI variants switchable via `?variant=` for "what should this look like" questions. Cross-referenced from `spec-grill`/`idea-refine` as an optional detour when talk isn't resolving a design question. Content verified against the real upstream source.
- Augmentation: `skills/git-advanced-workflows/SKILL.md` + new `references/git-conflict-resolution.md` — the skill pointed to this reference file for advanced conflict resolution, but the entire `references/` directory didn't exist on disk (confirmed before writing). Filled with a 5-step discipline (see current state → find primary sources/original intent → resolve preserving both intents, never invent behavior → run automated checks → finish, never `--abort`), adapted from `resolving-merge-conflicts` and verified against its real upstream content.
- Augmentation: `agents/kiro/save-session-agent.md` — added a "Suggested Skills" section to the session-file template and two new Important Constraints (no duplication with specs/ADRs/commits — reference by path instead; redact secrets from captured evidence), adapted from the `handoff` skill and verified against its real upstream content (which also surfaced the redaction rule, not originally flagged).

**Rejected:** `ask-matt` router (this harness's routing role is already served by `docs/harness-documentation/SDD-USAGE.md` + `/kiro:spec-status` + CLAUDE.md's Context Resources table over a fixed pipeline, not a loose 20-skill catalog); `domain-modeling`/`grill-with-docs`/`grilling` (already covered by `/kiro:spec-grill` + per-spec `CONTEXT.md`/ADRs + `.claude/steering/`); `setup-matt-pocock-skills` (duplicates `steering-agent`/`/kiro:adapt-to-repo`); `to-spec`/`to-tickets`/`implement`/`tdd` (superseded by the harness's own SDD pipeline — `wayfinder` specifically already processed 2026-08-02); `triage` (`issue-triage-routing`); `research` (`deep-research`/`research-engineer`/`storm-research`/`search-specialist`); `codebase-design`/`improve-codebase-architecture` (`codebase-legibility`/`repo-drift-review`/`refactoring-safely`/`architecture-patterns`); `teach`/`to-questionnaire`/`wait-what` (off-mission for an engineering harness); `writing-for-agents` (`instruction-architecture`/`writing-skills`/`skill-creator`); `diagnosing-bugs` (`debugging-strategies`/`systematic-debugging`/`/kiro:debug`); `grill-me` (duplicate of `grilling`); parallel-dispatch Standards+Spec review pattern from `code-review` — verified moot rather than assumed: `agents/kiro/spec-impl.md` already auto-spawns `spec-refactor-agent` (the Standards axis) as step 5 of every single task, and the Spec axis (`validate-impl-agent`) is deliberately feature-level, not per-task, so bundling it in would be a category error, not a missing feature.

---

## github.com/memcode-ai/memcode
**URL:** https://github.com/memcode-ai/memcode | **Added:** 2026-08-16

**What it's about:** Go-based, MIT-licensed standalone coding-agent CLI and self-hosted messaging gateway (Telegram/Discord/Slack/WhatsApp/etc.) — a direct competitor to Claude Code itself. Headline features: per-repo persistent memory (`.memcode/`, committed with the repo), client-side adaptive model routing, a 4-event hook system, and a plan-mode with cross-model review.

**What we added:**
- Hook: `hooks/claude/ruff-quality-gate-hook.sh` (new) — `PostToolUse` on `Write|Edit|MultiEdit`, advisory `ruff check` on touched `.py` files. Closes a real, self-declared gap: `CLAUDE.md`'s Quality Gates section states "`ruff check`: on every `.py` file write" as automated, but no hook actually ran it before this — only a `Bash(ruff check *)` permission entry that lets Claude run it if it remembers to. Wired into `templates/settings.json.template`, `templates/settings.harness.json.template`, and this repo's own `.claude/settings.json`; verified against a real `ruff`-absent machine (silent no-op) and a real lint finding.

**Rejected:** `.memcode` persistent memory (already far exceeded by `.claude/memory/` + the `memory-systems`/`agent-memory-consolidation`/`agent-memory-discipline`/`agent-memory-mcp` skill cluster; memcode's own docs don't disclose the internal schema, so nothing concrete to port beyond "share memory via git," which this harness already does); hook system (already more mature — 7 event types here vs memcode's 4, plus self-healing `settings.json` repair memcode doesn't have); adaptive model routing (client-side API interception has no equivalent hook point in a prompting-only harness; `model-tiers` already covers the portable analog — manual tier-selection guidance for subagent spawns); context compaction (already covered by `compaction-discipline-hook.sh`'s detailed fidelity rules; fetch of memcode's own `COMPACTION.md` failed repeatedly, so nothing to compare against); plan-mode with cross-model review (already covered by `spec-refactor-agent`/`session-judge`/`validate-adversarial-agent`, and a near-identical cross-vendor "second opinion" subagent was already rejected in a prior extraction — `codex-advisor`, above — for needing an unconfirmed external CLI); messaging gateway (already covered by per-service automation skills + `notify`); migration tooling, TUI features, sub-agents/background jobs (native Claude Code features or product-specific — no gap); internal memory-adjacent modules (`internal/{recall,knowledge,...}`) — name-only from a directory listing with no fetchable content, would be a hollow/fabricated addition.

---

## github.com/x74353/Amphetamine
**URL:** https://github.com/x74353/Amphetamine | **Added:** 2026-08-18

**What it's about:** Not the Amphetamine sleep-prevention app itself — a narrow companion repo ("PowerProtect") that patches a Closed-Display-Mode bug on Apple Silicon MacBooks via a `defaults write` toggle + `sudoers.d` entry. No general scriptable sleep-prevention API is exposed here; that lives in the main paid app, not this repo.

**What we added:**
- Script: `scripts/orchestration/setup-mac-orchestrator.sh` — wrapped the LaunchAgent's `ProgramArguments` in native `caffeinate -i` around `daily-orchestrator.sh`. Not sourced from this repo (its PowerProtect fix targets an unrelated display-mode/power-adapter bug and would add a third-party-app + sudo dependency this harness doesn't otherwise have) — but auditing it surfaced a real, previously-uncovered gap: the daily 18:00 unattended macOS run had no guard against idle/display sleep interrupting it mid-run. Zero-dependency fix using a built-in macOS binary.

**Rejected:** any skill/hook derived from the Amphetamine/PowerProtect repo itself — wrong tool for the gap (targets a display-mode/adapter bug, not general sleep prevention), and would introduce a paid third-party app + sudoers.d modification where a one-line native `caffeinate -i` already solves the actual orchestrator failure mode.

See also: [articles/README.md](../articles/README.md) — "The Shapes of Agent Memory" entry, extracted in the same batch.

---

## onecli/onecli
**URL:** https://github.com/onecli/onecli
**Added:** 2026-08-25
**Source / Author:** onecli — TypeScript/Rust pnpm-turbo monorepo, Apache-2.0 with an `ee/` enterprise carve-out, ~3,376★

**What it's about:** "Open-source sandboxed agent harness for teams" — one sandboxed agent per employee, provisioned off the corporate IdP, reachable via dashboard or Slack. Mature and heavily engineered (`apps/{gateway,runner,sandbox-supervisor,api-server,channel-adapter,web}`, ~70 Prisma migrations, live-gated conformance suites). Architecturally a *server product*, not a CLI installed alongside Claude Code — so nothing here is a dependency to adopt.

**What we added:**
- **Hook:** `hooks/claude/agent-commit-attribution-hook.sh` — PreToolUse(Bash), advisory. Warns when a `git commit` supplies an inline message with no `Co-Authored-By` trailer. Registered in `templates/settings.json.template` and the local `.claude/settings.json`; tests in `hooks/claude/agent-commit-attribution-hook.test.sh` (22 cases).

  What transferred is not the trailer format but onecli's chokepoint argument. Its gateway rewrites the `message` field on GitHub content/commit endpoints in-flight, appending `On-Behalf-Of: <agent>[onecli] (<workspace_id>)`, because App-installation-token commits have "no natural author identity" — attribution is injected where identity is known rather than requested of the agent. The local analogue: a `.git/hooks/commit-msg` hook sees a commit but not an author and can only nag on all commits or none, whereas a PreToolUse Bash hook knows Claude issued the command.

  The justification is a measured local defect, not tidiness. `skills/keep-rate/SKILL.md` selects agent commits with `git log --grep="Co-Authored-By: Claude"` and the keep-rate dashboard widget blames against the same set, so an untrailered agent commit leaves the denominator and the metric reads **high** — invisible, and biased flatteringly. Verified at time of extraction: only 10 of the last 30 commits in this repo carried the trailer; `.git/hooks/commit-msg` was a zero-byte file. Also ported: onecli's idempotence check and its explicit exclusion list (it skips merge endpoints because they use different append semantics — here that maps to `--amend --no-edit`, `--squash`, `--fixup`, `-C`/`-c` reuse forms, and editor-driven commits).

**Rejected:** onecli as an installed dependency — Postgres + Rust MITM gateway + Docker sandbox runner + Next.js control plane for *teams*; this harness is single-user and local; semantic action catalog (`provider/tool` → host/path/method policy targeting) — hollow without a proxy intercepting every egress request, and the design principles are >70% covered by `agent-permissions-design`; **vendor-neutral harness interface + conformance suite** — the most interesting artifact in the repo and still a skip: sdd-harness is single-vendor with no second adapter and no prospect of one, so writing it up is speculative architecture with zero call sites, which the repo's own Rule of Three forbids; capability-profile discipline ("declared up front, never probed") — a real principle with no enforcement point here; skill-loading-mechanic guidance — conditioned on the native Skill tool being *disabled*, which it is not, and the project CLAUDE.md's GitNexus table already lists literal skill-file paths; vault/60s-TTL credential injection — needs the gateway; the achievable local subset is already `protected-path-hook` + `scan-pii`; fail-closed policy resolution on partial load — a property of a stateful policy server, nothing here resolves cached policy over a network.

See also: [articles/README.md](../articles/README.md) — 14-source sweep batch note, and the Google ADK entry, which prompted the guard-normalization rewrite in the same session.

---

## dmmulroy/anti-slop
**URL:** https://github.com/dmmulroy/anti-slop
**Added:** 2026-08-26
**Source / Author:** dmmulroy — MIT, ~3.7k★, TypeScript

**What it's about:** "Opinionated Oxlint rules that reject low-evidence and low-signal TypeScript and JavaScript patterns." 15 generic rules plus a separately packaged Effect group, and an `install-anti-slop` agent skill that does the setup. Explicitly **vendored, not depended on** — you copy `src/` into the repo, register it under `jsPlugins` in `oxlint.config.ts`, and own it from then on. Representative rules: `no-chained-type-assertions` (blocks stacked casts), `no-unknown-parameters` / `no-unknown-returns` / `no-unknown-type-aliases` (keeps `unknown` out of contracts), `no-unsafe-dictionary-type` (rejects `Record<string, unknown>`), `no-known-value-widening` / `no-widen-then-assert` (prefer `satisfies`), `require-safety-comment-for-type-assertion` (every non-const cast needs a `// SAFETY:` note), `no-runtime-typeof` (`allowInTypeGuards`, default off), `no-module-mocking`.

**What we added:**
- **Hook:** `hooks/claude/js-quality-gate-hook.sh` — `PostToolUse` on `Write|Edit|MultiEdit`, advisory. The sibling of `ruff-quality-gate-hook.sh` for the other half of the languages this harness installs into. Selects `.ts/.tsx/.mts/.cts/.js/.jsx/.mjs/.cjs`; skips `.d.ts` and `node_modules/`, `dist/`, `build/`, `.next/`, `coverage/`, `vendor/`; prefers `oxlint`, falls back to `eslint`, silent no-op when neither exists; suppresses the linters' own "0 problems" summary so a clean write prints nothing. Adds an extra callout when a finding names an anti-slop rule, because those mean type evidence was discarded rather than style drifting — recover the real type, never silence the rule. Registered in `templates/settings.json.template`, `templates/settings.harness.json.template`, and this repo's `.claude/settings.json`. Tests: `hooks/claude/js-quality-gate-hook.test.sh`, 21 cases, stubbing the linter on `PATH` so the suite passes on a machine with no JS toolchain.

  The gap was measurable, not aesthetic: `CLAUDE.md`'s Quality Gates section had `ruff check` firing on every `.py` write and **nothing at all** firing on a `.ts`/`.js` write, so agent-written TypeScript reached the human unlinted in every project the harness ships to. Complexity linting (which `guardrails-agent` already covered) and type-evidence linting are independent axes, and agent-written TS fails the second far more often — a cast or `unknown` is the shortest path to a quiet compiler.

- **Augmentation:** `agents/kiro/guardrails-agent.md` — a "JS/TS (type evidence)" audit dimension scored separately from complexity (a project can be fully compliant on complexity and score zero here), an `oxlint.config.*` / `.oxlintrc.json` row in the config-discovery table, a Type-Evidence Rules line in the audit output, and a scaffold path that **recommends rather than vendors**: it surfaces `npx skills add dmmulroy/anti-slop --skill install-anti-slop` and what that will change, honoring the agent's existing "Don't install packages" constraint. Two rules are flagged as judgement calls to name when proposing the set — `require-safety-comment-for-type-assertion` (high friction, high value) and `no-runtime-typeof` (needs `allowInTypeGuards` on for codebases with real type guards).

  Nothing was copied from the repo. The 15 rule implementations were never fetched and are not reproduced here — writing them from README prose would have been fabrication. What transferred is the taxonomy and the enforcement point; the rules stay upstream where they are maintained, vendored per-repo by whoever owns that repo.

**Rejected:** anti-slop as a harness dependency — this repo is bash + markdown with zero TS/JS surface, and the rules are designed to be edited per-team, so pinning them centrally fights their own distribution model. Duplicating the upstream `install-anti-slop` skill into `skills/` — it already exists and is one command away; a local copy would be a stale fork within a release cycle.

---

## M4F-S/mnemosyne
**URL:** https://github.com/M4F-S/mnemosyne
**Added:** 2026-08-26
**Source / Author:** `mnemosyne-memory` — Apache-2.0, Python, v3.4.0, ~25★

**What it's about:** "Production-grade, local-first hierarchical memory engine for autonomous AI agents." Three storage layers: an Obsidian markdown vault (YAML frontmatter + `[[wikilinks]]`), Postgres + pgvector (HNSW, `vector_cosine_ops`, m=16, ef_construction=64, plus GIN full-text search) with a full-parity SQLite fallback, and optional MD5-checksummed Google Drive sync. Wing/Room two-level scoping so a marketing agent's recall never surfaces pentest findings. Hybrid RRF retrieval fusing dense vectors (1.0), keyword FTS (0.8), recursive-CTE graph traversal at 1 and 2 hops (0.6), and salience (0.2). Ebbinghaus decay at ×0.95/day, archiving below 0.05 salience after 90 idle days; `pinned=True` locks salience at 1.0 permanently. MCP server over stdio exposing 8 tools. Ingest-time credential regex screening and control-token neutralization outside code blocks.

**What we added:** nothing.

**Rejected:** the whole engine. `.claude/memory/` plus the `memory-systems` / `agent-memory-consolidation` / `agent-memory-discipline` / `agent-memory-mcp` cluster, `memory-discipline-hook.sh`, `housekeeping-agent`, and `memory-first-lookup` already cover roughly 85% of this, and the remainder is infrastructure — Postgres, pgvector, an embedding service, a sync daemon — for a single-user local harness that has none of it and needs none of it. Ebbinghaus decay was the one genuinely novel mechanism and is still a skip: `housekeeping-agent` already archives on staleness, so a salience float would be new arithmetic guarding a failure nobody has observed here. Wing/Room scoping maps onto per-repo `.claude/memory/` directories, which is isolation this harness gets for free. Injection armor and credential screening are real, but `scan-pii.sh` and `protected-path-hook.sh` hold the equivalent ground on the write path.

---

## xhluca/session-migrate
**URL:** https://github.com/xhluca/session-migrate
**Added:** 2026-08-26
**Source / Author:** xhluca — MIT, Python 3.11+, Linux

**What it's about:** A CLI for moving coding-agent conversations between harnesses — "Migrate your sessions to any harness." Supports Claude Code, Codex CLI, Pi, Oh My Pi, OpenCode, Copilot CLI, Antigravity CLI, Mistral Vibe, Muse Code, Qwen Code, Kimi Code, and Cursor Agent (experimental, version-pinned, text-only): 144 ordered routes. Pipeline is `native session → validated event timeline → native target → resume`, emitting only structures verified against the destination CLI and leaving the source untouched. Messages preserved in full; tool calls, images, and compaction summaries carried when the target has an equivalent native shape; session name/ID/picker entry recreated; reasoning traces, auth, hooks, policies, MCP and runtime config deliberately dropped. Every transformation is tallied in a "content-free migration manifest."

**What we added:** nothing.

**Rejected:** the tool and any wrapper around it. It solves cross-CLI portability; this harness runs Claude Code, and `save-session` / `resume-session` plus `.claude/memory/` already cover the continuity actually in use. Adopting it means a Linux-only Python tool (per its own README) for a migration with no evidence of demand. The one idea worth remembering is documentary, not portable: its explicit **drop list** — hooks, policies, MCP, auth stay with the original client — is a clean statement that the harness *is* the non-transferable part of a session, which is the same conclusion the "Intelligence EXPLOSION" video reaches from the opposite direction.

See also: [articles/README.md](../articles/README.md) and [x/README.md](../x/README.md) — same 13-source sweep, 2026-08-26.

---

## dzhng/skills — "AI skills for building software factories"
**URL:** https://github.com/dzhng/skills
**Added:** 2026-08-30
**Source / Author:** dzhng — MIT, 21 skills, ~766★, last push 2026-08-25

**What it's about:** A harness-agnostic skill pack (Claude Code / Codex / opencode / Cursor) encoding one opinionated unattended-agent loop: `/explore-unknowns` → `/write-spec` → `/implement-spec` → review. Its thesis is that the hard part of delegation is not decomposing a goal into tasks but splitting it into *independently verifiable* slices, and that the human review surface should be the agent's **decisions**, not its diff. 95% prose-in-markdown; only the visual skills ship executable code.

**What we added:**
- Skill: `auditing-spec-choices` — the choices-ledger pattern. Reconstructs decisions the implementer made where the spec was **silent**, verdicts each `sound`/`unsound`/`needs-user`, and requires a *reversible provisional call* for every `needs-user` so an unattended run never stalls. Fills a real hole: the harness reviews artifacts (`validate-impl`, `validate-adversarial`) and conduct (`session-quality`, `.claude/behaviors/`) but nothing reviewed decisions invented because the spec was underspecified. A diff shows what was built and says nothing about what was silently discarded.
- Command: `/kiro:audit-choices <feature> [--close]` — runs the audit per pass; `--close` consolidates at spec close.
- Augmentation: `commands/kiro/spec-impl.md` — **Decision-Budget Gate** as a fourth Phase -1 check ("does every task leave the implementer inheriting decisions rather than making them?"). The existing three gates all catch *too much*; this one catches *too little*. Plus a Phase +1 step invoking the audit after each pass.
- Augmentation: `skills/dispatching-parallel-agents` — fan-out **for planning** as a distinct variant from fan-out for partitioned work. Undifferentiated drafters converge on correlated blind spots, so divergence must be engineered: assign each an orthogonal bias (fewest-slices / risk-first / seam-quality), keep them blind to each other, diversify by *vendor family* not model tier, and have the orchestrator synthesize rather than anoint a winner.
- Augmentation: `skills/goal-mode` — non-blocking checkpoint protocol (open evidence, bounded ~5min wait, on silence decide on evidence and record how to reverse it, continue). Resolves the tension between "don't stop to ask" and decisions that genuinely want a human.

**Rejected:** the `write-spec`/`implement-spec`/`close-spec` commands wholesale — sdd-harness *is* a spec-driven harness with a 12-command `kiro:spec-*` pipeline, and a parallel workflow creates two competing sources of truth; mine the mechanics, not the commands. `explore-unknowns` (covered by `surfacing-unknowns`), `write-skills` (covered four times over by `skill-creator`/`skill-curator`/`skill-developer`/`writing-skills`), `eval-skills` (`skill-eval-gate`), `refactor-clean` (installed twice already), `compare-screenshots` + its pixelmatch script (hardwired to a `web/` monorepo layout the harness does not have), `renderer`/`marketing-pages`/`eli5` (domain-narrow or hollow). 17 of 21 skills were redundant against the installed ~999.

See also: [articles/README.md](../articles/README.md) and [x/README.md](../x/README.md) — same 8-source sweep, 2026-08-30.

---

## kelviq/tare — Claude Code token-usage forensics
**URL:** https://github.com/kelviq/tare
**Added:** 2026-08-30
**Source / Author:** kelviq — MIT, Python (stdlib only), ~141★, created 2026-08-12

**What it's about:** A skill plus three scripts that parse `~/.claude/projects/**/*.jsonl` to answer "where did my tokens go and why did I hit the 5-hour limit." Ships a diagnosis workflow rather than a dashboard: the scripts produce numbers, the agent produces the cause. **The newsletter that surfaced it ("7 companies got hacked by a tricked AI") misfiled it — there is nothing security-related here.** Key mechanisms: `requestId` dedup, context amplification (`tokens × requests_remaining`, ranking tools by what they *caused* rather than what they returned), a rolling 5h two-pointer window, and automation fingerprinting via sweep-line peak concurrency.

**What we added:**
- Bug fix: `scripts/utils/dashboard.py` `_parse_session_file` now dedups on `requestId`/`message.id` before summing. One API response is written to the transcript as one JSONL line **per content block**, each repeating the identical `message.usage`; the parser summed line-by-line. Measured on this machine's own logs: naive 1,107,857,347 → dedup 607,706,742 across 200 transcripts — the usage tab and every derived USD figure were overstating spend by **~82%**. This was a defect in shipped code that the tool merely exposed.
- Script: `scripts/utils/dashboard-usage-dedup.test.sh` — 8 synthetic cases plus a format-drift canary that **fails on zero duplicates found in real transcripts**, since that would mean the transcript format changed and the dedup had silently become a no-op. Each session record now carries a `collapsed` count for the same reason.

- Script: `scripts/utils/token-forensics.py` + skill `auditing-token-spend` — the measurement layer the harness lacked. Deduplicated totals, **amplified per-tool cost** (chars returned × requests that followed it, ranking what a tool *caused* rather than what it *returned*), peak rolling 5h window, session-shape automation fingerprint, and an automation split. Every existing token-related skill (`context-optimization`, `context-window-management`, `rtk-token-reduction`, `cost-optimization`) is prescriptive advice about reducing context; none measured actual spend, so none could say which advice would pay.
- Routine: Phase 3 of the bi-weekly harness-health routine — the runner executes the script and substitutes its output into the prompt, so a headless session cannot silently skip it and report on nothing. Reports only anomalies; a stable profile is one line.

**Correction to tare's own approach:** tare reaches for an OTLP collector (`otel_sink.py`, a daemon plus six env vars) to separate subagent spend. `isSidechain` in the transcript looked like a free replacement — **it is not**. Measured here: present on every assistant line, `True` on none, 0 of 6,423 across 150 transcripts in sessions that provably spawned agents. Each agent appears to get its own session file, indistinguishable from a short human session. The script therefore reports a clearly-labelled short-session **proxy** and switches to the exact split automatically if the field is ever populated. It never prints the unpopulated field as `0%` — unknown and zero are different findings, and only one of them is true.

**Rejected:** `ccwatch.py` — walks `~/.claude/.credentials.json` and the macOS Keychain for an OAuth token, then polls the undocumented `https://claude.ai/api/oauth/usage`. Credential-harvesting shape against an unsupported endpoint; not worth it to confirm phantom usage. The `/tare` command, HTML report, and `--share` export (the dashboard already has a usage tab). `MODEL_RATES` (the dashboard already prices models). `otel_sink.py` — superseded by the proxy above for the harness's purposes, and a daemon plus six env vars is a large standing cost for an attribution that a labelled proxy approximates.

See also: [articles/README.md](../articles/README.md) and [x/README.md](../x/README.md) — same 8-source sweep, 2026-08-30.

---

## DietrichGebert/ponytail — "lazy senior dev mode"
**URL:** https://github.com/DietrichGebert/ponytail
**Added:** 2026-08-30
**Source / Author:** DietrichGebert — MIT, JS + Markdown + a Python benchmark harness, npm `@dietrichgebert/ponytail` v4.9.0

**What it's about:** A cross-agent ruleset pushing coding agents toward minimalism via a 7-rung decision ladder (YAGNI → reuse existing → stdlib → native platform → installed dep → one line → minimum that works), shipped as always-on injected instructions with adapters for ~20 agents. Star count (~117k in 79 days on a Markdown ruleset repo) is meme velocity, not engineering signal — and the README itself retracts its original "80–94% less code" headline as a conversational-baseline artifact, restating it as −54%.

**What we added:**
- Hook: `hooks/claude/subagent-context-hook.sh` (`SubagentStart`) — injects harness conventions directly into every spawned subagent via JSON `hookSpecificOutput.additionalContext`. **This falsified a stated assumption baked into the harness**: `gbrain-agent-spawn.sh` asserted in a comment (and `docs/hooks/README.md` repeated) that hooks cannot inject content into a subagent's context. That was true of `PreToolUse:Agent`, which is all that existed when it was written. Verified against Claude Code 2.1.221 with a probe subagent that read the injected block back verbatim, `agent_type` included. Two constraints inherited from ponytail's scar tissue: `SubagentStart` is **not** a stdout-as-context event (only `UserPromptSubmit`/`UserPromptExpansion`/`SessionStart`/`PostModelSwitch` are), so the `cat << 'RULES'` pattern used by every other hook here would be silently discarded; and the stdin read must be bounded or a hung read stalls the spawn itself (their issue #443). Both are covered by tests, including a FIFO stall case.
- Augmentation: `skills/skill-eval-gate` — judge self-validation. Before trusting an LLM rubric, make it rank a deliberately-bad reference strictly above a good one on the same task; if it cannot, discard the run. Plus a paired **completeness** judgement, because a skill can win any "is this lean" rubric by doing less, and persisting raw runs so a rubric tweak re-scores offline instead of re-generating (which changes two variables at once).
- Correction: `hooks/claude/gbrain-agent-spawn.sh` — stale model IDs (`claude-sonnet-4-6`, `claude-opus-4-7`) updated to `claude-sonnet-5` / `claude-opus-5`, and the falsified comment replaced with a division-of-labour note.

**Rejected:** the minimalism doctrine itself — `CLAUDE.md`'s AI-Legible Code section already runs it harder (blast radius ≤1 folder, Rule of Three, vertical slices, no `shared/`/`utils/`/`common/`), and `progressive-complexity-ladder` plus `code-review-excellence` are installed; a fourth anti-over-engineering voice is context tax. `/ponytail-review`, `/ponytail-audit` (covered by `code-review-excellence`, `architect-review`, `codex-review`). The mode-flag + statusline pattern (`caveman-savings-hook.sh` already does exactly this). The `ponytail:` debt marker (one grep and a format string; the harness already has `DEBT:` markers). `/ponytail-gain` as a dashboard widget — it renders the vendor's own benchmark medians, and its own docs admit it can never produce a real per-repo figure. The 20-agent portability layer and `ponytail-mcp` (Claude Code only here; four MCP servers already installed).

See also: [articles/README.md](../articles/README.md) and [x/README.md](../x/README.md) — same 8-source sweep, 2026-08-30.

---

## visa/visa-vulnerability-agentic-harness (VVAH)
**URL:** https://github.com/visa/visa-vulnerability-agentic-harness
**Added:** 2026-09-03
**Source / Author:** Visa — Apache 2.0, v1.2.0, ~2.7k stars

**What it's about:** An open-source agentic SAST pipeline for automated vulnerability discovery, fixing and fix-checking with frontier models, built on lessons from Anthropic's "Project Glasswing". Four phases, eleven stages (S1–S11) plus an optional Stage 0 static seed, each LLM stage a swappable skill: attack-surface mapper, STRIDE threat modeler, research strategist, specialist lenses (crypto, logic-bug, access-control, IaC), adversarial reviewer, deduplicator, exploit strategist, remediation playbooks keyed to CWE–language–framework, and an adversarial validation panel. Headline metric is Mean Time to Adapt; the README argues triage speed, not discovery, is the real constraint. **No precision/recall numbers are published** — the README says so plainly rather than implying zero.

**What we added:** one mechanism, which turned out to be the highest-value item in the whole 7-source batch.
- Augmentation (shared with the articles batch): VVAH's **majority-vote false-positive filtering** — run a finding N times at temperature > 0 and require it to survive a threshold count — is the same claim Perrone makes as `pass^k`. A repo-wide grep for `pass@k`, `pass^k`, `mutation test`, `majority vote` and `self-consistency` across `hooks/`, `agents/`, `scripts/` and `commands/` returned **zero** hits; the only match anywhere was a vendored third-party skill. Two harness components were taking exactly one sample and treating it as fact: `skill-eval-gate` (one baseline + one treatment run per scenario) and `session-judge` (spawned once daily, its `score_delta` applied straight into a cumulative score nothing ever revisits). Both now sample and reconcile — see [articles/README.md](../articles/README.md) for the full file list.

VVAH's own README is what made the mechanism concrete rather than aspirational: it notes the `cli` backend has **no temperature control** and is therefore single-pass, i.e. unfiltered — the same structural position the harness's judge was in.

**Rejected:** SARIF 2.1.0 output (over-engineering for a solo harness with no security-tooling pipeline to feed). The MTTA metric (presumes an org and a production deploy path). The read-only, idempotent validation panel that never patches — already how the harness works: `validate-adversarial`, `validate-impl`, `validate-perf` and `session-judge` all declare `tools: Read, Grep, Glob` and cannot write code. `validate-adversarial`'s three passes were checked for overlap and are a *different axis* — three reasoning stances (assess / refute / synthesize) inside one sampling run, not N samples — so no `better-call` comparison was warranted.

**Note, not an integration:** VVAH is runnable against this repo. The `taint.yaml` profile is login-only with S10/S11 off, so it needs no `ANTHROPIC_API_KEY` — which matters, since none exists here (`harness-llm-calls-use-subscription`). Two caveats: structured taint evidence exists only for Python, Java and C#, and this harness is mostly bash, so most of it would fall back to reachability seed paths; and a bare `scan` **edits source files in the target repo** at stage S10, making `--stop-after s9` mandatory for detection-only use.

See also: [articles/README.md](../articles/README.md) — the other six sources in the same 2026-09-03 batch.

---

## humanlayer/skills and huggingface/funes — both surfaced by the 2026-09-06 batch, both rejected
**URLs:**
- https://github.com/humanlayer/skills — `plugins/show-me/skills/show-me/SKILL.md` (dexhorthy)
- https://github.com/huggingface/funes — single-binary local memory layer for coding agents

**Added:** 2026-09-06
**Source / Author:** Neither repo was an input URL. Both were reached *through* the 2026-09-06 six-source batch — `humanlayer/skills` from Matt Pocock's X post, `funes` from its Hugging Face blog announcement. Logged here anyway so the rejections are findable in the index where someone would go looking for them.

**Retrieval, stated honestly:** `humanlayer/skills` was fetched at the raw-file URL, but **WebFetch declined to reproduce the SKILL.md verbatim** and returned a structural description instead — so the evaluation below rests on a summary of the file, not the file. The `funes` repo itself was **never fetched**; everything known about it comes from the author's blog post. Neither rejection should be read as a code review.

**What they're about:**
- **`humanlayer/skills` → `show-me`** — a skill that picks a visual format to explain code: pseudocode for algorithms, call trees for runtime flow, component trees for UI, shallow file trees for refactors, Mermaid for interaction and data flow, `diff` blocks for changes against existing shape, whole blocks for mostly-new code, and a single focused HTML file for comparisons too dense for Mermaid. Its governing rule is "Pick the smallest view that makes the key point clear." One command: `open path/to/show-me-{description}.html`.
- **`huggingface/funes`** — parses Claude Code, Codex, pi and Hermes traces into a shared turn-and-block shape, chunks and embeds them with a pinned local model into a Lance dataset, and answers queries by fusing vector and BM25 rankings, reranking with a cross-encoder, reweighting by recency and attaching neighbouring chunks. `recall` "returns the original text, not a summary," with provenance; "Nothing is distilled into a fact at write time." Optionally binds to a Hugging Face dataset the user owns, so memory travels between machines without becoming an account in someone else's service.

**What we added:** nothing from either repo.

**Rejected — `show-me`.** Four-way overlap with `diff-teach`, `mermaid-expert`, `artifact-diagramming` and `gitnexus-exploring`, in the saturated software-engineering domain (SkillsBench: 4.5pt lift there vs 51.9pt in model-weak domains). The right move if it is ever wanted is to install `humanlayer/skills` as an upstream plugin rather than vendor a copy — that keeps maintenance at zero and avoids a fifth artifact in a crowded corner. Recorded rather than dropped because the recommendation came from a high-signal source and will surface again.

**Rejected — `funes`.** The tool is well-argued and its handoff-vs-recall benchmark is pointed: recall was **8x cheaper than a written handoff on one task and 4x on the other**, and compaction was the only channel that split — passing one task and failing the other, where "its summary had flattened the findings that mattered." It was rejected on fit, not quality. This harness already runs two memory stores that do not see each other (`.claude/memory/` and `~/.claude/projects/<slug>/memory/`), plus `ctx_knowledge` and headroom; a third store makes a known fragmentation problem worse. The companion proposal — rewriting `/kiro:save-session`'s written handoff into transcript recall — was rejected on evidence strength: n=2 tasks, author-run, on the author's own tool. What did survive is the dependency the benchmark exposes: recall over traces is impossible if the traces are deleted, and Claude Code's `cleanupPeriodDays` default of 30 days was silently doing exactly that. That setting is now pinned at 365 — see [articles/README.md](../articles/README.md).

See also: [articles/README.md](../articles/README.md) and [x/README.md](../x/README.md) — same 2026-09-06 six-source batch.

---

## Three repositories from the 2026-09-09 eleven-source batch — all rejected
**URLs:**
- https://github.com/llm-as-a-verifier/llm-as-a-verifier
- https://github.com/humanlayer/skills/blob/main/plugins/show-me/skills/show-me/SKILL.md
- https://github.com/langgenius/dify

**Added:** 2026-09-09

**Retrieval:** All three via `ctx_url_read` rung 1. `show-me`'s SKILL.md came back **verbatim this time**, which matters: the 2026-09-06 rejection above rests on a structural description, and this one does not.

**What they're about:**
- **`llm-as-a-verifier`** — a general verification framework for agent output. Scores with fine-grained granularity, takes an expectation over the full logprob distribution of the verifier's score tokens, and scales through repeated evaluation and criteria decomposition. Ships best-of-N selection via a Probabilistic Pivot Tournament that ranks N trajectories in O(Nk) pairwise verifications instead of a full O(N²) round-robin, plus a `ProgressTracker` that scores an agent step by step while it runs so a hopeless rollout can be abandoned early. Reports Terminal-Bench V2 83.1% → 86.5% against a 92.1% oracle, SWE-Bench Verified 76.1% → 78.2% against 84.4%. A companion Claude Code plugin, TurboAgent, sits between the client and the model provider as a drop-in API proxy.
- **`humanlayer/skills` → `show-me`** — see the entry above; unchanged on a second read.
- **`langgenius/dify`** — 155k-star open-source platform for building LLM apps: visual workflow canvas, RAG pipeline, agent definitions over Function Calling or ReAct with 50+ built-in tools, model management across hundreds of providers, LLMOps, and a backend-as-a-service API layer.

**What we added:** nothing from any of the three.

**Rejected — `llm-as-a-verifier`.** The mechanism does not port. Scoring by expectation over a logprob distribution requires logprobs, and every harness LLM call goes through `claude --print` on a subscription, which returns none. What is left after removing that is repeated evaluation with criteria decomposition, which `skill-eval-gate` (`pass^3`) and `session-judge` (median of 3) already do. The pivot tournament solves ranking at N > 2; `better-call` compares exactly two candidates with an order flip, so it has no N to reduce. TurboAgent generates N candidate responses per turn, multiplying subscription spend in a harness where token cost is already tracked as a problem. The one uncovered idea, live progress scoring for early abandonment, would here reduce to a coarse single-shot LLM judgement made repeatedly — the failure mode already recorded as a durable memory.

**Rejected — `show-me`, a second time and now on the file itself.** The four-way overlap holds: `diff-teach`, `mermaid-expert`, `artifact-diagramming`, `gitnexus-exploring`. Reading the real text sharpened one point rather than changing the verdict — its strongest idea is diff-shaped explanation, showing a change against the shape that already exists, and that is exactly `diff-teach`'s ground, where the predict-then-reveal drill does more work than a one-way explanation. Install upstream as a plugin if it is ever wanted.

**Rejected — `dify`.** A product, not a technique. Nothing in it is portable to a shell-and-markdown harness. One incidental observation kept for provenance: it ships `.agents/skills` and `.claude` alongside both `CLAUDE.md` and `AGENTS.md`, so the dual-standard skills directory is spreading beyond agent-tooling repos.

See also: [articles/README.md](../articles/README.md) — the full eleven-source batch, including the two integrations it produced. [x/README.md](../x/README.md) — the three X-archive mirrors from the same batch.

---

## github.com/Atomburstofficial/geiger
**URL:** https://github.com/Atomburstofficial/geiger | **Added:** 2026-09-16

**What it's about:** CLI that scans a repo for AI-generated-code fingerprints ("geiger counter" for slop) and emits a strict JSON diff report, runnable via `npx --yes geiger-scan`.

**What we added:**
- Already covered before this source was logged — `skills/ai-surface-audit/SKILL.md` (pre-existing, uncommitted WIP from an earlier session) already wraps `npx --yes geiger-scan --strict --json` and writes `.claude/memory/.ai-surface-scan.json`, wired into `commands/kiro/daily-maintenance.md` Step 6c ("AI-Surface Drift Check"). No new work — logging for attribution.

---

## github.com/shadcn-ui/lint
**URL:** https://github.com/shadcn-ui/lint | **Added:** 2026-09-16

**What it's about:** shadcn-ui's internal ESLint ruleset enforcing Tailwind design-token discipline — no raw hex/rgb colors in `className`, no arbitrary-value syntax (`w-[13px]`), static (non-interpolated) class names so the JIT scanner can see them.

**What we added:**
- Agent: `agents/kiro/guardrails-agent.md` — Step 0 Tailwind sub-check (detected via `tailwind.config.*` or `tailwindcss` in `package.json`), Step 2 new rule table (`no-raw-colors`, `no-arbitrary-values`, `require-static-classes`) with MINIMAL/FULL enforcement tiers, audit report block, scaffold action step.
- Hook: `hooks/claude/js-quality-gate-hook.sh` — `tailwind_design_token_check()`, regex-only MINIMAL-tier enforcement of the same 3 rules, gated on Tailwind being detected in the repo.
- Doc: `kiro/settings/rules/deterministic-enforcement.md` — rationale + `eslint-plugin-tailwindcss` FULL-tier upgrade path.

---

## github.com/anthropics/claude-code (issue #91870 — native Function Hooks)
**URL:** https://github.com/anthropics/claude-code/issues/91870 | **Added:** 2026-09-16

**What it's about:** Open feature request for first-class function-level hooks in Claude Code, as an alternative to the current shell-script PreToolUse/PostToolUse pattern.

**What we added:**
- Tracking only, not built — feature is unshipped. Logged in `skills/claude-code-guide/SKILL.md`'s new "Watch Items" section and `.claude/memory/hot-memory.md` System Notes. Re-evaluate whether `hooks/claude/*.sh` scripts should migrate if/when it ships.

---

## github.com/cathrynlavery/diagram-design
**URL:** https://github.com/cathrynlavery/diagram-design | **Added:** 2026-09-17

**What it is:** A workflow for generating branded, editorial-quality diagrams (HTML+SVG, styled from a target site's colors/fonts) rather than generic boxes-and-arrows.

**What we added:**
- Skill: `skills/diagram-design/SKILL.md` — 5-step workflow for producing publication/deck-ready diagrams, explicitly distinguished from `mermaid-expert` (syntax-only, not brand-styled). Includes an "Anti-Pattern: Mermaid Slop" section naming when the generic-diagram default is the wrong choice.

---

## github.com/alibaba/open-code-review
**URL:** https://github.com/alibaba/open-code-review | **Added:** 2026-09-17

**What it is:** A PR review architecture that splits mechanical work (file bundling, per-file-type rule matching, comment-anchor verification) into a deterministic layer with no model call, reserving the model call for judgment that can't be reduced to a rule — reported ~9x token efficiency versus a review agent doing everything through the model.

**What we added:**
- Skill enhancement: `skills/gitnexus-pr-review/SKILL.md` — new "Hybrid Deterministic + Agent Review Pattern" section, complementary to GitNexus's existing call-graph/impact-analysis split. Notes that this harness's `validate_review_json.py` anchor check already implements the deterministic comment-anchor-verification piece. The 9x token-efficiency figure is cited as the source's own claim, not a verified number for this harness — flagged as such in the skill text, along with the recommendation to benchmark any adoption on precision/recall/token-cost rather than pass/fail.

---

## github.com/langchain-ai/deepagents — examples/better-harness
**URL:** https://github.com/langchain-ai/deepagents/tree/main/examples/better-harness | **Added:** 2026-09-24

**What it is:** An example where one Deep Agent (the "outer agent") automatically improves another agent's harness (the "inner agent") by editing whitelisted surfaces (prompt text, tool/skill files, middleware code + its wiring) in an isolated proposer workspace, then testing the change against `train` and `holdout` eval splits and keeping it only if combined pass counts improve. The outer agent only ever sees visible train failures and prior keep/discard history — never the holdout set.
See also: [articles/README.md](../articles/README.md) (RRSI names the same overfitting risk this train/holdout split guards against).

**What we added:**
- Skill enhancement: `skills/skill-eval-gate/SKILL.md` — new Phase 1d, "Reserve a Holdout Scenario" (same augmentation logged under the RRSI entry in articles/README.md — this repo's train/holdout split was the concrete implementation pattern that made the abstract RRSI concern actionable as a gate rule).

---

## github.com/alphaXiv/OpenResearch
**URL:** https://github.com/alphaXiv/OpenResearch | **Added:** 2026-09-30

**What it is:** A local-first Rust workspace (`orx`) that wraps coding agents for autoresearch. Each research line runs in its own worktree, experiments form a git branch tree with an immutable commit per run, results go to SQLite. Its skill file sets four rules, including that a node is frozen once a run has answered it and a crashed run that answered nothing is repaired rather than counted.

**What we added:**
- Augmentation: `agents/kiro/autoresearch-agent.md` — a REPAIR outcome. A crash caused by the change's own bug (typo, import error, shape mismatch) is fixed and re-run under the same hypothesis and logged ERRORED; ERRORED runs no longer count toward the "reverted 2+ times, pivot" rule; two errors after repair revert and move on. New constraint: a crash is not a result, a disappointing result is — don't re-tweak a hypothesis whose run completed and lost. Before this a crash was logged like a failed idea and could trigger a pivot, discarding hypotheses that were never tested.

**Rejected:** installing `orx` (default-on telemetry, unauthenticated remote mode, overlaps `/kiro:autoresearch`), the worktree-per-branch experiment tree (too heavy for the single-file keep/revert loop).

See also: [articles/README.md](../articles/README.md) — same 18-source batch, 2026-09-30. Three other repos in the batch (`rohitg00/ai-engineering-from-scratch`, `wbopan/tastebench`, `dzhng/jevgrep`) yielded nothing; see `docs/skills/skill-extraction/extraction-history.md`. `dzhng/skills` is logged separately above.

---

## github.com/earendil-works/pi
**URL:** https://github.com/earendil-works/pi | **Added:** 2026-10-01

**What it is:** A TypeScript monorepo for a coding agent that can extend itself. It contains a unified LLM API (`pi-ai`), an agent runtime, a TUI and durable/telemetry packages. Pi has no permission system and leaves sandboxing to Gondolin micro-VMs, Docker or OpenShell. The README documents its supply-chain hardening: exact pins, `.npmrc` `save-exact=true` plus `min-release-age=2`, a pre-commit hook that blocks lockfile changes, a shipped shrinkwrap and `npm ci --ignore-scripts`. Only the README was read; pi.dev/docs was not.

**What we added:**
- Script lib: `scripts/lib/pip-cooldown.sh` (+ `pip-cooldown.test.sh`, 9 cases) — pi's release-age cooldown, applied to pip and uv. Every harness-driven Python install (`check-harness-deps.sh`, `liteparse-setup.sh` via `lib/venv-tools.sh`, and `repo_pip_install`'s pip and uv paths in `lib/repo-venv.sh`) now skips packages uploaded within `SDD_PIP_MIN_AGE` (default `P2D`; `off` disables it). Before this, unpinned `raindrop-ai`/`liteparse` resolved to whatever was published that day. A pip without `--uploaded-prior-to` installs without the cooldown and warns.
- Augmentation: `install.sh` — `impeccable` pinned to `3.6.0` (`IMPECCABLE_VERSION`). npm 10.9.8 has no `min-release-age`, so a pin is the only guard available.

**Rejected:** lockfile pre-commit hook (no lockfile here), shrinkwrap and `--ignore-scripts` (the harness publishes no packages), sandbox options (Claude Code already has sandbox and permission modes), `pi-share-hf`, auto-closing new contributors' PRs, the RFC process.

---

## github.com/composio-community/open-dot
**URL:** https://github.com/composio-community/open-dot | **Added:** 2026-10-01

**What it is:** An open-source Mac copy of OpenAI's "Dots": background agents, each with its own Chrome profile, a Keychain-encrypted password vault the model never sees, Composio app connections, voice, routines and triggers. Before each risky action, a small review model checks it against approval rules written in plain language, and the user approves or denies it on a card.

**What we added:** nothing. The approval-rule review model overlaps Claude Code's auto-mode classifier and PreToolUse hooks, and a `claude --print` call on every tool call would add a lot of latency. The vault, browser profiles and voice don't apply to a coding harness. Routines and triggers are already covered by `daily-orchestrator.sh`.

See also: [x/README.md](../x/README.md) — same 4-source batch, 2026-10-01 (@tvykruta thread). The batch's YouTube episode (How I AI, Jev decision model) yielded nothing; see `docs/skills/skill-extraction/extraction-history.md`.
