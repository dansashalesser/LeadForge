# Agent Tracing — Invocation Log for Observability

Every `Agent` tool call gets a trace entry, written automatically by `agent-trace-hook.sh` (PostToolUse, matcher `Agent`) — this is now the deterministic default; commands may still append their own richer entry (with `alignment`/`structural` fields the hook doesn't populate) if they can score the outcome. This data feeds the evolve agent for evidence-based harness improvements.

## Trace File

Location: `.claude/memory/trace.log`

## Format

One line per agent invocation:

```
YYYY-MM-DD HH:MM | agent-name | tier | outcome | duration-hint | alignment:N | structural:ok|malformed
```

**Fields**:
- `agent-name`: The agent file stem (e.g., `spec-impl`, `validate-design`)
- `tier`: Model tier used (`opus`, `sonnet`, `haiku`)
- `outcome`: `pass`, `fail`, `error`, `no-go`, or `go`
- `duration-hint`: `fast` (<30s), `medium` (30s-2min), `slow` (>2min) — estimated, not measured
- `alignment:N`: (optional) 0-5 alignment score — how well output matched expected outcome. See `alignment-scoring.md` for the rubric. Omit if the command cannot meaningfully score.
- `structural:ok|malformed`: (optional) Whether agent output followed the expected format. `ok` = correct structure, `malformed` = missing sections, wrong format, or unparseable.

The last two fields are optional for backward compatibility — older entries without them remain valid.

## Example

```
2026-03-30 14:22 | spec-impl | sonnet | pass | medium | alignment:4 | structural:ok
2026-03-30 14:25 | validate-design | sonnet | no-go | fast | alignment:5 | structural:ok
2026-03-30 14:30 | spec-refactor | opus | pass | fast
2026-03-30 14:35 | steering | haiku | pass | medium | alignment:3 | structural:malformed
```

## Rules

- Append only — the trace log is a sequential record
- One entry per agent invocation (not per sub-step within an agent)
- `agent-trace-hook.sh` writes one automatically for every agent spawn (5 fields, no `alignment`/`structural`); an orchestrating command may additionally append a richer entry when it can score the outcome — the agent itself never writes its own entry
- If the trace file exceeds 200 lines, the housekeeping agent archives older entries to `glacier/`

## How Evolve Uses This

The evolve agent reads `trace.log` to:
- Identify agents with high failure rates (candidates for prompt improvement)
- Track tier usage patterns (are expensive agents being over-used?)
- Correlate failures with specific workflow phases
- Measure harness usage frequency over time
- Compute mean alignment per agent (prompt quality metric — see `alignment-scoring.md`)
- Compute structural reliability rate per agent (format compliance)
- Detect alignment trends (improving/degrading over recent invocations)
- Generate data-driven tiering recommendations (promote/demote based on alignment at current tier)
