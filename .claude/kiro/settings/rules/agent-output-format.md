# Agent Output Format — Standardized Return Convention

All sub-agents should return results in this structured format. This keeps parent context clean and enables verification without consuming intermediate noise.

## Required Format

```
## Summary
[2-3 sentences describing what was done and the outcome]

## Changes Made
- [filepath:line] — description of change
- [filepath:line] — description of change
(or "None" if read-only task)

## Verification
- [what was tested and how]: pass / fail
(or "N/A" for research-only tasks)

## Issues Found
- [description with filepath:line reference]
(or "None")
```

## Trace Metadata

Include a `## Trace` section at the end of the return for the orchestrating command to log:

```
## Trace
- agent: [agent-name]
- outcome: [pass | fail | error | go | no-go]
```

The orchestrating command uses this to append to `.claude/memory/trace.log` (see `agent-tracing.md`).

## Rules

- **Be concise**: The parent agent needs decisions, not narratives. Target under 150 words.
- **Cite sources**: Always use `filepath:line` format so the parent can verify without re-reading entire files.
- **Summarized output only**: Distill tool output (test logs, grep results, file contents) into concise findings. Raw output stays in temp files if needed for reference.
- **Separate facts from opinions**: Clearly distinguish what you observed (fact) from what you recommend (opinion).
- **Flag blockers explicitly**: If you hit a blocker, lead with it at the top of the return.

## Output Recovery (for orchestrating commands)

When a sub-agent returns output that doesn't match the expected format, apply graduated recovery:

### Tier 1: Lenient Parsing
Extract what is usable from the response:
- Look for `## Summary`, `## Changes Made`, `## Verification`, `## Issues Found` headings even if out of order
- Accept partial format (e.g., summary present but verification missing)
- Treat unstructured prose as the Summary section

### Tier 2: Format Reminder
If Tier 1 yields insufficient information, re-invoke the agent with a format reminder appended to the prompt:
```
Your previous response was not in the expected format. Return results using this exact structure:
## Summary / ## Changes Made / ## Verification / ## Issues Found
```

### Tier 3: Simplified Request
If Tier 2 fails, request only the essential information:
```
Respond with only: (1) What you did, (2) Did it work (pass/fail), (3) Any blockers. One paragraph.
```

Recovery is best-effort. If all tiers fail, surface the raw response to the user with a note that format was unexpected.
