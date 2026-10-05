# Prompt Quality (PQ) System

Heuristic scoring pipeline that measures and tracks the quality of every agent prompt spawned during Claude Code sessions. No external service or LLM required — runs locally, scores via literal-token heuristics (no regex), logs to `~/.code-insights/pq-log.jsonl`.

## What It Does

Every `Agent` tool call is intercepted by `prompt-quality-check.sh` (PreToolUse hook). The hook:
1. Extracts the `prompt` field from the tool input JSON
2. Scores the prompt against 6 PQ dimensions using fast Python heuristics
3. Outputs a scored report into Claude's context (stdout)
4. Appends a JSON entry to `~/.code-insights/pq-log.jsonl`

At session start, `session-start-hook.sh` reads the last 14 log entries and emits a one-line quality baseline so Claude knows its weak dimensions before writing any new agent prompts.

The dashboard **Session Health → Prompt Quality** tab reads the log file and visualizes trends.

## The 6 PQ Dimensions

| Dimension | What it measures | Heuristic signals |
|---|---|---|
| `context_provision` | Enough background for the agent to start | File paths, prior attempts, background keywords |
| `request_specificity` | Clear, unambiguous goal | Action verbs, named targets, no vague phrases |
| `scope_management` | Bounded scope + output format specified | "only", "do not touch", output format keywords |
| `information_timing` | Goal stated first, context after | Action verb in first third of prompt |
| `correction_quality` | If redirecting, names the exact error | "specifically", "exactly", expected behavior stated |
| `overall` | Composite average | Average of applicable dimensions |

`correction_quality` returns N/A (not counted) for new tasks — only scored when the prompt contains correction language ("wrong", "incorrect", "instead", etc.).

## Anti-Pattern Detection

Separately from the 5 scored dimensions, `detect_anti_patterns()` flags named prompt anti-patterns by presence/absence rather than a 1-5 spectrum — these bloat or degrade a prompt without adding real signal:

| Pattern | Trigger | Tip |
|---|---|---|
| `stale-few-shot` | 2+ `example:` / `e.g.` blocks | Verify the examples still match the current codebase, or drop them |
| `mandatory-scratchpad` | "use a scratchpad" / "write your reasoning in" / "think step by step in a scratchpad" | Only ask for visible intermediate reasoning if the task genuinely needs it |
| `maximally-thorough-phrasing` | "maximally thorough" / "as thorough as possible" / "leave no stone unturned" / "be extremely comprehensive" / "utmost thoroughness" | Name the actual completeness criterion instead (which files, which cases) |
| `verification-ritual` | 3+ occurrences of verify/double-check/triple-check/"make sure to confirm" | State the one concrete check that matters instead of stacking synonyms |
| `think-instruction` | "think carefully" / "think step by step" / "think hard(er)" / "think deeply", unless it is a scratchpad request (that is `mandatory-scratchpad`) | The model always reasons before replying — drop it, or raise the effort level instead |
| `show-reasoning-request` | "show your reasoning" / "show your thinking" / "reveal your reasoning" / "show/include your chain of thought" | Such requests can be declined — ask for a short explanation of the chosen approach instead |

The last two come from Anthropic's "Getting the most out of Opus 5.5" guidance (2026-09). Their hit rate is unmeasured: the log keeps only a prompt hash, so the only count available is the `anti_patterns` field from here on.

Findings are printed as a `🚩 Anti-patterns:` block in the hook output and logged under an `anti_patterns` array in each JSONL entry — they do not affect the `overall` score.

Matching is literal — the prompt is split into word tokens and phrases are checked on token boundaries, with no regex (repo-wide ban; see `scripts/utils/no-regex-debt.txt`). The rewrite was checked score-identical to the old regex version on 94 real Agent prompts. Tests: `bash hooks/claude/prompt-quality-check.test.sh`.

## Files

| File | Purpose |
|---|---|
| `hooks/claude/prompt-quality-check.sh` | PreToolUse hook — scores every Agent call |
| `hooks/claude/session-start-hook.sh` | Extended to show PQ baseline at session start |
| `scripts/utils/dashboard.py` | `render_prompt_quality()` function — Session Health tab |
| `skills/prompt-quality-assess/SKILL.md` | Cognitive rubric — apply before writing agent prompts |
| `~/.code-insights/pq-log.jsonl` | Runtime log (global, not per-repo) |

## Log Format

```jsonl
{"ts":"2026-06-11T12:34:56+00:00","overall":3.8,"dims":{"context_provision":3,"request_specificity":5,"scope_management":4,"information_timing":4,"correction_quality":null},"prompt_hash":"a1b2c3d4e5f6","word_count":42,"anti_patterns":[]}
```

## Dashboard Tab

Located at **Session Health → Prompt Quality** (✨ tab). Shows:
- Summary strip: 7-day avg, total spawns scored, weakest dimension
- Per-dimension horizontal bars (green ≥4, yellow 3–4, red <3)
- Rolling score trend chart (last 20 spawns)
- Glossary: what PQ means and how to improve

## Hook Output Example

```
⚡ PQ Score: 3.2/5
  ✅ request_specificity: 5/5
  ⚠  context_provision: 2/5  →  add file paths, prior attempts, or relevant background
  ⚠  scope_management: 2/5   →  state what NOT to change; specify output format
  ✅ information_timing: 4/5
  —  correction_quality: N/A
  🚩 Anti-patterns:
     - stale-few-shot: multiple example blocks — verify they still match the current codebase, or drop them
  ⬆  Consider improving flagged dimensions before spawning.
```

## Session Baseline Example

```
📊 Prompt Quality Baseline (last 14 agent spawns): 🟡 avg 3.4/5 | weakest: context provision (2.8), scope management (3.1)
   → Reminder: front-load context and bound scope on every Agent call.
```

## How to Improve Scores

Invoke the `prompt-quality-assess` skill before writing agent prompts. It provides per-dimension rewrite patterns to get scores ≥4.0 before the hook fires.

## Inspired By

[github.com/melagiri/code-insights](https://github.com/melagiri/code-insights) — local session analytics tool that scores prompt quality with LLM analysis. This system replicates the 6-dimension schema as fast heuristics with no external dependencies.

---

_Last synced: 2026-09-30_
