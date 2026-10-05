---
description: Evaluate session patterns with quality gates before saving to memory
allowed-tools: Read, Task
argument-hint: [scope:session|sprint|feature]
---

# Learning Evaluation

## Parse Arguments
- Scope: `$1` (optional, default: `session`)

## Scope Resolution

- **session**: Evaluate patterns from the current conversation
- **sprint**: Evaluate patterns accumulated in observations.md since last learn-eval
- **feature**: Evaluate patterns from a specific spec directory

## Invoke Subagent

Delegate evaluation to learn-eval-agent:

```
Task(
  subagent_type="learn-eval-agent",
  description="Evaluate and curate session learnings",
  prompt="""
Scope: {$1 or 'session'}

Evaluate patterns from the specified scope against existing knowledge in:
- .claude/memory/meta/patterns.md
- .claude/memory/observations.md

For each pattern found, produce a verdict: Save, Absorb, Route, or Drop.
Route = lesson is tied to exactly one existing skill → push it into that skill, not memory.
Apply quality gates: specificity, actionability, evidence.
"""
)
```

## Display Result

Show the evaluation report with verdicts.

### Next Steps Guidance

- Patterns marked **Save** have been written to `meta/patterns.md`
- Patterns marked **Absorb** have been merged into existing entries
- Patterns marked **Route** are skill-specific — push each into its target skill's SKILL.md (via `skill-augment-agent`, or automatically on the next `/kiro:daily-maintenance` run). Skills-over-memory: a single-skill lesson helps everyone using that skill; parked in memory it silently rots.
- Patterns marked **Drop** were discarded (reasons provided)

**Relationship to /kiro:reflect**: Use `/kiro:reflect` for quick, frequent capture during active work. Use `/kiro:learn-eval` for deeper periodic evaluation (end of sprint, after major feature, or before archival).
