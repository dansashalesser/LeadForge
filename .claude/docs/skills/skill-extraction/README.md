# Skill Extraction from Repositories

Extract reusable knowledge and automation from code repositories and convert them into the right artifact for the Claude Code harness — skills, hooks, scripts, commands, or routines.

Based on the methodology from [Automating Skill Acquisition through Large-Scale Mining of Open-Source Agentic Repositories](https://arxiv.org/abs/2603.11808).

All resources used in extractions are logged by type:
- [`docs/papers/`](../../sources/papers/README.md) — scientific papers (arXiv)
- [`docs/git/`](../../sources/git/README.md) — GitHub repositories
- [`docs/articles/`](../../sources/articles/README.md) — online articles and blog posts
- [`docs/x/`](../../sources/x/README.md) — pasted text

---

## How It Works

The extraction follows a three-stage pipeline:

### Stage 1: Repository Structural Analysis
- Maps directory structure (entry points, core logic, config, docs, tests)
- Classifies repo type (library, framework, CLI, web app, ML project, agent system)
- Reads key documentation and dependency manifests

### Stage 2: Semantic Identification & Classification
- Identifies candidate modules encoding reusable knowledge or automation
- Scores each against a 4-criteria rubric (Recurrence, Code Quality, Domain Expertise, Generalizability)
- **Harness alignment check** — invokes `agent-harness-design` to map each candidate to the 6-component framework (ℛ/ℳ/𝒞/𝒮/𝒪/𝒢) and flag whether the target component is weak/partial/covered before proposing anything
- **Classifies each candidate into the right artifact type** (skill, hook, script, command, or routine)
- Cross-references existing skills in **both** install tiers — `~/.claude/skills/` (listed) and `~/.claude/skill-library/` (on-demand) — to avoid duplicates; defaults to extend-not-duplicate when coverage is partial
- Produces a ranked extraction plan for human review

### Stage 3: Artifact Generation
- Deep-reads each approved candidate's source code
- Generates the appropriate artifact based on classification:
  - `skill` → `~/.claude/skill-library/<name>/SKILL.md` by default (plus a row in its domain master's table and a line in `scripts/setup/skill-library.txt`); `~/.claude/skills/<name>/SKILL.md` only when something must load it by name through the `Skill()` tool — see `docs/skills/SKILL-HIERARCHY.md`
  - `hook` → `~/.claude/hooks/<name>.sh` + registration instructions
  - `script` → `~/.claude/scripts/<name>.sh`
  - `command` → `~/.claude/commands/<name>.md`
  - `routine` → `~/.claude/commands/<name>.md` + suggested cron schedule
- **Phase 5b — SkillOS Quality Gate** (new skills only): scores the skill against 4 dimensions (task relevance, operational validity, content quality, compression) before marking it complete. Failures block installation until fixed.
- **Phase 5c — Identity Alignment Check** (new skills only): invokes `agent-identity` in Mode B to validate description specificity, trigger sharpness, behavioral concreteness, and explicit exclusions. Runs after Phase 5b passes, before the skill is logged to the sources index.
- **Phase 5d — Verification Companion Check** (new skills only): asks whether the skill's domain involves manual checks a human would run after Claude's work (visual inspection, sampling output, checking logs). If yes, invokes `verification-skill-authoring` to create a companion `<domain>-verify` skill before proceeding. Runs after Phase 5c.

---

## Artifact Types

| Type | Best for |
|------|---------|
| **skill** | Workflow patterns, decision trees, domain knowledge Claude applies when asked |
| **hook** | Auto-firing on Claude Code events (SessionStart, PreToolUse, PostToolUse, Stop) |
| **script** | Utility shell/Python scripts run directly, no Claude integration needed |
| **command** | Interactive `/slash-command` workflows with user arguments |
| **routine** | Recurring/scheduled operations (nightly maintenance, monitoring, reports) |

A single repo can yield candidates of multiple types. One candidate can also produce multiple artifacts if warranted.

---

## Commands

### `/kiro:skill-extract-scan <repo>` — Analyze and plan

Scans a repository and produces an extraction plan for review.

```
/kiro:skill-extract-scan https://github.com/org/repo
/kiro:skill-extract-scan /path/to/local/repo
/kiro:skill-extract-scan org/repo
```

Output: `.claude/skill-extraction/<repo-name>/plan.md`

### `/kiro:skill-extract <plan-or-repo>` — Generate artifacts

Generates artifacts from an approved plan, or runs the full pipeline.

```
# From a reviewed plan:
/kiro:skill-extract .claude/skill-extraction/my-repo/plan.md

# Full pipeline (no review gate):
/kiro:skill-extract https://github.com/org/repo -y
/kiro:skill-extract /path/to/local/repo -y
```

---

## Scoring Rubric

Each candidate is scored on 4 criteria (0-3 scale, max 12):

| Criterion | What it measures |
|-----------|-----------------|
| Recurrence | How universally reusable is this pattern? |
| Code Quality | Is it well-structured, documented, tested? |
| Domain Expertise | Does it encode non-obvious specialist knowledge? |
| Generalizability | Can it apply beyond this specific repo? |

**Threshold**: Score >= 6 to include, >= 9 is high priority.

**Modifiers**: -2 for existing skill overlap, +1 for good docs, +1 for test coverage, -1 for heavy coupling.

Full rubric: `.claude/kiro/settings/rules/skill-extraction-scoring.md`

---

## Typical Workflow

```
1. /kiro:skill-extract-scan https://github.com/interesting/repo
2. Review .claude/skill-extraction/repo/plan.md
   - Check artifact types — adjust if the classification feels wrong
   - Remove candidates you don't want
   - Split a row into two if a candidate warrants multiple artifact types
3. /kiro:skill-extract .claude/skill-extraction/repo/plan.md
4. Verify: read generated artifacts, check hook registration comments
```

For trusted repos or quick extraction:
```
/kiro:skill-extract https://github.com/org/repo -y
```

---

## Security

- Source repos are cloned with `--depth 1` (shallow) to a temp directory
- Analysis is read-only — no code from the source repo is executed
- Scripts are not copied verbatim — knowledge is extracted and regenerated
- All artifacts default to `risk: unknown` unless purely informational
- Source provenance is recorded in every artifact's frontmatter or header

## Extracted Skills Index

Skills extracted and logged to `docs/sources/` by source type. See the relevant source index for full provenance.

### From arXiv papers (`docs/sources/papers/`)
- `agent-harness-design` — 6-component harness framework (ℛℳ𝒞𝒮𝒪𝒢) from arXiv:2605.26112; Phase 4 (Operational Diagnostics) added 2026-05-31
- `agent-execution-control` — Plan-Execute-Verify loop, gatekeeper pattern from arXiv:2605.18747
- `multi-agent-patterns` — functional role taxonomy, convergence types from arXiv:2605.18747
- `context-degradation` — context window failure modes
- `context-optimization` — context budget strategies

### From GitHub repositories (`docs/sources/git/`)
- `cag-implementation` — Cache-Augmented Generation (KV cache preloading) from hhhuang/CAG
- `rag-architect` — RAG design decision gates
- `rag-implementation` — RAG implementation patterns
- `llm-fine-tuning` — LLM fine-tuning workflow
- `prompt-engineering` — prompt engineering patterns (adapted from prompt-master)
- `frontend-performance` — frontend performance checklist from Linear breakdown
- `frontend-code-quality` — HTML/CSS/JS quality rules from bendc/frontend-guidelines
- `evaluation` — evaluation skill family (router + micro/macro/funnel/long-trajectory sub-skills)
- `karpathy-guidelines` — 4-principle behavioral checklist for every coding task from multica-ai/andrej-karpathy-skills (2026-05-31)
- `action-capture` hook + `agent-memory-discipline` enhancement — "memory from what agents do" design patterns from MemoriLabs/Memori (2026-06-02)
- `hook-design` augmentation + daily maintenance Step D (pattern scoring) + `forward-patterns.md` — instinct-based confidence scoring from affaan-m/ECC (2026-06-02)

### From online articles (`docs/sources/articles/`)
- `frontend-performance` — from performance.dev Linear breakdown (2026-05-27)
- `evaluation/funnel` — from Spotify Engineering Blog (2026-05-27)
- `progressive-complexity-ladder` — from jxnl.co (2026-05-27)
- `cma-outcomes` — from Anthropic Cookbook (2026-05-27)
- `evaluation/macro` — from OpenAI Cookbook (2026-05-31)
- `evaluation/long-trajectory` — from JudgmentLabs blog (2026-06-01)
- `instruction-architecture` — from walkinglabs.github.io/learn-harness-engineering Lectures 03–04 (2026-05-31)
- `feature-list-primitive` — from walkinglabs.github.io/learn-harness-engineering Lectures 07–08 (2026-05-31)
- `session-clean-state` — from walkinglabs.github.io/learn-harness-engineering Lectures 05, 12 (2026-05-31)
- `multi-agent-patterns` v1.3.0 enhancement — Agent vs. Workflow tool decision table + routing tree + ultracode mode, from claude.com/blog dynamic-workflows (2026-05-31)
- `agentic-rl-tito` — TITO correctness invariant + prefix-preservation test + model compat table for multi-turn RL training of tool-calling LLMs, from qgallouedec-tito.hf.space (2026-06-02)

### From pasted text (`docs/sources/x/`)
- `tdd-workflows-tdd-red` source-grounding augmentation + `tdd-workflow` RED phase principles + `setup-buffer-hook.sh` + `stop-hook.sh` setup capture — from Cognition Devin blog post on autonomous end-to-end testing (2026-06-02)
- `rtk` tool install + `caveman` tool install with auto-lite SessionStart hook — from YouTube transcript on Claude Code token reduction strategies (2026-06-02)
- `multi-agent-patterns` v1.4.0 enhancement — "Dynamic Workflow Patterns" section: 3 failure modes, 6 patterns (classify-and-act, fan-out-and-synthesize, adversarial verification, generate-and-filter, tournament, loop-until-done), composition matrix, `/goal`+`/loop` controls, quarantine pattern, workflow-as-Skill packaging, 8 common mistakes — from movez.substack.com dynamic-workflow article (2026-06-08)

_Last synced: 2026-10-01_
