---
description: Review the tool-failure ledger — diagnose why recurring Bash/MCP calls keep failing and promote the durable lessons into memory so they stop happening.
allowed-tools: Skill, Read, Write, Edit, Bash, Glob, Grep
argument-hint: "[min-count]   (default 3 — only review signatures that failed this many times)"
---

# Tool-Failure Review

Turn the per-repo tool-failure ledger into durable memory. This is the **review** step of the tool-failure-memory loop (capture → recall → **review**): recurring failures whose cause is understood graduate from the ephemeral ledger into a memory file that explains *why* and how to avoid them.

Invoke `Skill("tool-failure-memory")` first for the signature model and remedy/promotion mechanics.

## Step 0 — Preflight

```bash
LED=.claude/memory/tool-failures.jsonl
[ -f "$LED" ] || { echo "no ledger — nothing to review"; exit 0; }
```

If the ledger is missing or empty, write nothing and stop. Never invent failures.

## Step 1 — Select promotable signatures

Read `.claude/memory/tool-failures.jsonl`. `$ARGUMENTS` may set `[min-count]` (default **3**). Select entries where:

- `count >= min-count`, AND
- `promoted == false` (or absent), AND
- `status == "open"`.

These are the failures that recur often enough to be worth a durable lesson. If none qualify, write a one-line "nothing promotable this cycle" report (Step 4) and stop.

## Step 2 — Diagnose each (why does this shape keep failing?)

For each selected entry, reason from its `sig`, `samples`, and `last_error` to a **root cause**, not a restatement of the error. Use repo evidence — read the relevant config/test/build files, check for a venv/lockfile/missing dep, grep for the failing command in scripts. Produce, per entry:

- **Cause** — the underlying reason (e.g. "deps live in a uv venv; bare `pytest` can't see them").
- **Remedy** — the concrete avoidance (e.g. "run `uv run pytest`").
- **Reusable?** — does the lesson help a *different* future task, or is it a one-off? Apply the memory transfer test. One-offs are NOT promoted.

## Step 3 — Promote the reusable ones to memory

For each reusable lesson:

1. Write/update a memory file under `.claude/memory/` (type `feedback` or `project`), structured as cause → **How to apply** → remedy. Follow the memory-discipline rules (the PreToolUse gate will show them).
2. Add a one-line pointer to `MEMORY.md`.
3. If the lesson belongs in `ERRORS.md` (took 2+ attempts — most do), append a concise entry there too: what failed, what worked, why.
4. Mark the ledger entry resolved + promoted, and store the remedy on it so future recall is actionable:

```bash
python3 - "$LED" <<'PY'
import json, sys
LED = sys.argv[1]
# Fill SIG→REMEDY for each promoted signature this run:
PROMOTED = {
    # "pytest <path> -x": "Run via uv run pytest (deps live in the uv venv).",
}
rows = [json.loads(l) for l in open(LED) if l.strip()]
for r in rows:
    if r["sig"] in PROMOTED:
        r["remedy"] = PROMOTED[r["sig"]]
        r["status"] = "resolved"
        r["promoted"] = True
open(LED, "w").write("".join(json.dumps(x) + "\n" for x in rows))
PY
```

Leave non-reusable / unclear entries `open` and unpromoted — they will resurface next cycle (or age out of recall after 45 days).

## Step 4 — Report

Write `.claude/reports/tool-failures/<YYYY-MM-DD>.md`:

- Signatures reviewed, promoted (with the memory file each became), and skipped (with why).
- If nothing qualified: one line saying so.

Keep it short. The durable output is the memory files, not the report.

## Done

End with: how many failures were promoted, which memory files were written/updated, and any signatures left open for next cycle.
