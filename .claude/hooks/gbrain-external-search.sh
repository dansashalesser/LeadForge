#!/bin/bash
# Fires PreToolUse (WebFetch|WebSearch) — reminds to check claude-mem first
# before reaching for external APIs.

cat << 'RULES'
╔══ Memory-First Check ══════════════════════════════════════════════╗
║  BEFORE this external call: did you search claude-mem?            ║
╚═══════════════════════════════════════════════════════════════════╝

  1. mcp__plugin_claude-mem_mcp-search__search  ← fast, zero cost
  2. semantic search if keyword is thin
  3. get_observations for any hits found
  4. External APIs only if memory returns nothing useful

Skip memory → proceed.   Memory has the answer → use it instead.
RULES
