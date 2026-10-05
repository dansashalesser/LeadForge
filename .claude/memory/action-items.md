<!-- L0: Cross-session TODOs with priority and due dates -->

# Action Items

## Active
<!-- Format: - [ ] task description | due:YYYY-MM-DD | pri:high/medium/low | added:YYYY-MM-DD -->
- [ ] Split specs/lead-source-adapters requirements.md (67KB) + design.md (76KB) into sub-16KB files before running spec-tasks | due:2026-10-07 | pri:high | added:2026-10-05
- [ ] Add a subagent-aware remedy to .claude/hooks/lean-ctx-nudge-hook.sh — it denies Read >=16KB and tells the caller to use ctx_read, which subagents cannot call | due:2026-10-08 | pri:high | added:2026-10-05
- [ ] Make failed/timed-out detectors emit a suppress-scoring sentinel instead of 0.0 (session-judge counted errored runs as agreement; claude --print 120s timeout wrote nothing) | due:2026-10-10 | pri:medium | added:2026-10-05
- [ ] Fix stop-hook.sh learnings promoter (line ~161): it writes applies_when stubs like "a situation similar to this" that no future run can match, defeating the learning loop | due:2026-10-12 | pri:medium | added:2026-10-05
- [ ] Stop hooks from auto-appending probe non-zero exits to observations.md as [seed-target:*] entries — a jq probe exiting 1 is an answer, and these consume the 50-entry cap | due:2026-10-12 | pri:low | added:2026-10-05

## Completed
<!-- Format: - [x] task description | done:YYYY-MM-DD | added:YYYY-MM-DD -->
<!-- Archive completed items to glacier/ when >10 entries -->
