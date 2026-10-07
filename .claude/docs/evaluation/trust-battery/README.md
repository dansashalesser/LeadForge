# Trust Battery — Nightly Self-Improving Loop

> The harness's nightly scoring, reflection, and memory-gap detection system. Adapted from [@nityeshaga's trust-battery design](https://x.com/nityeshaga/status/2044864114682741134) (April 2026), scoped to a single-developer CLI harness.

## What It Is

A scheduled nightly pipeline that scores the harness's own recent behavior, converts the score into concrete memory changes, and surfaces unresolved drift to the developer. It closes the loop the harness was previously missing: **the harness has observations, patterns, and rules — but no wall-clock feedback that runs whether or not the developer remembers to invoke it**.

Without the battery loop, `/kiro:reflect` and `/kiro:housekeeping` run irregularly, the memory-gap signal is never captured, and the harness drifts. With the loop, every day ends with:

- a ±4.5% adjustment to a single **Trust Score** (observability only — it never gates harness behavior)
- a one-line `[judge]` observation summarising the day's charges and drains
- new memory entries converted from `[memory-gap]` signals the user generated that session
- a `[routine-alert]` in `observations.md` if any memory-gaps remain unresolved

### What problem it solves

The harness already had the right ingredients — an adversarial validator, a reflection loop, rule-encoded structural fixes, memory tiers — but every step was user-triggered. In practice, `/kiro:reflect` ran only when the developer remembered. The trust-battery loop supplies the **clock**.

The single sharpest insight from the source post: *every re-explained preference is a memory the agent should have saved but didn't*. The detector catches those in real-time; the Judge counts them as the flagship drain; the Reflector converts them into memories. That loop did not exist before.

## Origin and Attribution

Based on a post by [@nityeshaga](https://x.com/nityeshaga/status/2044864114682741134) (April 2026) describing a "trust battery" implementation inspired by Tobi Lütke's mental model at Shopify. The original design has per-human batteries, autonomy tiers unlocked by battery %, and a dashboard UI.

### What was adopted

- **Nightly Judge/Reflector separation** — the core insight: if one agent both scores and improves, it optimizes for score instead of work.
- **Structural fixes over memory patches** — when a memory fails, encode the rule structurally (this was already harness philosophy; the loop reinforces it).
- **Re-explanation as flagship drain** — the source's most practical signal.
- **±4.5%/day cap** — prevents single-day swings from dominating the score.

### What was deliberately left out

- **Per-person batteries** — the harness is single-developer per project. One scalar is enough; a graph of per-human relationships adds complexity with no audience.
- **Autonomy tiers unlocked by battery %** — conflicts with the harness's non-negotiable "every spec gate requires explicit human approval" property. Score is informational, never gates.
- **Dashboard UI** — the harness is CLI. A plain-text scoreboard at the top of `hot-memory.md` gives the same trend signal without building UI.

## Architecture

```
.claude/memory/
├── hot-memory.md                  # Scoreboard header line updated by trust_score.py
├── observations.md                # [memory-gap], [session-charge] (from detector), [judge], [routine-*] entries
└── trust-score.jsonl              # Append-only score history, one record per nightly run

.claude/scripts/
├── detect_reexplanation.py        # Detector — Haiku LLM; drain (re-explanation → [memory-gap]) and charge (approval → [session-charge])
└── trust_score.py                 # Score math, clamping, header rewrite

.claude/agents/kiro/
└── session-judge.md               # Haiku-tier adversarial scorer, emits verdict only

.claude/commands/kiro/
└── daily-maintenance.md           # Orchestrator: Judge → Reflect → Housekeeping → Session Quality → Keep Rate → Trust Score → Augment Skills

.claude/kiro/settings/rules/
└── session-quality-rubric.md      # Charges/drains rubric with evidence requirement

.claude/hooks/
└── stop-hook.sh                   # Runs detector after each session; appends [memory-gap] (drain) and [session-charge] (charge) obs
```

## The Four Components

### 1. Session Signal Detector — drains and charges

**File**: `scripts/session/detect_reexplanation.py`
**Trigger**: stop-hook (every session end), also callable from CI
**Model**: Claude Haiku (LLM-based, runs once per session)

Analyses the session's user turns using Haiku to identify two signal types in a single LLM pass:

**Drain signals** — user had to re-explain something the AI should have remembered, or expressed frustration at a repeated mistake. Catches both explicit ("I already told you", "stop doing that") and implicit signals ("you're still doing that thing I asked you to stop", "this is wrong again").

**Charge signals** — user gave unambiguous approval. Strong signals: "perfect", "exactly what I needed", "great work", "that works!". Excludes standalone "ok"/"yes", anything with "but" immediately following, or phrasing that implies partial approval.

Input modes:
- `--file PATH` — scan a plain-text file
- `--stdin` — scan stdin
- `--auto-transcript` — find most recent Claude Code `.jsonl` transcript for cwd and extract user turns

Output filter via `--mode`:
- `drain` (default) — emit drain signals as JSON array
- `charge` — emit charge signals as JSON array

The stop-hook runs both modes and appends at most **one** observation per type per calendar day — `[memory-gap]` for drains, `[session-charge]` for charges. Subsequent stops within the same day are no-ops for each type already recorded.

### 2. Session Judge — independent adversarial scorer

**File**: `agents/kiro/session-judge.md`
**Model**: Haiku (pattern matching, cheap pass)
**Trigger**: Step 1 of `/kiro:daily-maintenance`

Reads `observations.md` + `trace.log` for the last 24 hours and applies the rubric in `kiro/settings/rules/session-quality-rubric.md`. Emits a JSON verdict:

```json
{
  "window": "2026-04-20T00:00Z..2026-04-21T00:00Z",
  "positives": [{"tag": "root-cause-fix", "evidence": "observations.md 2026-04-20 [debug]", "weight": 1}],
  "negatives": [{"tag": "re-explanation", "evidence": "observations.md 2026-04-20 [memory-gap]", "weight": -2}],
  "score_delta": -1.0,
  "summary": "One sentence."
}
```

**Hard constraint on the Judge**: it proposes no fixes. The prompt is explicit — "if you catch yourself writing 'the harness should...', stop, delete it." This is the Judge/Reflector separation that Nityesh's design hinges on: a Judge that also improves would soften its scoring so the day looks better.

Idempotent: if a `[judge]` observation already exists for today, the Judge emits `score_delta: 0` with summary `"Already judged for this window"` and appends nothing new.

**Sample mode suspends that idempotency.** When the caller runs the Judge three times and tells it not to append, the duplicate-run guard does **not** apply: each run scores the window normally and appends nothing, and the caller writes one `[judge]` line covering all three. This is load-bearing. The guard exists to stop a rerun of the whole routine from double-scoring a day; applied to samples it would mean run 1 scores the day, runs 2 and 3 see run 1's entry and return `score_delta: 0`, and three independent draws collapse into `[-2, 0, 0]` with a median of `0`. That is not a measurement with variance removed — it is the first sample discarded and replaced with two fabricated zeros. Suppressing the append also keeps `trust_score.py auto-score` honest, since it counts tag occurrences and three appended `[judge]` lines would triple-count every tag.

### 3. Reflector — consumes the Judge's drains

**File**: `agents/kiro/reflect-agent.md` (existing; not changed)
**Trigger**: Step 2 of `/kiro:daily-maintenance`

The existing `/kiro:reflect` pipeline, now seeded with the Judge's drains list as priority input. Each `[memory-gap]` drain should produce either:
- a new memory entry (if the gap is real), or
- an explanation in `patterns.md` of why it is a known false positive (rare)

The orchestrator passes the Judge's verdict JSON directly into the reflect-agent's prompt so the reflector knows what to prioritize.

### 4. Trust Score — scoreboard math

**File**: `scripts/session/trust_score.py`
**State**: `.claude/memory/trust-score.jsonl`
**Trigger**: Step 4 of `/kiro:daily-maintenance`

```bash
# One --delta per judge run; the median is applied and the spread gates it:
python3 .claude/scripts/trust_score.py apply --delta -1.0 --delta -2.0 --delta -1.0 --summary "..."
# A single sample still works, and skips the spread gate:
python3 .claude/scripts/trust_score.py apply --delta -1.0 --summary "..."
python3 .claude/scripts/trust_score.py show
```

Behavior:
- Starts at **20%** on fresh install (matches the source's "AI hasn't earned trust yet" framing)
- Accepts **repeated `--delta`, one per judge run**, and reduces them with `consensus_delta()`
- Clamps the applied delta to `[-4.5, +4.5]` (matches the source's cap)
- Clamps the score to `[0, 100]`
- **Idempotent per calendar day**: if a record already exists for today, skips without writing
- Rewrites the `## Harness Trust Score:` line at the top of `hot-memory.md`
- Appends to `trust-score.jsonl` with raw delta, applied delta, and timestamp for the 7-day trend, plus the raw `samples`, the `spread`, and the `inconclusive` flag

Output arrows: ▲ (up), ▼ (down), ▬ (flat). 7-day delta is `null` until there are records older than 7 days.

#### Multi-sample verdicts — the spread gate

The judge is an LLM at temperature > 0, and until 2026-09-03 a single draw from it was committed straight into a cumulative score that nothing ever revisits. That made "the score fell 4 points" and "the judge sampled differently" the same observation, and nothing ever revisited a draw. `/kiro:daily-maintenance` now runs the judge three times and passes all three deltas here.

`consensus_delta(deltas)` returns `(delta, spread, inconclusive)`:

| Samples | Result |
|---|---|
| One | Passed through unchanged, `spread` is `null`, gate skipped. One sample has no spread; reporting `0.0` would read as perfect agreement on evidence that was never collected |
| Several, spread ≤ `JUDGE_SPREAD_LIMIT` | The **median**, so one outlier draw cannot move the score |
| Several, spread > `JUDGE_SPREAD_LIMIT` | `0.0`, and the day is recorded **inconclusive** |

`JUDGE_SPREAD_LIMIT` is **2.0** on the `[-4.5, +4.5]` scale: three runs landing within 2 points of each other are reading the same day, while a wider spread means the reading depends on which draw you looked at, and the honest delta for that day is `0.0` rather than whichever number the middle sample happened to be. An inconclusive day is stored distinguishably from a day nothing ran — it is the helper declining to commit a number it cannot stand behind, not a failure to fix.

Sources: Perrone, *What is Agentic Testing?* (`pass^k` over `pass@k` — a gate that greens on 1-of-3 is not a gate) and Visa's VVAH README (majority-vote false-positive filtering at temperature > 0). See `docs/sources/articles/README.md`.

## The Nightly Loop

`/kiro:daily-maintenance` runs five steps. Each is error-isolated — a bad Judge pass does not block housekeeping.

```
┌──────────────────────────────────────────────────────────────┐
│ Stop-hook during the day                                     │
│   └─ detect_reexplanation.py ──► [memory-gap] observation    │
│                                  (once per calendar day)     │
└──────────────────────────────────────────────────────────────┘
                             │
         (night, via local system scheduler / SessionStart hook)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ /kiro:daily-maintenance                                      │
│                                                              │
│  Pre-check:  .claude/memory/ exists?                         │
│              today's [judge] already written?  → exit        │
│                                                              │
│  Step 1 ── session-judge ×3 ──► 3 JSON verdicts             │
│             (concurrent, independent; caller writes ONE      │
│              [judge] obs covering all three)                 │
│                                                              │
│  Step 2 ── reflect-agent  ──► new memory / patterns          │
│             (seeded with Judge's drains)                     │
│                                                              │
│  Step 3 ── housekeeping-agent ──► pruned / archived memory   │
│                                                              │
│  Step 4 ── memory-gap check ──► [routine-alert]               │
│             (only if gaps remain after Step 2)               │
│                                                              │
│  Step 5 ── session-quality ──► [session-quality] obs         │
│                                                              │
│  Step 6 ── keep-rate ──► [keep-rate] obs                     │
│                                                              │
│  Step 7 ── trust_score.py apply --delta ×3 ──► scoreboard    │
│             (runs after steps 5 & 6; sees all signals;       │
│              median applied, spread >2.0 ⇒ inconclusive)     │
└──────────────────────────────────────────────────────────────┘
```

### Scheduling (OS-aware, auto-registered)

Auto-registered by `install.sh` on first install (and `update.sh` on existing installs). No manual setup needed. The platform-specific scheduler:

| OS | Scheduler | Force-recreate |
|---|---|---|
| **macOS** | launchd LaunchAgent | `bash $SDD_HARNESS/scripts/orchestration/setup-mac-orchestrator.sh --force` |
| **Linux** | crontab | `bash $SDD_HARNESS/scripts/orchestration/setup-linux-orchestrator.sh --force` |
| **WSL / Windows** | Windows Task Scheduler | `bash $SDD_HARNESS/scripts/orchestration/setup-global-orchestrator.sh --force` |

Each script ends with a **preflight** that proves the orchestrator can actually execute under its scheduler — registration succeeding says nothing about whether the job can run. macOS registers a throwaway probe LaunchAgent that runs `daily-orchestrator.sh --dry-run` in the same launchd context; Linux runs it under an approximated cron environment; both exit 1 if it comes back non-zero. WSL/Windows warns only. The preflight also runs when the job is already registered, since "already loaded" is exactly what a silently-dead job reports. On macOS, a harness under `~/Documents`, `~/Desktop` or `~/Downloads` is refused by TCC at exec time (exit 126) — the failure output names this and gives both fixes. The macOS LaunchAgent also runs the orchestrator under `caffeinate -i`, so the unattended 18:00 fire isn't cut short partway by idle or display sleep.

Fires at 18:00 local for all repos listed in `$SDD_HARNESS/projects.txt` — once daily on macOS/Linux; on WSL/Windows, Task Scheduler repeats every 4h after 18:00 (6x/day total, each sub-routine self-gating on its own last-run state so repeat fires are cheap no-ops). Each repo runs its own `daily-runner.sh`, maintaining its own memory, score history, and rubric application. A SessionStart hook provides catch-up: if the runner hasn't fired in >24h, opening any Claude session in the repo fires it silently in the background.

Opt out at install time:

```bash
SDD_SKIP_ROUTINE=1 $SDD_HARNESS/install.sh /path/to/project
```

Or after the fact (per platform, global): `launchctl unload -w ~/Library/LaunchAgents/com.sdd.daily-orchestrator.plist` (macOS), `crontab -l | grep -vF sdd-daily-orchestrator | crontab -` (Linux), `schtasks.exe /Delete /TN "SDD Daily Orchestrator" /F` (WSL). Per-repo: `rm .claude/scripts/daily-runner.sh`.

Note that the orchestrator also does one harness-level thing before it visits any repo: once per calendar day it runs `update.sh` to sync harness changes into every registered project (previously nothing ever ran `update.sh` on its own — the stop hook only nudged a human to). Opt out of that part alone with `SDD_SKIP_HARNESS_SYNC=1`, which leaves the per-repo maintenance runs untouched.

## Rubric at a Glance

See [`kiro/settings/rules/session-quality-rubric.md`](../../../kiro/settings/rules/session-quality-rubric.md) for the full rubric.

### Charges (+1 each, max 5 per day)

- Clean gate pass — a spec phase completed without rejection or redo
- Self-caught mistake — harness found and fixed before the user flagged it
- Reused existing utility — cited a pre-existing function/pattern
- Cited memory during work — referenced an observation, pattern, or hot-memory entry
- Root-cause fix — debugging observation describes *why*, not just *what*
- Rule obeyed under pressure — `decision` observation cites a rule file as the reason a shortcut was rejected

### Drains (-2 each, max 5 per day)

- **Re-explanation** (flagship) — `[memory-gap]` observation from the detector
- Silent failure — agent returned success when output was wrong
- Gate bypass — implementation shipped without passing `/kiro:verify`
- Rationalized rule-skip — harness argued its way out of a rule (see `anti-rationalization.md`)
- Stale context — acted on a fact that files/git already showed had changed
- Churn — 3+ redo cycles on the same artifact without a captured learning

Asymmetric weighting (−2 vs +1) matches the existing `/kiro:validate-adversarial` design: concerns carry double weight because missed drift is costlier than unnecessary caution.

## Trust Score ≠ Autonomy

This is non-negotiable. The Trust Score is **observability only**. It never gates harness behavior.

- High score does not unlock skipping spec phase gates
- Low score does not force extra approval steps
- The scoreboard exists to make drift *visible*, not to drive decisions

The harness's safety property — every spec gate requires explicit human approval — is preserved regardless of score. This is the single most important constraint: if a future contributor tries to add "at ≥75% skip this gate" logic, that is a bug, not a feature. The rule belongs in `kiro/settings/rules/session-quality-rubric.md` ("Judging Constraints" section) and should be enforced there.

## Troubleshooting

### The Routine fired but nothing changed

Check the guard conditions in order:

1. `ls .claude/memory/hot-memory.md` — does the project even have memory bootstrapped? If not, Step 0 of the orchestrator exits cleanly. Run `/kiro:reflect` once manually to bootstrap.
2. `grep "^- $(date +%Y-%m-%d) \[judge\]:" .claude/memory/observations.md` — has the pipeline already run today? If yes, it's idempotent and will no-op. Delete the `[judge]` line to re-enable (intentional escape hatch).
3. `cat .claude/memory/trust-score.jsonl | tail -1` — does the last record have today's date? If so, Step 4 skipped (same-day guard).

### Detector produces nothing even though I re-explained things

- The detector scans **user turns only** from the JSONL transcript, not assistant output.
- `~/.claude/projects/<encoded-cwd>/` must contain at least one `.jsonl` file. If the session ended abnormally, the transcript may not be flushed.
- Test the detector directly: `echo "I already told you to use the dev branch" | python3 .claude/scripts/detect_reexplanation.py --stdin`.

### Score is stuck at 20%

- The Judge scored 0 for each run. Causes: empty observations window, every finding lacked evidence (the rubric requires citations), or all observations are already tagged `[judge]` (idempotent skip).
- Check: `python3 .claude/scripts/trust_score.py show` — if `records > 0` but `score == 20`, the Judge is consistently emitting `score_delta: 0`. Inspect the rubric and recent observations.
- Check for **inconclusive days** in `trust-score.jsonl` before blaming the rubric: a record with `"inconclusive": true` applied `0.0` because the three judge samples spread past 2.0, which is a disagreement between draws rather than a genuinely flat day. Several in a row point at the rubric being ambiguous on the evidence available, not at the score being stuck.

### Routine not registered

Check per platform:
- **macOS**: `launchctl list com.sdd.daily-orchestrator` — listed? If not, re-run `update.sh` or run `bash $SDD_HARNESS/scripts/orchestration/setup-mac-orchestrator.sh --force`.
- **Linux**: `crontab -l | grep sdd-daily-orchestrator` — present? If not, re-run `update.sh` or run `bash $SDD_HARNESS/scripts/orchestration/setup-linux-orchestrator.sh --force`.
- **WSL**: `schtasks.exe /Query /TN "SDD Daily Orchestrator"` — present? If not, run `bash $SDD_HARNESS/scripts/orchestration/setup-global-orchestrator.sh --force` from WSL.
- `SDD_SKIP_ROUTINE=1` was set during install — run `update.sh` without it to retry.

### How do I reset a bad day?

Delete today's `[judge]` observation and the last line of `trust-score.jsonl`. The next Routine run will re-score.

```bash
sed -i "/^- $(date +%Y-%m-%d) \[judge\]:/d" .claude/memory/observations.md
sed -i '$ d' .claude/memory/trust-score.jsonl
```

## Future Considerations

The deliberate non-goals above are stable. If any of these come up in a future request, treat them as **scope expansions that require explicit design review**:

- **Multi-stakeholder batteries** would require observations to be tagged by author and the rubric to distinguish user-from-AI friction from user-from-user friction. The harness does not capture author metadata today.
- **Tiered autonomy** would require the spec workflow to accept a per-phase approval-level parameter. See the "Trust Score ≠ Autonomy" section above for the argument against.
- **Cross-repo score aggregation** would give one Trust Score across all installed projects. Not obviously useful — each repo has its own context and each project's score should stand alone.

## Related Documentation

- [`docs/memory/README.md`](../../memory/README.md) — the memory tier architecture the battery loop plugs into
- [`docs/harness-documentation/SDD-USAGE.md`](../../harness-documentation/SDD-USAGE.md#daily-maintenance-automated) — user-facing usage of `/kiro:daily-maintenance`
- [`docs/harness-documentation/SDD-SETUP-GUIDE.md`](../../harness-documentation/SDD-SETUP-GUIDE.md#automated-hooks) — hook and Routine registration flow
- [`kiro/settings/rules/session-quality-rubric.md`](../../../kiro/settings/rules/session-quality-rubric.md) — the full rubric the Judge applies
- [`kiro/settings/rules/anti-rationalization.md`](../../../kiro/settings/rules/anti-rationalization.md) — rule the "rationalized rule-skip" drain enforces

---

_Last synced: 2026-09-03_
