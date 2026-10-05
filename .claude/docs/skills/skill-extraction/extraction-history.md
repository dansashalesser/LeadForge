# Skill Extraction History

All `/skill-extraction` sessions from harness inception through mid-May 2026. Ordered chronologically.

---

## Mar 30, 2026 — The System Itself Built

**Source:** arxiv:2603.11808 (repository-to-skill extraction research paper)
*Note: This session built the `/kiro:skill-extract` system — not a live extraction run.*

**What was added:**
- `kiro/settings/rules/skill-extraction-scoring.md` — 4-criteria rubric (Recurrence, Code Quality, Domain Expertise, Generalizability), 0–3 per criterion, 6/12 inclusion threshold, 9/12 high-priority
- `kiro/settings/templates/skill-extraction-plan.md` — structured plan template with candidate ranking table, score breakdown, relationship map
- `agents/kiro/skill-extract-agent.md` — two-mode agent (SCAN → reviewable plan, GENERATE → SKILL.md)
- `commands/kiro/skill-extract-scan.md` + `commands/kiro/skill-extract.md`
- `docs/skills/skill-extraction/README.md` — user guide with security model (shallow clone, read-only, no execution)

**Reasoning:** Translate the paper's 3-stage pipeline (structural analysis → semantic scoring → SKILL.md generation) into a repeatable harness workflow. Security was the main design constraint: shallow clone only, no code execution from source repos, default-unknown risk tags, source provenance mandatory.

---

## May 6, 2026 — First Live Extractions

### 1. https://x.com/sukh_saroy/status/2046177...

**What was added:**
- `agents/kiro/skill-augment-agent.md` — post-session agent that reads judge verdict + observations and appends anti-patterns/learned patterns to relevant SKILL.md files. Append-only, max 3 skills/run, 150-char limit per addition, citation required
- `commands/kiro/daily-maintenance.md` updated — new Step 6 runs skill-augment-agent after reflect/judge
- Nightly CCR routine registered at 9:47 PM

**Reasoning:** Close the learning loop. Every session generates friction signals; skill-augment encodes them back into skills automatically. Judge drains map to skill domains (memory-gap → memory-systems, gate-bypass → verification-before-completion, etc.).

---

### 2. https://github.com/nidhinjs/prompt-master

**What was added:**
- `~/.claude/skills/prompt-master/SKILL.md` (v1.7.0) — JSON-structured prompt input, 30+ tool profiles, 14 templates, 38 anti-patterns, Opus 4.7 agentic guidance
- `~/.claude/skills/prompt-master/references/templates.md` + `patterns.md` — lazy-loaded on demand
- `docs/prompts/prompt-master/README.md` — when to use JSON vs prose, before/after gallery, key behaviors

**Reasoning:** Existing prompt skills were educational. prompt-master is an active prompt factory. Core insight: models guess when dimensions (tone, format, audience, length) are unspecified — JSON eliminates the guessing surface. Activated on ≥3 unspecified dimensions.

---

### 3. https://github.com/openai/privacy-filter

**What was added:**
- `docs/security/privacy-filter/README.md` — OPF setup, architecture (8-layer transformer + sparse MoE + Viterbi CRF), pre-commit integration, output modes, Python API, troubleshooting table, performance tuning

**Reasoning:** Complement existing secrets-management and gdpr-data-handling skills with a runtime PII scanner. Graceful degradation pattern documented: exit code 2 when OPF missing so pre-commit warns rather than blocks.

---

### 4. https://github.com/EveryInc/proof-sdk

**Session recorded** but artifact details not captured in memory. Likely produced `docs/proof/` documentation or a skill file — check `~/.claude/skills/proof*` or `docs/proof/`.

---

## May 7, 2026 — Design Quality + Code Keep Rate + Memory Governance

### 5. https://github.com/pbakaus/impeccable

**What was added:**
- `~/.claude/skill-library/impeccable-audit/SKILL.md` — 27 deterministic anti-pattern rules across 7 domains: typography, color/contrast, spatial design, motion, interaction, responsive, UX writing
- AI fingerprint detection for 7 common AI-generated UI patterns (gradient text, glassmorphism, colored left borders, gradient backgrounds, nested cards, identical card grids, pure white backgrounds)
- Output: structured audit with PASS / NEEDS WORK / BLOCK verdict + file:line references

**Reasoning:** Harness had architecture validation and visual regression testing but no rules for typography hierarchy, contrast ratios, or motion timing. Fills the design quality gap with deterministic (not subjective) rules.

---

### 6. https://github.com/codejunkie99/ztk (→ keep-rate)

**What was added:**
- `~/.claude/skills/keep-rate/SKILL.md` — calculates % of Claude-authored lines (grep: `Co-Authored-By: Claude`) still present in HEAD after 7+ days. Thresholds: >80% strong, 60–80% normal, 40–60% warning, <40% alert. Auto-triggers kaizen notes below 50%.

**Reasoning:** Inspired by Cursor's agent quality loops. Keep rate is a lagging signal — it measures actual code survival, not whether tests pass. Recommended cadence: Monday/Thursday evenings.

---

### 7. https://cursor.com/blog/continually-imp...

**What was added:**
- `hooks/claude/memory-discipline-hook.sh` — PreToolUse gate on `*/memory/*.md` writes; displays discipline rules before write executes
- `hooks/claude/compaction-discipline-hook.sh` — PreCompact gate; injects boundary-timing principles (compact at phase boundaries, preserve artifact paths/decisions/open questions, merge not regenerate)
- `~/.claude/skill-library/agent-memory-discipline/SKILL.md` — canonical reference
- `~/.claude/skills/context-compression/SKILL.md` updated — new "Compaction Modes and Boundary Timing" section

**Reasoning:** OpenAI cookbook identified memory contamination by case-specific facts as the #1 silent failure mode in long-running agents. Hook-based governance enforces rules automatically rather than relying on prompts that Claude might drift from.

---

## May 12, 2026 — GBrain Patterns + SkillOS

### 8. https://arxiv.org/abs/2605.06614 (SkillOS paper)

**What was added:**
- `~/.claude/skills/skill-curator/SKILL.md` — automated skill library curation: MERGE overlapping skills, COMPRESS verbose ones (≤5000 words), DELETE duplicates/stale. 6-phase workflow: inventory → identify → score → propose (approval required) → execute → summary
- Weekly CCR routine registered (Mondays 9am IDT) to prevent library bloat

**Reasoning:** Paper finding: high-quality skill curation is the key bottleneck for self-evolving agents, not skill accumulation. The 400+ skill library was growing without governance. Curation is mandatory approval-gated — no silent deletes.

---

### 9. https://github.com/garrytan/gbrain

**What was added:**
- `hooks/claude/gbrain-agent-spawn.sh` — PreToolUse on `Agent` tool; injects model-tier guidance (haiku=classification, sonnet=generation/subagents, opus=deep-reasoning-only) + background-routing pain signals
- `hooks/claude/gbrain-memory-write.sh` — PreToolUse on `save_observation`; enforces compiled-truth two-zone structure (State section rewrite-in-place at top, Evidence append-only at bottom)
- `hooks/claude/gbrain-external-search.sh` — PreToolUse on `WebFetch`/`WebSearch`; reminds to run memory-first lookup chain before reaching for external APIs
- `~/.claude/skill-library/agent-memory-consolidation/SKILL.md` — episodic-first architecture, 3 failure modes (misgrouping, interference, overfitting), audit checklist

**Reasoning:** Skills that must be manually invoked get skipped. Hook-based injection fires at the exact moment the protocol is relevant — no invocation needed. The memory consolidation skill addresses generative loop drift where iterative LLM rewrites degrade memory quality below no-memory baseline.

---

### 10. https://www.dbreunig.com/2026/05/10/ove...

**Session recorded** — article was about overloaded context / context optimization. Session contributed to the GBrain hook infrastructure (same session). The external search hook (`gbrain-external-search.sh`) directly addresses the overloaded context problem by routing to memory-first before external fetches.

---

## May 14, 2026 — Goal Primitive

### 11. https://code.claude.com/docs/en/goal (Claude Code `/goal` docs)

**What was added:**
- `~/.claude/skill-library/goal-mode/` directory created — skill for running any workflow in autonomous mode (Haiku evaluates completion condition after each turn, continues until met) vs interactive mode (permission steps, debuggable)

**Reasoning:** Existing 53 development workflow skills all required manual step-by-step interaction. No skill covered the pattern of "run this until done without stopping." Goal-mode fills that gap using the built-in `/goal` primitive.

---

## May 18, 2026 — Raindrop + Hook Candidate Assessment

### 12. https://github.com/yvgude/lean-ctx

**Session recorded.** lean-ctx focuses on aggressive context pruning and token budget management. Same session produced the skill-extraction hook candidate enhancement (#13 below).

---

### 13. https://github.com/raindrop-ai/workshop...

**What was added:**
- `~/.claude/skill-library/raindrop-instrument-agent/SKILL.md`
- `~/.claude/skill-library/raindrop-eval-loop/SKILL.md`
- `~/.claude/skill-library/raindrop-agent-replay/SKILL.md`
- Raindrop Workshop tab integrated into harness dashboard
- Per-repo tracing configuration across all 3 registered projects

**Reasoning:** Agent traces were invisible — no way to replay what went wrong or compare runs. Raindrop Workshop adds a dedicated tab for trace inspection, replay, and eval loops without changing session behavior.

---

### 14. https://huggingface.co/blog/continuous_...

**What was added:**
- `~/.claude/skills/skill-extraction/SKILL.md` updated — mandatory "Hook Candidate Assessment" section added to Phase 3. Before finalizing integration map, workflow now invokes `hook-design` to evaluate whether capabilities should become hooks (4 signals: must-run-every-time, describes enforcement, lifecycle-aware, prompt-would-fail-to-enforce)

**Reasoning:** Extractions were mapping capabilities to skills or features but missing hook candidates systematically. The continuous evaluation article reinforced that deterministic enforcement should always go to hooks, not prompts.

---

## Sep 30, 2026 — 18-Source Newsletter Batch (6 subagents × 3 links)

**Sources:** 18 links from codenewsletter.ai, tldr, The Neuron and Superhuman newsletters. Each subagent ran Phases 0–3 read-only on 3 links; the parent merged the fragments into one proposal, and the user approved all 9 integrations. 16 sources were read in full; 2 partly (Cua thread, Dream-RSI).

**What was added (all edits to existing artifacts, no new skills):**
1. `skills/model-tiers/SKILL.md` — removed the false "this harness runs `high`" effort claim (Osmani, *What a task costs on Opus 5.5*)
2. `skills/skill-eval-gate/SKILL.md` — infrastructure failures are missing runs, not FAILs; score-twice judge consistency check (Lance Martin, *Automating eval design and hillclimbing*)
3. `scripts/utils/token-forensics.py` (+ test) and `skills/auditing-token-spend/SKILL.md` — per-hook injection tables (@ericzakariasson's token-efficiency audit prompt)
4. `skills/auditing-token-spend/SKILL.md`, `skills/context-optimization/SKILL.md` — cache-breaker diagnosis, corrected cache lifetime (Osmani)
5. `skills/synthesizing-daily-briefings/SKILL.md`, `scripts/routines/daily-briefing-prompt.md` — stalled-work sweep (Builder.io software-factory video)
6. `scripts/routines/startup-payload-audit.sh` (+ new test), `scripts/utils/dashboard.py` — ceiling ratchet (claude.ai 3x faster post)
7. `hooks/claude/prompt-quality-check.sh` (+ new test), `docs/prompt-quality/README.md` — `think-instruction` / `show-reasoning-request` anti-patterns (Getting the most out of Opus 5.5)
8. `agents/kiro/autoresearch-agent.md` — REPAIR outcome for crashed runs (alphaXiv/OpenResearch)
9. `skills/hook-design/SKILL.md` + `resources/hook-replay.py` (+ test) — replay recorded calls through old/new PreToolUse hooks (Dream-RSI)

Side effect: #6 and #7 also paid down two entries of the no-regex debt ledger (`scripts/utils/no-regex-debt.txt`, 15 → 13).

**Rejected (10 sources):** Cua jev-use thread (partial read, unbacked speed claim, no computer-use workload), Tunguz *Thinking in Systems* (thesis, loops already exist), Marina Wyss workflow video (the SDD flow already run), `rohitg00/ai-engineering-from-scratch` (course material), LangSmith Managed Deep Agents (multi-user memory/credentials, single-user harness), `wbopan/tastebench` (both-orders already `better-call` Step 2b; unparseable-as-wrong conflicts with missing ≠ zero), OpenRouter Jev Router thread (advice text only, no router layer), "30+ PRs a day" video (secondhand pstack), Cloudflare Forge (no OpenAPI to generate from), `dzhng/jevgrep` (third-party API key, sends code off-machine, overlaps lean-ctx/GitNexus/Serena).

**Reasoning:** Default-skip held for 10 of 18. Every approved item extends an existing artifact rather than adding a competitor, so `better-call` was not run. Value is honestly low or unmeasured for #7 (prompt log stores only hashes), #8 (autoresearch usage unknown) and #9 (covers only commands already in history).

**Open:** the harness's `skills/claude-api` shares its name with Anthropic's official `claude-api` skill and may shadow its `build-eval` / `hillclimb` sub-commands — unverified.

---

## Oct 1, 2026 — 4-Source Batch (pi, open-dot, @tvykruta thread, How I AI video)

**Sources:** `earendil-works/pi` and `composio-community/open-dot` (READMEs, rung 1), the @tvykruta X thread via its codenewsletter archive (rung 1, partial: posts 8–10 missing; the link was labelled as a newsletter issue but is a single thread), and How I AI `dAIIaepNhQM` (John Lindquist on the Jev decision model, full transcript via rung 4). The user approved all items.

**What was added (no new skills):**
1. `kiro/settings/rules/tasks-generation.md` — refactor-first task ordering (@tvykruta "two steps, never one")
2. `scripts/lib/pip-cooldown.sh` (+ test) wired into `lib/venv-tools.sh` and `lib/repo-venv.sh` — `P2D` release-age cooldown on every harness pip/uv install (pi `min-release-age`)
3. `install.sh` — `impeccable@3.6.0` pin

**Rejected:** open-dot entirely (see git/README.md). The How I AI Jev episode: it needs a third-party API key while harness LLM calls go only through `claude --print`; cheap bulk classification is already covered by `cheap-model-delegation`; and the multi-pass and decision-type tips only apply if you call Jev. This is the third Jev source rejected after the two on 2026-09-30.

**Reasoning:** `better-call` is in the harness source but not installed in `~/.claude/skills` (`Skill("better-call")` returned "Unknown skill"), so #1 was scored by hand with its rubric, both reading orders: MERGE. #2 was first scoped to `.venv-tools` only. A run of `check-harness-deps.sh` showed `harness-requirements.txt` is empty and that `raindrop-ai` actually installs through `repo_pip_install`, so the cooldown moved into a shared lib that both paths use (3 call sites). That makes #2 a security fix for unpinned dependencies, an underserved area, not another generic software-engineering rule.

**Open:** `better-call` not installed globally. Not covered by the cooldown: `headroom-setup.sh` `pip install --user` and the `.venv-tools` pip self-upgrade. A real network install with the cooldown was denied at the permission prompt, so it is verified with fake binaries only.

---

## Summary Table

| Date | URL | Skill/Artifact Created | Type |
|------|-----|------------------------|------|
| Mar 30 | arxiv:2603.11808 | `/kiro:skill-extract` system (scoring rule, template, agent, commands, docs) | Harness feature |
| May 6 | sukh_saroy tweet | `skill-augment-agent`, daily-maintenance Step 6, nightly CCR | Harness feature |
| May 6 | github/nidhinjs/prompt-master | `prompt-master` skill v1.7.0 (JSON prompting) | Skill |
| May 6 | github/openai/privacy-filter | `privacy-filter` docs | Docs |
| May 6 | github/EveryInc/proof-sdk | Unknown — check `~/.claude/skills/proof*` | ? |
| May 7 | github/pbakaus/impeccable | `impeccable-audit` skill (27 design rules, AI fingerprints) | Skill |
| May 7 | github/codejunkie99/ztk | `keep-rate` skill (code survival metric) | Skill |
| May 7 | cursor.com/blog | memory-discipline hook + compaction hook + `agent-memory-discipline` skill | Hook + Skill |
| May 12 | arxiv:2605.06614 | `skill-curator` skill + weekly CCR routine | Skill + Routine |
| May 12 | github/garrytan/gbrain | 3 GBrain PreToolUse hooks + `agent-memory-consolidation` skill | Hook + Skill |
| May 12 | dbreunig.com | Context for gbrain hook design (overloaded context → external search hook) | Context |
| May 14 | claude.ai/code /goal | `goal-mode` skill directory | Skill |
| May 18 | github/yvgude/lean-ctx | Context for hook candidate assessment enhancement | Context |
| May 18 | raindrop-ai/workshop | 3 Raindrop skills + Workshop dashboard integration | Skill + Integration |
| May 18 | huggingface.co/continuous_eval | `skill-extraction` SKILL.md updated (hook candidate assessment phase) | Skill update |
| Sep 30 | 18-link newsletter batch | 9 augmentations: `model-tiers`, `skill-eval-gate`, `auditing-token-spend`, `synthesizing-daily-briefings`, `hook-design` (+ `hook-replay.py`), `autoresearch-agent`; `token-forensics.py`, `startup-payload-audit.sh`, `prompt-quality-check.sh` | Skill + Script + Hook updates |
| Oct 1 | pi, open-dot, @tvykruta, How I AI | `tasks-generation.md` refactor-first rule; `lib/pip-cooldown.sh` (+ test); `impeccable@3.6.0` pin | Rule + Script lib + Install pin |
