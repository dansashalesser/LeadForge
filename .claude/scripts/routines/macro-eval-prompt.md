You are running the twice-weekly MACRO-EVAL SWEEP for this repository. This invocation runs LOCALLY (not in Anthropic cloud), so you have access to:

- `~/.claude/skills/` via the Skill tool (including `evaluation/macro`)
- The full repo file tree (you are already in the repo's working directory)
- The Raindrop Workshop MCP tools (`mcp__raindrop__*`) — IF the server is authenticated in this context

Today's date: TODAY_PLACEHOLDER

## Task

Read `.claude/commands/kiro/macro-eval-sweep.md` and execute its pipeline end to end with default arguments (window = 4 days, all runs).

Critical rules:
1. **Preflight first.** If the Raindrop MCP server is unreachable (tool missing or query errors), do NOT fail silently — write `.claude/reports/macro-evals/TODAY_PLACEHOLDER-SKIPPED.md` explaining the skip, print one line saying it was skipped, and exit 0.
2. Follow the `evaluation/macro` skill's five-phase methodology exactly, including the Consistency Gap step (Step 4b in the sweep pipeline) for any task signature with ≥3 repeated runs in the window. Ground every finding in real `run_id`/`span_id` values from `query_traces`.
3. Write the dated report to `.claude/reports/macro-evals/TODAY_PLACEHOLDER.md` and post annotations back to Workshop for the top failing patterns (cap ~5 runs per pattern).
4. Keep context lean: prefer counts/IDs/SUBSTR previews; only pull full span payloads when a diagnosis truly needs them.

End with a 3-line summary: population size, top pattern + impact score, and the report file path.
