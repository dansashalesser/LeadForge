# GBrain Patterns

Operational protocols extracted from [garrytan/gbrain](https://github.com/garrytan/gbrain) — a production AI agent brain built on a hybrid knowledge graph + RAG system. These patterns apply regardless of whether gbrain is installed.

**Extracted:** 2026-05-12 | **Source:** gbrain v0.25.x skill conventions and architecture docs

---

## What Was Extracted

Four platform-agnostic protocols are now active in this harness:

| Pattern | Skill | Hook | Fires on |
|---|---|---|---|
| Memory-First Lookup | `memory-first-lookup` | `gbrain-external-search.sh` | Every `WebFetch` / `WebSearch` call |
| Model Tiers | `model-tiers` | `gbrain-agent-spawn.sh` | Every `Agent` tool call |
| Background Work Routing | `background-work-routing` | `gbrain-agent-spawn.sh` | Every `Agent` tool call |
| Compiled Truth Pattern | `compiled-truth-pattern` | `gbrain-memory-write.sh` | Every `save_observation` call |

---

## Pattern 1: Memory-First Lookup

**Skill:** `~/.claude/skill-library/memory-first-lookup/SKILL.md`
**Hook:** `gbrain-external-search.sh` (fires on `WebFetch`, `WebSearch`)

Before calling any external API, always check claude-mem first. The lookup chain:

1. `mcp__plugin_claude-mem_mcp-search__search` — keyword search, zero cost
2. Same tool with semantic phrasing if keyword is thin
3. `get_observations` for any hits found
4. External APIs only if memory returns nothing useful

Also applies when spawning agents: run memory lookup first, then include findings in the agent's prompt brief. An agent that re-derives established context wastes tokens and may contradict prior decisions.

**Why:** GBrain benchmarked this — memory had the answer ~90% of the time, but agents skipped it and burned external API calls anyway.

---

## Pattern 2: Model Tiers

**Skill:** `~/.claude/skill-library/model-tiers/SKILL.md`
**Hook:** `gbrain-agent-spawn.sh` (fires on every `Agent` call)

| Tier | Model | Use for |
|---|---|---|
| utility | `claude-haiku-4-5-20251001` | classification, validation, expansion, dedup |
| reasoning | `claude-sonnet-5` | generation, synthesis, chat — **DEFAULT** |
| deep | `claude-opus-5` | complex multi-step reasoning, high-stakes judgment |
| autonomous | `claude-fable-5` | long, multi-sitting autonomous sessions (`/model fable`) |
| subagent | `claude-sonnet-5` | Agent() calls — use sonnet, not opus |

The non-obvious rule: **subagents run sonnet, not opus.** Subagents run multi-turn tool loops; latency compounds and the bottleneck is tool-call reliability, not reasoning depth. Opus buys little here at 3× the cost.

---

## Pattern 3: Background Work Routing

**Skill:** `~/.claude/skill-library/background-work-routing/SKILL.md`
**Hook:** `gbrain-agent-spawn.sh` (fires on every `Agent` call)

Default mode: **inline**. Switch to background only when a pain signal fires:

| Signal | Trigger |
|---|---|
| Gateway restart mid-task | Session crashed while agent was running |
| State drop | User asks "what happened to the agent" |
| Parallel > 3 | More than 3 concurrent agents needed |
| Long runtime | Expected > 5 minutes |
| User frustration | "this keeps breaking", "it keeps losing track" |

When ≥1 signal fires: offer the switch explicitly, don't switch silently.

---

## Pattern 4: Compiled Truth

**Skill:** `~/.claude/skill-library/compiled-truth-pattern/SKILL.md`
**Hook:** `gbrain-memory-write.sh` (fires on every `save_observation`)

Every living memory observation has two zones:

```
## State
[Current synthesis — rewrite when evidence changes. Each fact gets [Source: ...].]

## Evidence / Timeline
[Append-only dated log. Never edit past entries.]
- YYYY-MM-DD | event [Source: ...]
```

**Zone rules:**
- State = rewrite in place (always current, never stale)
- Timeline = append only (preserves provenance)
- Every fact in State needs an inline `[Source: type, context, YYYY-MM-DD]` citation

**Why two zones?** A flat append-only log becomes unreadable. A flat rewrite-in-place document loses provenance. Two zones solve both.

---

## How Automatic Activation Works

### Via Hooks (shell-level enforcement)

Three hooks fire automatically from `$SDD_HARNESS/.claude/settings.json`:

```
gbrain-agent-spawn.sh     → PreToolUse on Agent
                            Injects model-tier table + background-routing pain signals + memory-first reminder
                            Fires on every Agent() call before the subagent is spawned
                            Addresses the PARENT only — PreToolUse:Agent cannot reach the child.
                            The banner is a request that the caller brief the subagent, not a
                            guarantee. Always-true conventions are carried into the child by
                            subagent-context-hook.sh on SubagentStart instead; this hook keeps
                            the spawn-time decisions only the parent can make (which model,
                            run mode, what context to hand down).

gbrain-memory-write.sh    → PreToolUse on save_observation
                            Injects compiled-truth two-zone structure + source attribution format
                            Fires on every memory write

gbrain-external-search.sh → PreToolUse on WebFetch|WebSearch
                            Single-box reminder to check memory before external calls
                            Fires on every web fetch or search
```

### Via Skills (semantic invocation)

The `using-superpowers` system requires skill invocation at 1% trigger confidence. Each skill has explicit trigger conditions covering its activation scenarios. Skills load the full protocol detail — hooks keep output compact to avoid noise on every call.

**Skill triggers summary:**

| Skill | Auto-invoked when |
|---|---|
| `memory-first-lookup` | About to use WebFetch/WebSearch, research tasks, entity lookups, agent briefing |
| `model-tiers` | Spawning Agent, choosing model in API code, multi-agent architecture decisions |
| `background-work-routing` | Spawning Agent, long-running tasks, parallel work, agent state frustration |
| `compiled-truth-pattern` | Writing save_observation, updating memory files, structuring research output |

---

## What Was Not Extracted

| Component | Reason |
|---|---|
| `brain-ops`, `query`, `ingest`, `enrich` skills | Require gbrain CLI and `gbrain__*` MCP tools |
| GBrain MCP server | Requires `gbrain init` — separate install if user wants to adopt gbrain as knowledge backend |
| Cron scheduler, Minion orchestrator | Tied to gbrain's job queue system |
| Entity enrichment pipeline | Requires gbrain's graph database |
| Citation fixer, frontmatter guard | Require gbrain's CLI |

To adopt gbrain as the actual knowledge backend: `git clone https://github.com/garrytan/gbrain ~/gbrain && cd ~/gbrain && bun install && gbrain init`. The `INSTALL_FOR_AGENTS.md` in the repo is the 9-step guide.

_Last synced: 2026-09-01_
