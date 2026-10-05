<!-- L0: Distilled workflow rules — promoted from repeated observations -->

# Workflow Patterns

<!-- Edited in-place. Max 70 lines. Promote when 3+ observations cluster on same theme. -->
<!-- Each pattern: what the rule is, why it matters, when it applies. -->

## A Gate's Remedy Must Be Reachable From Where It Fires
When a hook denies a tool call, the suggested fix must be callable by the caller that was
denied. Three observations (2026-10-05) show this broken: subagents get lean-ctx's shell
allowlist and its 16KB `Read` deny but have no `ctx_*` tools to obey the remedy;
`ledger-append-only.sh` blocks Write/Edit on observations.md while `ctx_shell` blocks the
`>>` redirect that is then the only remaining path; a /tmp workaround for the read gate
became unreadable by every `ctx_*` tool ("path escapes project root"). Why it matters:
an unreachable remedy converts a guardrail into a dead end, and the escape route taken
under pressure is unreviewed (the scratchpad chunker scored high-slop, erosion 1.0).
When it applies: writing or reviewing any PreToolUse deny hook, and any time two
enforcement layers can fire on the same file. Check the remedy against the *least*
capable caller (subagent, no MCP), not the main session.

## Never Convert a Failed Probe Into a Value
A detector that times out, errors, or is blocked must emit a sentinel that suppresses
scoring — it must not fall through to 0.0, "no signals", or a templated stub. Four
observations (2026-10-04/05) cluster here: `[detector-down]` (`claude --print` timed out
at 120s) wrote nothing, so a window holding real spec work scored as idle; session-judge
logged pass,error,error yet reported "3 independent runs, all agreed, spread=0.0" —
errored runs counted as agreement; the stop-hook learnings fallback emitted
`applies_when: "a future run encounters a [judge]-tagged situation similar to this"`,
syntactically valid and informationally empty. Why it matters: a confident-looking zero
is worse than a missing reading, because downstream passes trust it and stop looking.
When it applies: any auto-scored metric, judge consensus, or deterministic memory
fallback. Distinguish "measured null" from "could not measure" in the written record.
