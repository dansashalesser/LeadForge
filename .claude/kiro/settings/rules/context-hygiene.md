# Context Hygiene — Preventing Context Rot

Context rot describes how model reasoning quality degrades as the context window fills with noise. These rules mitigate it.

## Success Silent, Failure Loud

- When running tests, linters, or builds: capture output to a temp file
- Check the exit code only — if success, report "pass" without reading the file
- Only read the output file when the exit code indicates failure
- Capture only failures and errors; success is confirmed by exit code alone

## Offload Large Outputs

- If a tool returns more than ~200 lines, write it to a file and read a summary instead
- Prefer targeted file reads (specific line ranges) over reading entire large files
- When searching, use specific patterns — avoid broad searches that return hundreds of matches

## Use Sub-Agents for Exploration

- Spawn sub-agents for exploratory work (codebase scanning, research, pattern discovery)
- Sub-agents isolate noisy intermediate results in their own context window
- Only the condensed result returns to the parent — keeping the parent context clean

## Checkpoint Long Sessions

- After completing each spec task, write a brief progress summary to the spec's `tasks.md`
- Before starting complex work, note the current state so it can be recovered if context is compacted
- If you notice reasoning quality degrading, proactively checkpoint to filesystem and suggest continuing in a fresh session

## Context Degradation Detection

Watch for these signals that context quality is declining:
- **Tool call repetition**: Same search or read repeated within a few turns
- **Contradictory statements**: Agent says X, then later says not-X without new information
- **Loss of prior decisions**: Agent re-asks questions already answered in conversation
- **Increasing vagueness**: Responses become generic instead of project-specific

**When degradation is detected**:
1. Checkpoint current progress to filesystem (tasks.md, hot-memory.md, or a temp summary file)
2. Report the checkpoint location to the user
3. Suggest: "Context quality is declining. Consider continuing in a fresh session — progress has been saved to [location]."

**Proactive checkpointing triggers**:
- After completing 5+ spec tasks in a single session
- After 3+ agent invocations without a natural pause
- When the conversation includes 10+ tool calls on a single topic
