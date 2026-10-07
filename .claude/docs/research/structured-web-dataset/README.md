# structured-web-dataset

Skill for building structured tabular datasets from natural language descriptions — via parallel web research agents or synthetic generation.

**Skill file:** `skills/structured-web-dataset/SKILL.md`  
**Source:** https://github.com/tinyfish-io/bigset  
**Added:** 2026-06-07

---

## What It Does

Turns "I need a table of X with columns Y and Z" into a validated, deduplicated CSV or markdown table.

Two modes:
- **Web** — real data; fans out parallel agents to research and verify each entity from live sources
- **Synthetic** — generated data; defines schema + distribution rules and produces rows with controlled edge cases

---

## When to Invoke

The skill fires when:
- User says "build a dataset of X", "generate synthetic data for X", "I need a table of X"
- User asks for "training data", "sample data", "mock data" with a schema
- Output must be rows and columns — not a prose report

**Do not invoke** for narrative research reports (use `deep-research` instead).

---

## Workflow Summary

```
NL description
      │
      ▼
Phase A: Classify → Web or Synthetic?
      │
      ▼
Phase B: Schema inference → present columns/types/primary key → WAIT FOR APPROVAL
      │
      ├── Web mode ──────────────────────────────────────────┐
      │   C1: Entity discovery via WebSearch                  │
      │   C2: Fan-out Agent() per entity (batches of 10-15)   │
      │   C3: Aggregate + deduplicate                         │
      │                                                        │
      └── Synthetic mode ─────────────────────────────────────┤
          C1: Define generation rules → WAIT FOR APPROVAL      │
          C2: Generate rows (Agent batches for >200 rows)      │
          C3: Validate types, uniqueness, null rate            │
                                                               │
                                                               ▼
                                                     Phase D: Output
                                                     (summary + preview + CSV)
```

---

## Design Decisions

**Why two modes in one skill?**  
Both start identically — NL description → schema approval — and converge at the same output format. Splitting into two skills would duplicate the schema inference and output phases for no benefit.

**Why not use `deep-research`?**  
`deep-research` produces prose reports. This skill requires structured tables with typed columns, primary keys, dedup, and null-rate tracking. The quality contract is different.

**Cap of 20 agents per batch (web mode)**  
Prevents context overload. Larger datasets chain batches.

**Synthetic ≠ hallucinated web**  
Web mode requires null over guess. Synthetic mode generates per explicit rules. The modes are never mixed.

---

## Related Skills

- `deep-research` — prose research reports, not tables
- `dispatching-parallel-agents` — fan-out mechanics reference
- `data-engineer` — broader data pipeline patterns
- `exa-search`, `tavily-web` — can be used inside Phase C2 agents for better web coverage
