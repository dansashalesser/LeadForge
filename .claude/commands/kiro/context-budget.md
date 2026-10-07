---
description: Analyze token consumption across steering, memory, and rules
allowed-tools: Read, Bash, Glob
argument-hint:
---

# Context Budget Analysis

This command measures the token footprint of all context files that are loaded or referenced during a session. No agent needed — this is mechanical file-size counting.

## Execution

### Step 1: Inventory Context Files

Use Glob and Bash to measure file sizes across these categories:

**Steering** (loaded at session start):
```bash
wc -w .claude/steering/*.md 2>/dev/null
```

**Memory — Hot** (loaded every session):
```bash
wc -w .claude/memory/hot-memory.md .claude/memory/meta/patterns.md .claude/memory/meta/self-observations.md 2>/dev/null
```

**Memory — Warm** (loaded on demand):
```bash
wc -w .claude/memory/observations.md .claude/memory/action-items.md .claude/memory/entities.md 2>/dev/null
```

**Rules** (loaded when agents are invoked):
```bash
wc -w .claude/kiro/settings/rules/*.md 2>/dev/null
```

**CLAUDE.md** (loaded at session start):
```bash
wc -w CLAUDE.md 2>/dev/null
```

### Step 2: Estimate Tokens

Use the approximation: **1 token ~ 0.75 words** (conservative estimate for English + code).

For each file: `tokens = ceil(word_count / 0.75)`

### Step 3: Generate Report

```
Context Budget Analysis
═══════════════════════════════════════════════════════

Category          Est. Tokens   Files
────────────────  ──────────    ─────────────────────────
Steering           {N}          {file list with individual counts}
Memory (hot)       {N}          {file list}
Memory (warm)      {N}          {file list}
Rules              {N}          {count} files
CLAUDE.md          {N}          CLAUDE.md
────────────────  ──────────
Total              {N} / 200,000 ({percentage}%)

Recommendations:
{generated based on thresholds below}
```

### Step 4: Generate Recommendations

Apply these thresholds:
- observations.md > 40 entries → "Consider `/kiro:housekeeping` to archive old entries"
- Any steering file > 2,000 tokens → "Consider trimming {file} — large steering files slow relevance filtering"
- Total > 30,000 tokens → "Context budget is high. Consider archiving cold memory or trimming verbose steering files"
- hot-memory.md > 800 tokens → "hot-memory.md exceeds recommended 50-line limit"
- patterns.md > 1,000 tokens → "patterns.md approaching 70-line limit — prune low-value entries"
- Total < 5,000 tokens → "Context is lean. No action needed."

If no recommendations apply: "Context budget is healthy. No action needed."
