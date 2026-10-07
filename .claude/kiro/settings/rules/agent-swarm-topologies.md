# Agent Swarm Topologies

When spawning multiple sub-agents, choose a coordination topology that matches the task structure. Wrong topology choice causes deadlocks, duplicated work, or missed synthesis.

Source: [Ruflo](https://github.com/ruvnet/ruflo) — adapted as harness-native coordination patterns.

---

## Topology Selection

| Task Structure | Topology | Use When |
|---|---|---|
| One orchestrator delegates, workers report back | **Hierarchical** | Tasks have clear owner + specialists; results need synthesis |
| All agents work independently, no dependencies | **Mesh** | Tasks are parallel and self-contained; no shared state |
| Output of agent N feeds agent N+1 | **Ring** | Pipeline stages: research → design → implement → validate |
| Single coordinator, agents are pure executors | **Star** | Orchestrator holds all context; agents are stateless workers |

---

## Hierarchical (Queen / Worker)

One queen agent owns the task, decomposes it, dispatches workers, and synthesizes results.

```
Queen (Opus)
├── Worker A (Sonnet) — scoped subtask
├── Worker B (Sonnet) — scoped subtask
└── Worker C (Haiku)  — mechanical subtask
```

**Use for:** spec implementation, full-stack features, complex investigations with multiple facets.

**Queen responsibilities:** task decomposition, conflict resolution, final synthesis. Workers must not share state or call each other.

**Pitfalls:** queen becomes a bottleneck if workers produce outputs the queen can't synthesize in one pass. Keep worker count ≤ 6.

---

## Mesh (Peer-to-Peer)

Agents work fully independently. No agent depends on another's output. Results are collected and merged at the end.

```
Agent A ──┐
Agent B ──┤──► Collect + merge
Agent C ──┘
```

**Use for:** parallel research across independent domains, scanning multiple files/modules simultaneously, running multiple validators in parallel.

**Pitfalls:** only works when tasks are truly independent. If any agent needs another's output mid-flight, use Ring or Hierarchical instead.

---

## Ring (Sequential Pipeline)

Each agent receives the previous agent's output and enriches or transforms it.

```
Research → Design → Implement → Validate → Document
```

**Use for:** spec phases (requirements → design → tasks → impl), data pipelines, workflows where each stage gates the next.

**Pitfalls:** a failure in any ring agent blocks all downstream agents. Add validation at each handoff. Don't pipeline more than 5 stages without checkpointing.

---

## Star (Hub + Spokes)

One coordinator holds all context and dispatches stateless executor agents. Each spoke does exactly one job and returns.

```
     Coordinator
    /     |     \
  Exec  Exec   Exec
```

**Use for:** orchestrating identical operations across many targets (e.g., same analysis on 20 files), tool-calling patterns where the coordinator drives.

**Pitfalls:** coordinator context grows with every spoke result. Summarize spoke outputs before feeding to the next dispatch.

---

## Integration with Model Tiering

Topology and model tier interact. Apply both rules together:

| Topology Role | Recommended Tier |
|---|---|
| Queen / Coordinator | Opus (inherits parent) |
| Worker with judgment | Sonnet |
| Worker mechanical | Haiku |
| Ring: early stages (design, analysis) | Sonnet or Opus |
| Ring: late stages (execution, scanning) | Haiku |

---

## Anti-Patterns

| Anti-Pattern | Problem | Fix |
|---|---|---|
| Mesh with shared state | Race conditions, duplicate writes | Switch to Hierarchical with queen managing state |
| Ring > 5 stages without checkpoint | Full restart on late failure | Add `save-session-agent` after stage 3 |
| Queen spawning > 8 workers | Context overflow on synthesis | Sub-decompose: queen → sub-queens → workers |
| Star with stateful spokes | Context bleed between executions | Ensure each spoke receives only what it needs, returns only what coordinator needs |
| Mixed topology without explicit handoff | Ambiguous ownership | Pick one topology per task boundary; nest if needed |
