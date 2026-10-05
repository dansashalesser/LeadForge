#!/bin/bash
# Fires PreToolUse (mcp__raindrop__) — injects active observability best practices
# when any Raindrop Workshop tool is called.

cat << 'RULES'
╔══ Active Observability (Raindrop) ═══════════════════════════════════╗
║  Apply these patterns when working with traces:                      ║
╚══════════════════════════════════════════════════════════════════════╝

  1. BATCH FACETS — multiple analytical dimensions → one LLM call
     (trace tokens paid once, not once per dimension)

  2. FACET-FIRST — summarize trace in 1-2 sentences, embed the summary
     (raw million-token traces produce noisy clusters)

  3. CAP INPUT — preprocess to 128K tokens before any LLM analysis
     (walk spans, deduplicate messages, drop scorer/metric spans)

  4. NO-LLM CLASSIFY — at classification time use nearest-summary lookup
     (~100ms; no LLM call needed once topic map exists)

  5. LONG TAIL — don't sample aggressively; bugs live in rare clusters
     (HDBSCAN: no prespecified count, outliers → no_match not forced)

  Discovery before eval: run ~/.claude/skill-library/active-observability/SKILL.md first if you
  don't know what patterns to look for. Then raindrop-eval-loop.

RULES
