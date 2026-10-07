# Papers

Scientific papers (primarily arXiv) that informed skills or methodology in this harness. Ordered by date added.

---

## Don't Do RAG: When Cache-Augmented Generation is All You Need
**arXiv:** https://arxiv.org/abs/2412.15605 | **Year:** 2024 | **Authors:** Brian J Chan, Chao-Ting Chen, Jui-Hung Cheng, Hen-Hsen Huang

**What it's about:** Proposes Cache-Augmented Generation (CAG) as an alternative to RAG for bounded knowledge bases. Rather than retrieving documents at query time, all relevant knowledge is preloaded into the LLM's extended context once and the resulting KV cache state is persisted to disk. Subsequent queries reuse the cached state without re-encoding the knowledge — eliminating retrieval latency, retrieval failure modes, and chunking complexity.

**What we added:**
- Skill: `cag-implementation` — CAG vs RAG decision matrix, three-phase implementation pattern (preload → persist → query loop), HuggingFace `past_key_values` implementation. See also: [git/README.md](../git/README.md) for the reference implementation repo.

---

## SkillOS: Eliciting and Organizing Skills for Autonomous LLM Agents
**arXiv:** https://arxiv.org/abs/2605.06614 | **Year:** 2026
**Added:** 2026-05-12

**What it's about:** Introduces SkillOS — a framework for autonomous skill management in LLM agents covering skill elicitation, organization, and quality curation. Key finding: high-quality skill curation (not just accumulation) is the primary bottleneck for self-evolving agents. Defines 4 quality dimensions for skills: task relevance (real repeated use), operational validity (executable steps), content quality (structured phases), and compression (≤5000 words / ≤200-char description). Proposes MERGE, COMPRESS, DELETE as the three maintenance operations.

**What we added:**
- Skill: `skill-curator` — automated skill library curation using the 4 SkillOS quality dimensions. Implements MERGE (consolidate overlapping skills), COMPRESS (trim to ≤5000 words), DELETE (remove duplicates/stale skills). 6-phase workflow with mandatory human approval before any execution. Weekly CCR routine registered (Mondays 9am IDT) to prevent library bloat as the skill collection grows.

---

## Automating Skill Acquisition through Large-Scale Mining of Open-Source Agentic Repositories
**arXiv:** https://arxiv.org/abs/2603.11808 | **Year:** 2026 | **Authors:** Shuzhen Bi, Mengsong Wu, Hao Hao, Keqian Li, Wentao Liu, Siyu Song, Hongbo Zhao, Aimin Zhou

**What it's about:** Framework for automatically extracting procedural skills from open-source agentic repositories and encoding them into standardized formats that augment LLM capabilities without retraining. Shows that procedural knowledge mined from repos can achieve 40% gains in knowledge transfer efficiency compared to conventional methods, enabling scalable skill acquisition for autonomous systems.

**What we added:**
- Methodology: The entire `/skill-extraction` pipeline (`docs/skills/skill-extraction/README.md`) — three-stage process (structural analysis → semantic identification → artifact generation), scoring rubric (4 criteria, 0–12 scale, threshold ≥6), and the `/kiro:skill-extract` + `/kiro:skill-extract-scan` commands are based on this framework.

---

## From Model Scaling to System Scaling: Scaling the Harness in Agentic AI
**arXiv:** https://arxiv.org/abs/2605.26112 | **Year:** 2026 | **Author:** Shangding Gu

**What it's about:** Argues that the next major bottleneck in agentic AI is system-level architecture, not model capability. Introduces a 6-component harness decomposition model — 𝒫_H = Φ(ℛ, ℳ, 𝒞, 𝒮, 𝒪, 𝒢) — where each component (reasoning substrate, memory, context constructor, skill-routing, orchestration, governance) is an independent design lever. Proposes temporal scaling tiers (prompt/skill/memory) and a shift from outcome metrics to process metrics (context efficiency, memory hygiene, routing accuracy, trajectory quality). Benchmarks against CheetahClaws, a Python-native reference implementation.

**What we added:**
- New skill: `agent-harness-design` — 6-component framework as design/audit vocabulary; per-component audit questions and failure modes; staleness-aware memory ranking formula; temporal scaling tier heuristics; process metrics table; harness alignment check format used by skill-extraction Phase 3.
- Integration into `skill-extraction` — Phase 3 now has a mandatory Step 3a that invokes `agent-harness-design` before mapping integration types, so every extraction passes through the architectural lens.
- Enhancement: `context-optimization` v1.1 — "Context Governance" section: selection-policy framing, 4-axis governance table (relevance/compactness/traceability/refresh), staleness-aware ranking formula, provenance-in-practice pattern.
- Enhancement: `multi-agent-patterns` v1.2 — "Skill Routing Quality" section: 4 requirements (specificity/selectivity/composability/verifiability), post-condition coupling pattern, routing audit trail guidance.

---

## Code as Agent Harness
**arXiv:** https://arxiv.org/abs/2605.18747 | **Year:** 2026 | **Authors:** Xuying Ning, Katherine Tieu, Dongqi Fu et al. (42 authors)

**What it's about:** Comprehensive survey reframing code as the operational foundation for agent systems — not just output, but the substrate for reasoning, action execution, environment modeling, and verification. Organized around three layers: Harness Interface (code as reasoning substrate and action interface), Harness Mechanisms (planning, memory, tool use, feedback loops, adaptive optimization), and Scaling (multi-agent coordination, shared state, collective verification).

**Key quote:** "The bottleneck of autonomy is not only the reasoning ability of the base model, but also the reliability of the system that connects model outputs to long-horizon actions and persistent states."

**What we added:**
- New skill: `agent-execution-control` — Plan-Execute-Verify loop (failures are signals, not blockers), Action-Validation Gatekeeper pattern (programmatic safety for irreversible actions), Execution Trace Grounding (intermediate state as repair signals), Contract Formation via Planning (plans as testable specs), Iterative code-grounded repair. Scoped to autonomous multi-step agents only.
- Enhancement: `multi-agent-patterns` v1.1 — Functional Role Specialization taxonomy (Synthesis / Understanding / Verification / Execution / Planning), Convergence Mechanisms taxonomy (6 types: Correctness, Security, Performance, Score-based, Consensus, Implicit), Adversarial Validation pattern (dedicated falsification agent).

---

## Language Models Need Sleep: Learning to Self-Modify and Consolidate Memories
**arXiv:** https://arxiv.org/abs/2606.03979 | **Year:** 2026 | **Authors:** Ali Behrouz, Farnoosh Hashemi, Vahab Mirrokni
**Added:** 2026-06-07

**What it's about:** Proposes a Wake/Sleep paradigm for continual LLM learning without catastrophic forgetting. Wake phase = normal inference with weakness identification; Sleep phase = Knowledge Seeding (targeted demonstrations for observed gaps) + Dreaming (synthetic curriculum generation via RL) + Distillation (parameter-efficient LoRA adapters). Demonstrates measurable improvement on long-horizon tasks, continual learning, knowledge incorporation, and few-shot generalization.

**What we added:**
- Enhancement: `scripts/daily-maintenance-prompt.md` — new Step D (Knowledge Seeding) wires `skill-augment-agent` into the nightly runner, completing the Wake→Sleep cycle automatically. Output line extended to include `skill-updates=<N>`.
- Enhancement: `hooks/claude/action-capture.sh` — Wake-phase struggle tagging: detects non-zero Bash exit codes, infers skill domain, auto-writes `[seed-target:<domain>]` to `observations.md` so the Sleep phase has explicit weakness markers.
- Enhancement: `agents/kiro/skill-augment-agent.md` — two new steps: Step 2.5 collects `[seed-target:]` observations as seeding evidence; Step 3.5 (Dreaming) generates synthetic worked examples per skill gap and writes them to `resources/examples/`.
- Enhancement: `skills/agent-memory-consolidation/SKILL.md` — new "Sleep Cycle Protocol" section documents the full Wake/Sleep framing, the `[seed-target:]` convention, domain mapping table, and the episodic-first guarantee that raw observations are never rewritten.

---

## PACE: A Proxy for Agentic Capability Evaluation
**arXiv:** https://arxiv.org/abs/2607.02032
**Added:** 2026-07-08
**Year:** 2026

**What it's about:** A method to predict expensive agentic benchmark performance (SWE-Bench, GAIA) using a small, cheap subset of non-agentic evaluation instances selected via SVD leverage scores and Spearman correlation. Achieves ~4% MAE and 0.81 Spearman correlation across 14 models at under 1% of full benchmark cost.

**What we added:** Nothing — SKIP.

**Rejected (all candidates):** PACE answers "which model do I run on expensive benchmarks?" — a question the harness never asks. It requires a calibration corpus of 14+ models scored on 19+ benchmarks, a multi-week offline ML effort the harness has no infrastructure for. The `evaluation/funnel` skill already covers the conceptual equivalent (early-stage filtering before expensive runs) at product-team scale. The capability profiles from PACE (per-benchmark ability requirements) would add noise to `model-tiers`, which routes by task type rather than benchmark rank. No skill, hook, routine, or command candidate survived the critic gate.

## SkillsBench: Benchmarking How Well Agent Skills Work Across Diverse Tasks
**arXiv:** https://arxiv.org/abs/2602.12670 | **Year:** 2026 | **Authors:** Xiangyi Li, Yimin Liu, Wenbo Chen, et al.
**Added:** 2026-07-28

**What it's about:** Benchmark of 87 tasks across 8 domains with curated Agent Skills + deterministic verifiers. Curated skills raise average pass rate from 33.9% to 50.5%. Critically: "Focused Skills with at most three modules outperform larger or exhaustive bundles," and smaller models with good skills can match larger models without them.

**What we added:**
- Skill enhancement: `skill-creator` — explicit ≤3-module authoring constraint added to Key Principles.
- Skill enhancement: `skill-curator` — new module-count audit + "Split" action-type row, flagging skills bundling >3 modules as split candidates.
- Skill enhancement: `model-tiers` — routing note that skill-curation and model-escalation are competing levers; check whether a better-curated skill closes the gap before escalating tiers.

---

## When is Routing Meaningful? Diversity and Robustness in Language Model Societies
**arXiv:** https://arxiv.org/abs/2607.09197 | **Year:** 2026 | **Authors:** Fantine Huot, Michael Kaisers, Mirella Lapata
**Added:** 2026-07-28

**What it's about:** Argues multi-agent/multi-model routing needs evaluation beyond task accuracy — behavioral diversity and routing stability under perturbation. Finds diminishing returns past ~10 curated agents, and that prompted/rule-based routers stay stable under paraphrase where learned (KNN-style) routers become fragile despite higher raw accuracy.

**What we added:**
- Skill enhancement: `multi-agent-patterns` — new "Roster size and router robustness" note capping curated subagent_type rosters near ~10 behaviorally-distinct agents, and preferring prompted/rule-based routing over learned routing when robustness matters.
- Skill enhancement: `evaluation` — new "Periodic Checks" section with a perturbation-robustness test for task routers (rephrase a task, confirm routing stays consistent).

---

## Self-Improvements in Modern Agentic Systems: A Survey
**arXiv:** https://arxiv.org/abs/2607.13104 | **Year:** 2026 | **Authors:** Zhe Ren, Yimeng Chen, Dandan Guo, et al.
**Added:** 2026-07-28

**What it's about:** Frames a "modern agent" as a foundation model + operational scaffold, and self-improvement as a self-induced update operator. Classifies prior work by "update target" (what changes — scaffold vs. weights) and "change signal" (what triggers the change — reward, failure trace, verifier output, human feedback).

**What we added:**
- Skill enhancement: `skill-curator` — framing note naming its own weekly audit routine as a scaffold-level self-improvement loop in this taxonomy (update target = skill files, change signal = usage/health metrics + human feedback).

---

## Generative Skill Composition for LLM Agents
**arXiv:** https://arxiv.org/abs/2606.32025 | **Year:** 2026 | **Authors:** Xinyu Zhao, Zhen Tan, Vaishnav Tadiparthi, et al.
**Added:** 2026-07-28

**What it's about:** Reframes skill selection as joint "structured skill composition" (subset + count + order decided together in one pass) rather than sequential top-k retrieval. Shows +18–23pp pass-rate gains on production coding agents.

**What we added:**
- Skill enhancement: `skill-extraction` — new principle in Step 3a: for compound tasks needing multiple skills, decide the full ordered skill plan in one reasoning pass rather than invoking skills reactively one-by-one. (The constrained-decoding mechanism itself isn't portable to a prompting-only harness; only the joint-selection principle was ported.)

---

## Agent Lightning v1.0: Towards Harnessed Agentic RL
**arXiv:** https://arxiv.org/abs/2608.17528 | **Year:** 2026 | **Authors:** Zhiyuan He, Siwei Zhang, Zhiwen Zhou, Yuqing Yang, Yu Kang, Yuge Zhang, Luna K. Qiu, Tin Yan Tsui, Jiahang Xu, Chong Luo (Microsoft, Fudan, Zhejiang, Univ. of Edinburgh)
**Added:** 2026-08-25
**Repo:** https://github.com/microsoft/agent-lightning

**What it's about:** Because agents now run inside deployment harnesses (Claude Code, OpenHands, mini-SWE-agent) that own the tools, context, and control loop, RL post-training should run *through* that harness — "harnessed agentic RL." The trainer then never sees a clean token trajectory, only request/response pairs across a service boundary. The paper enumerates the five failure modes this creates (retokenization, sample merging, advantage calculation, loss normalization, backend scheduling) and ships a ~3,500-LOC framework resolving them: a stateful API Gateway whose proxy URL embeds the rollout ID so every call is auto-attributed, a K8s-Job rollout controller, and a VERL-based trainer. Reported: SWE-bench Verified 41.8% → 56.4% (+14.6pp, RL-only, ~6K examples); search 25.1% → 41.7%; instruction-following 51.9% → 70.2%; collocated async RL ≈2× end-to-end speedup.

**What we added:**
- Augmentation: `skills/agentic-rl-tito/SKILL.md` — new section "When TITO Is Unattainable: Harness-Mediated RL", plus a widened `description` (the old one implied the trainer owns the loop). The skill previously asserted the TITO invariant — never re-encode what you decoded — and stopped, with edge cases covering history rewriting and truncation *inside* an owned loop. It said nothing about a foreign loop, where the invariant is structurally impossible.

  Ported: the three root causes of prefix breakage (chat-template non-compositionality dropping an earlier `<think>` marker; decode–retokenize drift where `h`+`aving` retokenizes as `hav`+`ing`; output reserialization by tool-call handlers); the measurement that makes it non-optional — **only 36% of rollouts remain a single sample, mean 2.41 samples/rollout**, so prefix continuity breaks in ~2/3 of harness-mediated rollouts; best-effort merging as the default, with an explicit warning against buffered token replacement (AReaL, verl Uni-Agent), which trains a response under a stitched prompt `p̃ ≠ p` it was never sampled from — a silent off-policy discrepancy; and the ablation showing the two loss-side fixes must ship **as a pair** (baseline 35.0%; rollout-level advantage *alone* 33.1%, i.e. a regression; both together 38.2%), plus keeping one rollout inside a single optimizer update.

**Rejected:** a new `harnessed-agentic-rl` skill — every mechanism needs trainer, GPU pool, and logit access; three RL skills already exist in a harness whose product is spec-driven development, and all portable content fits in one section of a skill that already exists; Agent Lightning's architecture as a pattern for `agent-harness-design` — idempotent endpoints, stable correlation IDs and "the gateway keeps no request buffer" are generic distributed-systems hygiene in RL vocabulary, text not behavior; the "harness participates in post-training" framing — a definitional reframe with no decision attached, and this harness will never post-train a model; SWE-smith data-curation pipeline — no consumer here, and `skills/evaluation/resources/benchmark-construction.md` owns the adjacent ground; **reward-hacking countermeasures** (observed exploits: reading the gold commit from git history, `wget`-ing the fix from GitHub, `pip`-downloading source; countermeasures: hide `.git` in the task image, egress whitelist) — a genuine gap, `rl-agent-training` gates on "verifiable reward" without warning that a verifiable reward invites gaming, but it is ~10 lines of ART-specific and SWE-bench-specific text; deferred as marginal. No hook, routine, or script is derivable from this paper — nothing in it is event-triggered or schedulable from a Claude Code harness.

See also: [articles/README.md](../articles/README.md) — 14-source sweep batch note.

---

## SKILL.state: Scalable Long-Horizon Agent Skills
**arXiv:** https://arxiv.org/abs/2608.26263 (v2)
**Added:** 2026-09-01
**Year:** 2026 — accepted at EMNLP
**Authors:** Sanket Badhe (Google), Priyanka Tiwari (Purdue), Jonghyun Chung (Google)

**Provenance note:** reached via a newsletter blurb titled "anthropic wants claude operating real lab gear". That description is wrong — this is a Google/Purdue paper on agent runtime context management, with no lab robotics and no Anthropic authorship. The link and the blurb did not match.

**What it's about:** Replaces conversation history with a mutable state object. At step *t* the model receives exactly `(P, Σ_t, O_t)` — immutable skill spec, current structured state, latest observation only — and emits a reasoning trace, a JSON state patch, and an action. The runtime validates the patch, applies `Σ_{t+1} = Σ_t ⊕ ΔΣ_t`, executes, then **permanently discards the reasoning trace**. Prompt cost becomes `O(|P|+|Σ|+|O|)`, independent of step count, versus `O(t)` per prompt and `O(T²)` cumulative for conversational runtimes. Reported: prompt size flat at 1,736–1,905 tokens from T=10 to T=200; at T=200, 0.94 accuracy at 122,384 tokens vs Memory/Summary 0.84 at 6,175,509; InterCode CTF pass@1 54.2% vs 46.4% best baseline at −60.4% tokens; state-based agents recover from an out-of-band world change in **0** turns where history-based ones "hallucinate for 5 to 8 consecutive turns."

**What we added:**
- Augmentation: `skills/context-compression/SKILL.md` — new section "When Compression Is Worse Than Useless", carrying Table 5. The skill already argued compression can be *inefficient* (tokens-per-task over tokens-per-request); it had no case where compression is *destructive*. At an identical ~1,800-token budget on a 100-step task: sliding-window truncation **0.18**, ReAct+LLMLingua **0.22**, summary-capped **0.52**, structured state **0.94** — and the structured form at 1,800 tokens also beat *unbounded* full history at 0.84. Because the budget is held constant, the experiment isolates the variable: the gain is from structure, not from spending fewer tokens. LLMLingua is the instructive failure — a purpose-built compressor scoring barely above naive truncation, because entropy-based pruning deletes "seemingly redundant slot identifiers that are semantically vital." The added practical rule: before compressing a span, ask whether anything later must *join* on an ID, path, key or name in it; if so, restructure rather than summarize. This also supplies the mechanism behind the skill's existing artifact-trail finding — file paths are exactly that kind of low-entropy, high-value token.
- Augmentation: `skills/context-window-management/SKILL.md` — filled two empty anti-pattern stubs (`❌ Naive Truncation`, `❌ Ignoring Token Costs`) that had headings and no bodies. Same Table 5 evidence, plus the cache price ratios pointing at `scripts/utils/token-forensics.py`.

**Rejected:** the core mechanism itself — SKILL.state works by rebuilding the prompt from scratch each turn, and no hook, skill, or command can delete prior turns from a Claude Code context; the runtime owns the prompt. The paper's own limitation #3 also excludes this harness explicitly: it fails where "the task objective is defined over the historical trajectory itself (e.g. auditing, debugging provenance, or explaining past actions), where interaction history is the target output rather than operational overhead" — which describes `agents/kiro/session-judge.md`, `scripts/utils/token-forensics.py`, and `hooks/claude/agent-trace-hook.sh`. Discarding reasoning after a state update — same runtime constraint. Domain schema authored once — `skills/planning-with-files/SKILL.md` and `skills/compiled-truth-pattern/SKILL.md` already do this. Deterministic validation outside the model — already the entire `hooks/claude/*-quality-gate-hook.sh` family. `tested_hypotheses` to prevent repeated failed commands — `hooks/claude/tool-failure-capture.sh` + `tool-failure-recall.sh` is the same idea and stronger, being cross-session rather than per-episode. Merge-not-overwrite patch semantics — the harness's memory is markdown and append-only JSONL, not a merged JSON object, so the 68%-of-failures overwrite mode does not apply. Small-model structured-output taxonomy (68/20/12 split, Gemma-4-31B) — about a model class this harness never runs. Immediate override on contradicting observation — `skills/verification-before-completion` and the injected "a non-zero probe exit is an ANSWER" rule already cover it.

See also: [articles/README.md](../articles/README.md) — same 6-source batch, 2026-09-01.

---

## Dream-RSI: Recursive Self-Improvement through Evolving Worlds
**arXiv:** 2609.14858 (project page https://dream-rsi.com/; repo github.com/zhengkid/Dream-RSI, code "being prepared")
**Added:** 2026-09-30

**Retrieval:** partial. The project page and repo README were read; the paper PDF was not, and WebFetch paraphrased the highlighted passage rather than returning it verbatim.

**What it's about:** Every finished run leaves a tree of decisions with their real outcomes attached, and that tree works as an exact replay simulator. Thousands of candidate policies are scored by replaying the history ("dreaming") without executing anything. The current policy is always one of the candidates, so the chosen one can never score worse than what it replaces. Stated limit: it can only test paths the history covers. Also reports that advice from past runs placed in the prompt did worse than replay alone.

**What we added:**
- Augmentation + script: `skills/hook-design/SKILL.md` "Replay Before Ship" and `skills/hook-design/resources/hook-replay.py` (+ `hook-replay.test.sh`). Replays recorded tool calls from `~/.claude/projects/*/*.jsonl` through the old and new version of a PreToolUse hook and reports `newly_blocked` / `newly_allowed` / `new_errors`, with the old hook as baseline. Counts only by default because transcripts hold secrets; `--fail-on-change` for use inside a hook's `*.test.sh`; zero recorded calls exits 2 rather than passing. Smoke run on the rewritten `prompt-quality-check.sh`: 150 recorded Agent calls, no flips.

**Rejected:** replaying the daily-maintenance reflect loop (its inputs are free-text observations, nothing exact to replay); "only replace the incumbent if the candidate scores better" (already `harness-health-prompt.md` step c).

See also: [articles/README.md](../articles/README.md) — same 18-source batch, 2026-09-30.
