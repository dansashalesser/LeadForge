---
description: Audit harness rules effectiveness, propose improvements
allowed-tools: Read, Task, Glob, Bash
---

# Kiro Evolve — Harness Architecture Audit

## Pre-check

Use Glob to verify `.claude/memory/meta/` exists. If missing, tell user to run `/kiro:reflect` first to bootstrap memory.

## Invoke Subagent

Delegate audit to evolve-agent:

Use the Task tool to invoke the Subagent:

```
Task(
  subagent_type="evolve-agent",
  description="Audit harness rules and propose improvements",
  prompt="""
Audit the SDD harness rules for effectiveness and propose improvements.

File patterns to read:
- .claude/kiro/settings/rules/*.md
- .claude/memory/meta/*.md (including meta/graduations.md for graduation tracking)
- .claude/memory/observations.md
- .claude/memory/hot-memory.md

Also scan for enforceable patterns (Step 2b):
- Read .claude/steering/*.md for conventions that could be linter rules
- Check for [enforceable] tagged observations
- Check existing linter config to avoid proposing already-enforced rules
- Use the graduate-to-linter proposal type for enforceable patterns

Also perform alignment analysis (Step 1c):
- Parse alignment:N and structural:ok|malformed fields from trace.log
- Compute mean alignment, structural reliability, and trends per agent
- For agents flagged DIAGNOSE (alignment < 3.0) or FIX-FORMAT (structural < 90%),
  invoke prompt-diagnosis-agent to produce targeted instruction change recommendations
- Convert diagnosis recommendations into add/remove/modify-instruction proposals

Also read the instruction library if it exists:
- .claude/memory/meta/instruction-library.md

JIT Strategy: Fetch codebase files when needed, not upfront.
"""
)
```

## Display Result

Show Subagent summary to user:

### Evolve Audit:
- Memory Scorecard (memory health metrics)
- Alignment Scorecard (per-agent alignment, structural reliability, trends)
- Friction patterns detected
- Prompt diagnoses (for flagged underperforming agents)
- Proposed changes — rule, instruction library, and tiering (for user approval)
- Self-observations appended

## Notes

- Run on demand to improve harness effectiveness
- Proposed changes are NEVER applied automatically — user must approve
- Audits the rules themselves, not the project code
- Findings appended to `meta/self-observations.md`
- Now includes **graduation proposals** — conventions that can be promoted from markdown docs to deterministic linter enforcement
- After approving a `graduate-to-linter` proposal, run `/kiro:guardrails scaffold` to apply the linter rule change
- Graduation history tracked in `.claude/memory/meta/graduations.md`
- Now includes **alignment analysis** — computes per-agent alignment scores and structural reliability from trace.log
- Agents flagged `DIAGNOSE` or `FIX-FORMAT` get automatic prompt diagnosis with specific instruction change recommendations
- After approving instruction changes, run `/kiro:harness-test regression` to verify no regressions
