#!/bin/bash
# Fires PreToolUse (save_observation) — injects compiled-truth structure guidance
# before every memory write so observations follow the two-zone pattern.

cat << 'RULES'
╔══ Compiled Truth Pattern ══════════════════════════════════════════╗
║  You are about to write a memory observation.                     ║
╚═══════════════════════════════════════════════════════════════════╝

STRUCTURE — two zones, different rules for each:
  ── STATE (top) ─────────────────────────────────────────────────
  Current synthesis. Rewrite in place when new evidence arrives.
  Always reflects best understanding. No stale facts.
  Every fact needs: [Source: type, context, YYYY-MM-DD]

  ── EVIDENCE / TIMELINE (bottom) ────────────────────────────────
  Append-only dated log. Never edit past entries.
  Format: YYYY-MM-DD | event or finding [Source: ...]

SOURCE PRECEDENCE (highest → lowest):
  1. User's direct statements
  2. Compiled truth from prior observations
  3. Timeline entries (raw evidence)
  4. External sources

UPDATING — when new evidence arrives:
  ✓ Rewrite the State section to incorporate the new truth
  ✓ Append a new entry to the Timeline
  ✗ Never append stale facts to State — rewrite it entirely
RULES
