# Local Daily Maintenance Implementation Plan

> **MACHINE-SPECIFIC — do not copy commands verbatim.** This is a dated implementation record from one machine. Absolute paths below (`/home/dalesser/...`, `/mnt/c/dev/...`) reflect that environment; translate to your own `~/.claude/sdd-harness/` and project roots before running anything.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three auto-disabled CCR routines (Kiro Daily Maintenance, Session Quality Assessor, Keep Rate Evaluator) with a local Task-Scheduler-driven loop + session-start catch-up hook, and fix the never-installed trust-battery stop-hook wiring.

**Architecture:** Each installed repo gets a self-contained `.claude/scripts/daily-runner.sh` that runs the daily maintenance pipeline locally via `claude --print`. A thin global orchestrator at `~/.claude/sdd-harness/scripts/daily-orchestrator.sh` loops `projects.txt` and dispatches to each repo's runner. Windows Task Scheduler fires the orchestrator daily at 11:30 IST; a session-start hook fires the per-repo runner on Claude open if the timestamp file is stale (>24h).

**Tech Stack:** Bash, Python 3 (existing detector), Windows Task Scheduler (via `schtasks.exe` from WSL), `flock` for race protection.

**Spec:** [docs/superpowers/specs/2026-05-12-local-daily-maintenance-design.md](../specs/2026-05-12-local-daily-maintenance-design.md)

---

## File Structure

**Per-repo (template lives in `~/.claude/sdd-harness/`, copied per-install into `<project>/.claude/`):**
- `scripts/daily-runner.sh` — NEW. Runs this repo's daily maintenance pipeline. ~50 lines.
- `scripts/daily-maintenance-prompt.md` — NEW. Prompt body sent to `claude --print`. References `/kiro:daily-maintenance` and the `session-quality` + `keep-rate` skills by name.
- `hooks/session-start-hook.sh` — MODIFIED. Adds catch-up trigger block.
- `hooks/stop-hook.sh` — MODIFIED. Adds `detect_reexplanation.py` call (fixes trust-battery bug).
- `scripts/detect_reexplanation.py` — MODIFIED. Add `--emit observation` mode so stop-hook can write a one-line observation directly.

**Harness master only:**
- `scripts/daily-orchestrator.sh` — NEW. Reads `projects.txt`, dispatches per-repo runners. ~40 lines.
- `scripts/setup-global-orchestrator.sh` — NEW. One-time Windows Task Scheduler bootstrap. ~30 lines.
- `install.sh` — MODIFIED. Copies the two new per-repo scripts.

**Per-repo state (created at runtime, gitignored):**
- `.claude/memory/.last-routine-run` — ISO timestamp written by the runner.
- `.claude/memory/.last-routine-run.lock` — flock guard file.

---

### Task 1: Add `--emit observation` mode to `detect_reexplanation.py`

The stop-hook needs the detector to emit one observation line directly. Today the script emits JSON; the shell-side conversion is ugly. Adding a `--emit observation` flag keeps the wiring clean.

**Files:**
- Modify: `/home/dalesser/.claude/sdd-harness/scripts/detect_reexplanation.py`

- [ ] **Step 1: Read the current script to find the output section**

```bash
grep -n "json.dump\|print(json" /home/dalesser/.claude/sdd-harness/scripts/detect_reexplanation.py
```

Locate where it writes the JSON result. There should be a single `json.dump(...)` or `print(json.dumps(...))` call near the bottom.

- [ ] **Step 2: Add `--emit` argument and the observation branch**

In the `argparse` block, add:
```python
parser.add_argument(
    "--emit",
    choices=["json", "observation"],
    default="json",
    help="Output format. 'observation' writes a single [memory-gap] line to stdout suitable for appending to observations.md.",
)
```

After the existing detection logic produces the `hits` list (or whatever the variable is named), replace the JSON-only output with:

```python
if args.emit == "observation":
    if not hits:
        sys.exit(0)
    from datetime import date
    topics = ", ".join(
        dict.fromkeys(h["suggested_memory_topic"] for h in hits)
    )
    # Cap at ~80 chars to keep observations.md readable
    if len(topics) > 80:
        topics = topics[:77] + "..."
    print(
        f"- {date.today().isoformat()} [memory-gap]: "
        f"{len(hits)} re-explanation hit(s) — topics: {topics}"
    )
else:
    print(json.dumps(hits, indent=2))
```

- [ ] **Step 3: Smoke test the new mode**

```bash
echo "I already told you to use the dev branch. As I said, the dry-run flag is required." | \
  python3 /home/dalesser/.claude/sdd-harness/scripts/detect_reexplanation.py --stdin --emit observation
```

Expected output (date will be today's):
```
- 2026-05-12 [memory-gap]: 2 re-explanation hit(s) — topics: <some topics>
```

Test empty input:
```bash
echo "Hello world." | python3 /home/dalesser/.claude/sdd-harness/scripts/detect_reexplanation.py --stdin --emit observation
```
Expected: no output, exit 0.

Test JSON mode still works:
```bash
echo "I already told you" | python3 /home/dalesser/.claude/sdd-harness/scripts/detect_reexplanation.py --stdin
```
Expected: JSON array.

- [ ] **Step 4: Commit**

```bash
cd /home/dalesser/.claude/sdd-harness
git add scripts/detect_reexplanation.py
git commit -m "feat(trust-battery): add --emit observation mode to re-explanation detector"
```

---

### Task 2: Wire `detect_reexplanation.py` into `stop-hook.sh` template

This is the bugfix the trust-battery docs describe but the code never had.

**Files:**
- Modify: `/home/dalesser/.claude/sdd-harness/hooks/stop-hook.sh`

- [ ] **Step 1: Append the detector block to the stop-hook template**

Add at the end of `/home/dalesser/.claude/sdd-harness/hooks/stop-hook.sh`, after the existing memory health check block:

```bash
# --- Trust-battery: re-explanation detection ---
# Designed in docs/trust-battery/README.md but never installed until now.
# Appends a single [memory-gap] observation per calendar day (script handles idempotency).
DETECTOR=".claude/scripts/detect_reexplanation.py"
OBS_FILE=".claude/memory/observations.md"
if [ -x "$DETECTOR" ] && [ -f "$OBS_FILE" ]; then
  today=$(date +%Y-%m-%d)
  # Skip if today's [memory-gap] already exists
  if ! grep -q "^- $today \[memory-gap\]:" "$OBS_FILE" 2>/dev/null; then
    python3 "$DETECTOR" --auto-transcript --emit observation 2>/dev/null >> "$OBS_FILE" || true
  fi
fi
```

- [ ] **Step 2: Smoke test in a sandbox repo**

```bash
# Use an existing project as a temporary sandbox — pick the smallest one
SANDBOX="/mnt/c/dev/aiq-zora-agent-skills"
cd "$SANDBOX" || exit 1
# Manually create a fake transcript with a re-explanation phrase
mkdir -p "$HOME/.claude/projects/-mnt-c-dev-aiq-zora-agent-skills"
echo '{"type":"user","message":{"content":"I already told you to use the dev branch"}}' \
  > "$HOME/.claude/projects/-mnt-c-dev-aiq-zora-agent-skills/test-$(date +%s).jsonl"

# Make sure observations.md has no [memory-gap] for today already
grep "[memory-gap]" .claude/memory/observations.md | tail -3

# Run the new stop-hook template directly (not installed yet, so invoke from harness)
bash /home/dalesser/.claude/sdd-harness/hooks/stop-hook.sh

# Verify observation was appended
tail -3 .claude/memory/observations.md
```

Expected: A new line like `- 2026-05-12 [memory-gap]: 1 re-explanation hit(s) — topics: ...` appears in `observations.md`.

Clean up the test transcript:
```bash
rm "$HOME/.claude/projects/-mnt-c-dev-aiq-zora-agent-skills/test-"*.jsonl
```

- [ ] **Step 3: Test idempotency (same-day re-run is no-op)**

```bash
COUNT_BEFORE=$(grep -c "^- $(date +%Y-%m-%d) \[memory-gap\]:" .claude/memory/observations.md)
bash /home/dalesser/.claude/sdd-harness/hooks/stop-hook.sh
COUNT_AFTER=$(grep -c "^- $(date +%Y-%m-%d) \[memory-gap\]:" .claude/memory/observations.md)
echo "Before=$COUNT_BEFORE After=$COUNT_AFTER"
```

Expected: `Before=N After=N` (counts equal — no duplicate appended).

- [ ] **Step 4: Commit**

```bash
cd /home/dalesser/.claude/sdd-harness
git add hooks/stop-hook.sh
git commit -m "fix(trust-battery): wire detect_reexplanation.py into stop-hook (designed but never installed)"
```

---

### Task 3: Create `daily-maintenance-prompt.md` template

This is the prompt body the local runner sends to `claude --print`. Lives in the per-repo `.claude/scripts/` so each repo can customize it later if needed.

**Files:**
- Create: `/home/dalesser/.claude/sdd-harness/scripts/daily-maintenance-prompt.md`

- [ ] **Step 1: Write the prompt template**

Create `/home/dalesser/.claude/sdd-harness/scripts/daily-maintenance-prompt.md` with this exact content:

```markdown
You are running the local daily maintenance loop for this repository. This invocation runs LOCALLY (not in Anthropic cloud), so you have access to:

- `~/.claude/skills/` via the Skill tool
- The full repo file tree (you are already in the repo's working directory)
- All slash commands defined in `.claude/commands/`

Today's date: TODAY_PLACEHOLDER

Execute the three steps below in order. Each is error-isolated — if one step fails, log the failure and continue to the next.

## Step A — Daily Maintenance (trust-battery loop)

Read `.claude/commands/kiro/daily-maintenance.md` and execute its pipeline:
1. Judge — score the last 24h of observations using `kiro/settings/rules/session-quality-rubric.md`
2. Reflect — convert drains (especially [memory-gap] entries) into memory updates
3. Housekeep — archive observations.md if >50 entries
4. Trust Score — `python3 .claude/scripts/trust_score.py apply --delta <X> --summary "<one-line>"`
5. Alert — append `[routine-alert]` if any [memory-gap] entries remain unresolved after reflect

The pre-check at the top of daily-maintenance.md skips if today's `[judge]:` entry already exists. Respect that.

## Step B — Session Quality Assessment

Invoke the `session-quality` skill via the Skill tool. Apply its workflow:
- Collect today's git activity (commits, reverts, file rework counts)
- Score the session 1–5 based on charges vs drains
- Append a single `[session-quality]` observation to `.claude/memory/observations.md`

If today's `[session-quality]:` line already exists, skip silently.

## Step C — Keep Rate Evaluation

Invoke the `keep-rate` skill via the Skill tool. Apply its workflow:
- Find Claude-co-authored commits older than 7 days
- For each, compute lines added vs lines still in HEAD
- Append a single `[keep-rate]` observation with the overall %, trend, and any low-keep-rate flag

If today's `[keep-rate]:` line already exists, skip silently.

## Output

When all three steps are done, emit a single summary line on stdout:

```
Daily maintenance complete: judge=<delta> session-quality=<N/5> keep-rate=<N%>
```

If any step was skipped or failed, replace the value with `skipped` or `failed`.
```

The literal token `TODAY_PLACEHOLDER` will be substituted by the runner at execution time.

- [ ] **Step 2: Verify file**

```bash
cat /home/dalesser/.claude/sdd-harness/scripts/daily-maintenance-prompt.md | head -20
wc -l /home/dalesser/.claude/sdd-harness/scripts/daily-maintenance-prompt.md
```

Expected: file exists, ~40 lines, starts with "You are running the local daily maintenance loop".

- [ ] **Step 3: Commit**

```bash
cd /home/dalesser/.claude/sdd-harness
git add scripts/daily-maintenance-prompt.md
git commit -m "feat: add daily-maintenance-prompt template for local runner"
```

---

### Task 4: Create per-repo `daily-runner.sh` template

This script wraps the prompt + `claude --print` invocation with date-check, flock, and timestamp management. Lives at `~/.claude/sdd-harness/scripts/` as the template; install.sh copies it to each project's `.claude/scripts/`.

**Files:**
- Create: `/home/dalesser/.claude/sdd-harness/scripts/daily-runner.sh`

- [ ] **Step 1: Write the runner script**

Create `/home/dalesser/.claude/sdd-harness/scripts/daily-runner.sh`:

```bash
#!/bin/bash
# Local daily maintenance runner — one repo's daily loop.
# Designed to be invoked from this repo's working directory:
#   cd <repo> && bash .claude/scripts/daily-runner.sh
#
# Idempotent: writes today's date to .claude/memory/.last-routine-run at START;
# a second invocation on the same day exits in <1s.
# Race-safe: flock prevents two concurrent runners in the same repo.

set -u

REPO_DIR="$(pwd)"
REPO_NAME="$(basename "$REPO_DIR")"
MEMORY_DIR=".claude/memory"
STATE_FILE="$MEMORY_DIR/.last-routine-run"
LOCK_FILE="$MEMORY_DIR/.last-routine-run.lock"
PROMPT_TEMPLATE=".claude/scripts/daily-maintenance-prompt.md"
TIMESTAMP="$(date -Iseconds)"

log() {
  echo "[$TIMESTAMP] $REPO_NAME: $*" >&2
}

# --- Guards ---
if [ ! -d "$MEMORY_DIR" ]; then
  log "memory-not-bootstrapped, skipping"
  exit 0
fi

if [ ! -f "$PROMPT_TEMPLATE" ]; then
  log "prompt-template-missing ($PROMPT_TEMPLATE), skipping"
  exit 1
fi

mkdir -p "$MEMORY_DIR"
touch "$LOCK_FILE"

# --- Race protection ---
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  log "another runner active, skipping"
  exit 0
fi

# --- Date check (cheap short-circuit) ---
TODAY="$(date +%Y-%m-%d)"
if [ -f "$STATE_FILE" ]; then
  LAST_DAY="$(date -d "$(cat "$STATE_FILE")" +%Y-%m-%d 2>/dev/null || echo "")"
  if [ "$LAST_DAY" = "$TODAY" ]; then
    log "already ran today ($LAST_DAY), skipping"
    exit 0
  fi
fi

# --- Mark started ---
echo "$TIMESTAMP" > "$STATE_FILE"
log "starting daily maintenance"

# --- Substitute today's date into prompt and invoke claude --print ---
PROMPT="$(sed "s|TODAY_PLACEHOLDER|$TODAY|" "$PROMPT_TEMPLATE")"

if ! command -v claude >/dev/null 2>&1; then
  log "claude CLI not on PATH, aborting"
  exit 1
fi

echo "$PROMPT" | claude --print --output-format text
EXIT=$?

log "completed exit=$EXIT"
exit $EXIT
```

- [ ] **Step 2: Make it executable and smoke test**

```bash
chmod +x /home/dalesser/.claude/sdd-harness/scripts/daily-runner.sh

# Smoke test: invoke from a sandbox project without actually calling claude --print
# Temporarily replace the prompt template path so we can fake-out the claude call
SANDBOX="/mnt/c/dev/aiq-zora-agent-skills"
cd "$SANDBOX"
# Copy the runner into the sandbox just for this test (we'll do real installs in Task 9)
cp /home/dalesser/.claude/sdd-harness/scripts/daily-runner.sh .claude/scripts/
cp /home/dalesser/.claude/sdd-harness/scripts/daily-maintenance-prompt.md .claude/scripts/

# Reset state to test fresh run
rm -f .claude/memory/.last-routine-run .claude/memory/.last-routine-run.lock

# Run with claude --print stubbed — replace it temporarily
PATH_BACKUP="$PATH"
mkdir -p /tmp/claude-stub
cat > /tmp/claude-stub/claude <<'STUB'
#!/bin/bash
echo "stubbed-claude received prompt of length $(wc -c)"
exit 0
STUB
chmod +x /tmp/claude-stub/claude
export PATH="/tmp/claude-stub:$PATH"

bash .claude/scripts/daily-runner.sh

# Verify state file written
cat .claude/memory/.last-routine-run

# Restore PATH
export PATH="$PATH_BACKUP"
rm -rf /tmp/claude-stub
```

Expected:
- Log line `starting daily maintenance` then `completed exit=0` to stderr
- `.claude/memory/.last-routine-run` contains today's ISO timestamp
- `stubbed-claude received prompt of length N` printed (where N > 1000)

- [ ] **Step 3: Test the same-day no-op**

```bash
bash .claude/scripts/daily-runner.sh
```

Expected stderr: `already ran today (2026-05-12), skipping`. Exit 0 within ~1 second.

- [ ] **Step 4: Test flock**

```bash
# Reset to allow rerun
rm .claude/memory/.last-routine-run

# Hold the lock manually in another process, then try to run
(flock 200; sleep 10) 200>.claude/memory/.last-routine-run.lock &
LOCK_PID=$!
sleep 1
bash .claude/scripts/daily-runner.sh
kill $LOCK_PID 2>/dev/null
```

Expected stderr: `another runner active, skipping`. Exit 0.

- [ ] **Step 5: Clean up sandbox**

```bash
cd "$SANDBOX"
rm -f .claude/scripts/daily-runner.sh .claude/scripts/daily-maintenance-prompt.md
rm -f .claude/memory/.last-routine-run .claude/memory/.last-routine-run.lock
```

(Real installs happen in Task 9.)

- [ ] **Step 6: Commit**

```bash
cd /home/dalesser/.claude/sdd-harness
git add scripts/daily-runner.sh
git commit -m "feat: add daily-runner.sh template for local per-repo maintenance"
```

---

### Task 5: Add catch-up trigger to `session-start-hook.sh` template

When the user opens Claude in a repo, if `.last-routine-run` is absent or >24h old, fire the runner in the background. Doesn't block session start.

**Files:**
- Modify: `/home/dalesser/.claude/sdd-harness/hooks/session-start-hook.sh`

- [ ] **Step 1: Append the catch-up block**

Add at the end of `/home/dalesser/.claude/sdd-harness/hooks/session-start-hook.sh`:

```bash
# --- Daily maintenance catch-up ---
# If the daily runner hasn't fired in >24h (or .last-routine-run is absent),
# fire it now in the background. Doesn't block session start.
RUNNER=".claude/scripts/daily-runner.sh"
STATE_FILE=".claude/memory/.last-routine-run"

if [ -x "$RUNNER" ]; then
  should_run=0
  if [ ! -f "$STATE_FILE" ]; then
    should_run=1
  else
    last_epoch=$(date -d "$(cat "$STATE_FILE")" +%s 2>/dev/null || echo 0)
    now_epoch=$(date +%s)
    if [ $((now_epoch - last_epoch)) -gt 86400 ]; then
      should_run=1
    fi
  fi

  if [ "$should_run" = "1" ]; then
    echo "[SDD-MAINTENANCE-CATCHUP] Daily runner is stale (>24h or never ran). Firing in background."
    nohup bash "$RUNNER" > /dev/null 2>&1 &
    disown 2>/dev/null || true
  fi
fi
```

- [ ] **Step 2: Smoke test**

```bash
SANDBOX="/mnt/c/dev/aiq-zora-agent-skills"
cd "$SANDBOX"
# Install runner + prompt template into sandbox
cp /home/dalesser/.claude/sdd-harness/scripts/daily-runner.sh .claude/scripts/
cp /home/dalesser/.claude/sdd-harness/scripts/daily-maintenance-prompt.md .claude/scripts/
chmod +x .claude/scripts/daily-runner.sh

# Stale state: 2 days ago
mkdir -p .claude/memory
date -d '2 days ago' -Iseconds > .claude/memory/.last-routine-run

# Stub claude
mkdir -p /tmp/claude-stub
echo -e "#!/bin/bash\nsleep 1\nexit 0" > /tmp/claude-stub/claude
chmod +x /tmp/claude-stub/claude
PATH_BACKUP="$PATH"; export PATH="/tmp/claude-stub:$PATH"

# Run the hook
bash /home/dalesser/.claude/sdd-harness/hooks/session-start-hook.sh

# Hook should return immediately; the runner backgrounded
echo "hook returned at $(date +%s)"

# Wait for backgrounded runner
sleep 3
cat .claude/memory/.last-routine-run

export PATH="$PATH_BACKUP"
rm -rf /tmp/claude-stub
```

Expected:
- Hook prints `[SDD-MAINTENANCE-CATCHUP] Daily runner is stale ...` and returns within 1s
- After `sleep 3`, `.last-routine-run` contains today's date

- [ ] **Step 3: Test fresh-state (no .last-routine-run)**

```bash
cd "$SANDBOX"
rm -f .claude/memory/.last-routine-run
PATH_BACKUP="$PATH"; export PATH="/tmp/claude-stub:$PATH"
mkdir -p /tmp/claude-stub
echo -e "#!/bin/bash\nexit 0" > /tmp/claude-stub/claude
chmod +x /tmp/claude-stub/claude

bash /home/dalesser/.claude/sdd-harness/hooks/session-start-hook.sh
sleep 2
cat .claude/memory/.last-routine-run

export PATH="$PATH_BACKUP"; rm -rf /tmp/claude-stub
```

Expected: hook prints `Daily runner is stale (>24h or never ran)`, runner fires, state file gets today's date.

- [ ] **Step 4: Test fresh-today (no catch-up needed)**

```bash
cd "$SANDBOX"
date -Iseconds > .claude/memory/.last-routine-run

bash /home/dalesser/.claude/sdd-harness/hooks/session-start-hook.sh | grep -c "MAINTENANCE-CATCHUP" || echo "0"
```

Expected: `0` (hook did NOT print the catch-up message because state is fresh).

- [ ] **Step 5: Clean up sandbox**

```bash
cd "$SANDBOX"
rm -f .claude/scripts/daily-runner.sh .claude/scripts/daily-maintenance-prompt.md
rm -f .claude/memory/.last-routine-run .claude/memory/.last-routine-run.lock
```

- [ ] **Step 6: Commit**

```bash
cd /home/dalesser/.claude/sdd-harness
git add hooks/session-start-hook.sh
git commit -m "feat: add catch-up trigger to session-start-hook for stale daily runs"
```

---

### Task 6: Update `install.sh` to copy new per-repo scripts

`install.sh` already copies `scripts/` and `hooks/`. We need to confirm the new files land in each repo and that the runner is executable.

**Files:**
- Modify: `/home/dalesser/.claude/sdd-harness/install.sh`

- [ ] **Step 1: Inspect current copy block**

```bash
grep -n "cp -r\|cp " /home/dalesser/.claude/sdd-harness/install.sh
```

Currently line 52 has `cp -r "$HARNESS_DIR/scripts/" "$PROJECT_DIR/.claude/"` which already copies the new files (`daily-runner.sh`, `daily-maintenance-prompt.md`). The `chmod +x` block at lines 57-60 covers hooks only.

- [ ] **Step 2: Add chmod for daily-runner.sh**

After the existing hooks chmod block (around line 60 — find with `grep -n "chmod +x" install.sh`), add:

```bash
chmod +x "$PROJECT_DIR/.claude/scripts/daily-runner.sh"
```

- [ ] **Step 3: Add the local-maintenance setup hint at the end**

Find the existing "Nightly Maintenance Routine" reminder box (around line 154). Replace the entire box with:

```bash
# --- Remind user about local daily maintenance ---
PROJECT_NAME="$(basename "$PROJECT_DIR")"
if [ "${SDD_SKIP_ROUTINE:-0}" != "1" ]; then
  echo ""
  echo "  ┌─ Local Daily Maintenance ──────────────────────────────────────────────┐"
  echo "  │ This repo's daily runner is installed at:                              │"
  echo "  │   .claude/scripts/daily-runner.sh                                      │"
  echo "  │                                                                        │"
  echo "  │ It runs the daily-maintenance + session-quality + keep-rate pipeline.  │"
  echo "  │                                                                        │"
  echo "  │ Trigger options:                                                       │"
  echo "  │   1. Open Claude in this repo — session-start hook fires it if >24h    │"
  echo "  │      since last run.                                                   │"
  echo "  │   2. Windows Task Scheduler (one-time global setup):                   │"
  echo "  │        bash ~/.claude/sdd-harness/scripts/setup-global-orchestrator.sh │"
  echo "  │      This fires the orchestrator for ALL repos in projects.txt daily   │"
  echo "  │      at 11:30 IST.                                                     │"
  echo "  │                                                                        │"
  echo "  │ Disable per-repo: rm .claude/scripts/daily-runner.sh                   │"
  echo "  │ Disable globally: schtasks.exe /Delete /TN \"SDD Daily Orchestrator\"   │"
  echo "  └────────────────────────────────────────────────────────────────────────┘"
fi
```

- [ ] **Step 4: Verify install.sh syntax**

```bash
bash -n /home/dalesser/.claude/sdd-harness/install.sh && echo "syntax OK"
```

Expected: `syntax OK`.

- [ ] **Step 5: Dry-test install into a throwaway dir**

```bash
THROW="/tmp/install-test-$$"
mkdir -p "$THROW" && cd "$THROW" && git init -q
SDD_SKIP_ROUTINE=1 bash /home/dalesser/.claude/sdd-harness/install.sh "$THROW" 2>&1 | tail -20
ls -l "$THROW/.claude/scripts/daily-runner.sh" "$THROW/.claude/scripts/daily-maintenance-prompt.md" "$THROW/.claude/hooks/session-start-hook.sh" "$THROW/.claude/hooks/stop-hook.sh"
[ -x "$THROW/.claude/scripts/daily-runner.sh" ] && echo "runner is executable" || echo "runner NOT executable"
rm -rf "$THROW"
```

Expected: all four files exist, `runner is executable`.

- [ ] **Step 6: Commit**

```bash
cd /home/dalesser/.claude/sdd-harness
git add install.sh
git commit -m "feat: install.sh chmods daily-runner and prints local-maintenance setup hint"
```

---

### Task 7: Create the global `daily-orchestrator.sh`

The cross-repo loop. Lives only in the harness master location; not copied into projects.

**Files:**
- Create: `/home/dalesser/.claude/sdd-harness/scripts/daily-orchestrator.sh`

- [ ] **Step 1: Write the orchestrator**

Create `/home/dalesser/.claude/sdd-harness/scripts/daily-orchestrator.sh`:

```bash
#!/bin/bash
# Global daily maintenance orchestrator.
# Reads ~/.claude/sdd-harness/projects.txt and dispatches each repo's
# .claude/scripts/daily-runner.sh. Per-repo failures are isolated and logged;
# the loop never aborts midway.
#
# Usage:
#   daily-orchestrator.sh             — run all repos
#   daily-orchestrator.sh --dry-run   — print what would happen
#   daily-orchestrator.sh --repo PATH — run only the given repo

set -u

HARNESS_DIR="$HOME/.claude/sdd-harness"
PROJECTS_FILE="$HARNESS_DIR/projects.txt"
LOG_FILE="$HARNESS_DIR/logs/orchestrator.log"
mkdir -p "$(dirname "$LOG_FILE")"

DRY_RUN=false
SINGLE_REPO=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --repo)    shift; SINGLE_REPO="$1" ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

run_one() {
  local repo="$1"
  local ts="$(date -Iseconds)"

  if [ ! -d "$repo/.claude" ]; then
    echo "$ts [orphan] $repo — not installed" >> "$LOG_FILE"
    [ "$DRY_RUN" = true ] && echo "[orphan] $repo"
    return 0
  fi

  local state="$repo/.claude/memory/.last-routine-run"
  local last="never"
  [ -f "$state" ] && last="$(cat "$state")"

  if [ "$DRY_RUN" = true ]; then
    echo "[would-run] $repo (last=$last)"
    return 0
  fi

  local start=$(date +%s)
  (cd "$repo" && bash .claude/scripts/daily-runner.sh) > /dev/null 2>&1
  local exit_code=$?
  local duration=$(($(date +%s) - start))

  echo "$ts $repo exit=$exit_code duration=${duration}s" >> "$LOG_FILE"
}

if [ -n "$SINGLE_REPO" ]; then
  run_one "$SINGLE_REPO"
else
  if [ ! -f "$PROJECTS_FILE" ]; then
    echo "projects.txt missing at $PROJECTS_FILE" >&2
    exit 1
  fi
  while IFS= read -r repo; do
    [ -z "$repo" ] && continue
    [ "${repo:0:1}" = "#" ] && continue   # allow comments
    run_one "$repo"
  done < "$PROJECTS_FILE"
fi
```

- [ ] **Step 2: Make executable and dry-run test**

```bash
chmod +x /home/dalesser/.claude/sdd-harness/scripts/daily-orchestrator.sh
bash /home/dalesser/.claude/sdd-harness/scripts/daily-orchestrator.sh --dry-run
```

Expected output: one line per repo in `projects.txt`, like:
```
[would-run] /mnt/c/dev/aiq-zora-ai-engine (last=never)
[would-run] /home/dalesser/aiq-purina-salesorderintelligence-poc (last=never)
[would-run] /mnt/c/dev/aiq-zora-agent-skills (last=never)
```

- [ ] **Step 3: Test --repo single-repo mode**

```bash
bash /home/dalesser/.claude/sdd-harness/scripts/daily-orchestrator.sh --dry-run --repo "/mnt/c/dev/aiq-zora-agent-skills"
```

Expected: one line for that repo only.

- [ ] **Step 4: Test orphan detection**

```bash
echo "/tmp/does-not-exist" >> /home/dalesser/.claude/sdd-harness/projects.txt
bash /home/dalesser/.claude/sdd-harness/scripts/daily-orchestrator.sh --dry-run | grep "/tmp/does-not-exist"
# Clean up: remove the test line
sed -i '\|^/tmp/does-not-exist$|d' /home/dalesser/.claude/sdd-harness/projects.txt
```

Expected: `[orphan] /tmp/does-not-exist`.

- [ ] **Step 5: Commit**

```bash
cd /home/dalesser/.claude/sdd-harness
git add scripts/daily-orchestrator.sh
git commit -m "feat: add global daily-orchestrator.sh that loops projects.txt"
```

---

### Task 8: Create `setup-global-orchestrator.sh` (one-time Task Scheduler bootstrap)

Generates a Windows Task Scheduler XML and imports it. Idempotent — re-running updates the existing task.

**Files:**
- Create: `/home/dalesser/.claude/sdd-harness/scripts/setup-global-orchestrator.sh`

- [ ] **Step 1: Write the setup script**

Create `/home/dalesser/.claude/sdd-harness/scripts/setup-global-orchestrator.sh`:

```bash
#!/bin/bash
# One-time Windows Task Scheduler bootstrap for the SDD daily orchestrator.
# Creates a scheduled task "SDD Daily Orchestrator" that fires at 11:30 local
# every day. Uses "RunOnlyIfIdle=false" and "StartWhenAvailable=true" so the
# task runs as soon as possible after a missed start.
#
# Re-run this script to update the schedule or replace a broken task.

set -eu

TASK_NAME="SDD Daily Orchestrator"
WSL_DISTRO="${WSL_DISTRO_NAME:-Ubuntu}"
ORCHESTRATOR="$HOME/.claude/sdd-harness/scripts/daily-orchestrator.sh"
XML_PATH="/tmp/sdd-orchestrator-task.xml"

if [ ! -x "$ORCHESTRATOR" ]; then
  echo "ERROR: orchestrator not found or not executable at $ORCHESTRATOR" >&2
  exit 1
fi

if ! command -v schtasks.exe >/dev/null 2>&1; then
  echo "ERROR: schtasks.exe not on PATH. Are you in WSL?" >&2
  exit 1
fi

# Build the XML. Windows Task Scheduler XML is locale-sensitive; this template
# is the minimal portable form. The trigger uses local time.
cat > "$XML_PATH" <<XML
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>SDD harness daily maintenance — runs daily-orchestrator.sh inside WSL.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T11:30:00</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      <Enabled>true</Enabled>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>wsl.exe</Command>
      <Arguments>-d $WSL_DISTRO -- bash -lc "$ORCHESTRATOR"</Arguments>
    </Exec>
  </Actions>
</Task>
XML

# Convert to UTF-16 LE BOM (Windows Task Scheduler XML import requires this)
iconv -f UTF-8 -t UTF-16LE "$XML_PATH" > "$XML_PATH.utf16"
printf '\xFF\xFE' | cat - "$XML_PATH.utf16" > "$XML_PATH"
rm "$XML_PATH.utf16"

# Convert WSL path to Windows path for schtasks
WIN_XML_PATH="$(wslpath -w "$XML_PATH")"

# Delete existing task if present (idempotent)
schtasks.exe /Query /TN "$TASK_NAME" >/dev/null 2>&1 && \
  schtasks.exe /Delete /TN "$TASK_NAME" /F >/dev/null 2>&1

# Create from XML
if schtasks.exe /Create /TN "$TASK_NAME" /XML "$WIN_XML_PATH" /F >/dev/null 2>&1; then
  echo "✓ Task '$TASK_NAME' created. Daily run at 11:30 local time."
  echo "  Verify: schtasks.exe /Query /TN \"$TASK_NAME\" /V /FO LIST"
  echo "  Run now: schtasks.exe /Run /TN \"$TASK_NAME\""
  echo "  Delete:  schtasks.exe /Delete /TN \"$TASK_NAME\" /F"
else
  echo "✗ schtasks.exe /Create failed" >&2
  exit 1
fi

rm -f "$XML_PATH"
```

- [ ] **Step 2: Make executable and verify syntax**

```bash
chmod +x /home/dalesser/.claude/sdd-harness/scripts/setup-global-orchestrator.sh
bash -n /home/dalesser/.claude/sdd-harness/scripts/setup-global-orchestrator.sh && echo "syntax OK"
```

Expected: `syntax OK`.

Note: Actually running this script is **Task 10**, not now. We just want to confirm the script is syntactically valid.

- [ ] **Step 3: Commit**

```bash
cd /home/dalesser/.claude/sdd-harness
git add scripts/setup-global-orchestrator.sh
git commit -m "feat: add setup-global-orchestrator.sh for Windows Task Scheduler bootstrap"
```

---

### Task 9: Re-install harness into each repo in projects.txt

Apply the new templates (modified hooks, new scripts) to the three currently registered repos.

**Files:**
- No code changes; this task runs `install.sh` for each repo.

- [ ] **Step 1: Re-install to each repo**

```bash
HARNESS=/home/dalesser/.claude/sdd-harness
while IFS= read -r repo; do
  [ -z "$repo" ] && continue
  echo "=== Re-installing into: $repo ==="
  if [ -d "$repo/.git" ]; then
    SDD_SKIP_ROUTINE=1 bash "$HARNESS/install.sh" "$repo"
  else
    echo "  SKIP — not a git repo or path missing"
  fi
done < "$HARNESS/projects.txt"
```

Expected output: each repo gets the "SDD harness installed successfully" message and the new "Local Daily Maintenance" hint box.

- [ ] **Step 2: Verify the new files landed in each repo**

```bash
while IFS= read -r repo; do
  [ -z "$repo" ] && continue
  echo "=== $repo ==="
  [ -x "$repo/.claude/scripts/daily-runner.sh" ] && echo "  daily-runner.sh ✓" || echo "  daily-runner.sh ✗"
  [ -f "$repo/.claude/scripts/daily-maintenance-prompt.md" ] && echo "  prompt ✓" || echo "  prompt ✗"
  grep -q "detect_reexplanation" "$repo/.claude/hooks/stop-hook.sh" && echo "  stop-hook patched ✓" || echo "  stop-hook patched ✗"
  grep -q "MAINTENANCE-CATCHUP" "$repo/.claude/hooks/session-start-hook.sh" && echo "  session-start-hook patched ✓" || echo "  session-start-hook patched ✗"
done < /home/dalesser/.claude/sdd-harness/projects.txt
```

Expected: every check should print `✓` for every repo.

- [ ] **Step 3: No commit (this task only deploys; no harness changes to commit)**

The repos themselves have `.claude/` gitignored, so nothing to commit there either.

---

### Task 10: Bootstrap the Windows Task Scheduler entry and smoke-test end-to-end

- [ ] **Step 1: Run the setup script**

```bash
bash /home/dalesser/.claude/sdd-harness/scripts/setup-global-orchestrator.sh
```

Expected: `✓ Task 'SDD Daily Orchestrator' created. Daily run at 11:30 local time.`

If it fails with a Task Scheduler error, capture the full output and stop. Possible failures:
- `schtasks.exe` not on PATH → check WSL interop is enabled (`cat /etc/wsl.conf`)
- "Access is denied" → re-run from an elevated terminal (rare for InteractiveToken tasks)

- [ ] **Step 2: Verify the task exists in Windows**

```bash
schtasks.exe /Query /TN "SDD Daily Orchestrator" /V /FO LIST | head -30
```

Expected: task details shown — `Task To Run: wsl.exe -d <distro> -- bash -lc "...daily-orchestrator.sh"`, status `Ready`, schedule `Daily 11:30 AM`.

- [ ] **Step 3: Fire it manually to verify end-to-end**

```bash
schtasks.exe /Run /TN "SDD Daily Orchestrator"
# Wait a few seconds for it to start, then watch the log
sleep 5
tail -20 /home/dalesser/.claude/sdd-harness/logs/orchestrator.log
```

Expected: log lines like `2026-05-12T11:35:42+03:00 /mnt/c/dev/aiq-zora-ai-engine exit=0 duration=Ns` for each repo.

Note: this actually invokes `claude --print` against each repo. The first run will take ~30-60s per repo. Monitor with:

```bash
tail -f /home/dalesser/.claude/sdd-harness/logs/orchestrator.log
```

(Ctrl-C when each repo has logged.)

- [ ] **Step 4: Verify each repo got a real maintenance pass**

```bash
while IFS= read -r repo; do
  echo "=== $repo ==="
  echo "  last-routine-run: $(cat "$repo/.claude/memory/.last-routine-run" 2>/dev/null || echo 'missing')"
  echo "  today's [judge] obs:"
  grep "^- $(date +%Y-%m-%d) \[judge\]:" "$repo/.claude/memory/observations.md" | head -1
  echo "  today's [session-quality] obs:"
  grep "^- $(date +%Y-%m-%d) \[session-quality\]:" "$repo/.claude/memory/observations.md" | head -1
  echo "  today's [keep-rate] obs:"
  grep "^- $(date +%Y-%m-%d) \[keep-rate\]:" "$repo/.claude/memory/observations.md" | head -1
done < /home/dalesser/.claude/sdd-harness/projects.txt
```

Expected: each repo has `.last-routine-run` dated today plus three observation lines (one per step).

If any step is missing, check `orchestrator.log` for the exit code and inspect that repo's observations.md for partial output.

---

### Task 11: Delete the auto-disabled CCR routines

Now that the local loop is verified working, retire the cloud routines.

- [ ] **Step 1: List existing remote routines**

```bash
# In Claude Code, use the schedule tool. Document the routine IDs to delete:
# - Kiro Daily Maintenance (auto-disabled)
# - Session Quality Assessor (auto-disabled)
# - Keep Rate Evaluator (auto-disabled)
# - weekly-memory-consolidation-audit (paused — already documented as superseded)
```

Use the `schedule` skill or visit https://claude.ai/code/routines to view and delete each one through the UI. (No CLI command for this — manual step.)

- [ ] **Step 2: Document the deletion in memory**

Update `/home/dalesser/.claude/projects/-home-dalesser--claude-sdd-harness/memory/project_skillos_integrations.md` to note that the CCR routines have been replaced by the local loop. Specifically remove or annotate the references to:
- The three auto-disabled routines
- The "CCR runs remotely — not local ~/.claude/skills/" constraint (no longer relevant for these three)

- [ ] **Step 3: No git commit needed** — these are memory files outside the harness repo.

---

## Self-review notes

**Spec coverage check:** Every section of the spec maps to a task:
- Per-repo isolation → Tasks 3, 4, 5, 6
- Trust-battery stop-hook fix → Tasks 1, 2
- Daily-runner + orchestrator → Tasks 4, 7
- Task Scheduler bootstrap → Task 8, executed in Task 10
- Catch-up via session-start hook → Task 5
- Deploy to existing repos → Task 9
- Retire CCR routines → Task 11

**No placeholders.** All steps contain runnable commands or full code blocks.

**Type/name consistency check:** `daily-runner.sh`, `daily-orchestrator.sh`, `daily-maintenance-prompt.md`, `.last-routine-run`, `setup-global-orchestrator.sh`, `"SDD Daily Orchestrator"` (task name) — used identically across all tasks.

**Risk areas worth manual eyes during execution:**
- Task 8's Task Scheduler XML — locale-specific failures may occur; XML may need adjustment per Windows version. The Task 10 verification step catches this.
- Task 10's first end-to-end fire — claude --print may not expand slash commands the same way as interactive mode. If `/kiro:daily-maintenance` isn't resolved, the prompt fallback ("read .claude/commands/kiro/daily-maintenance.md and execute its pipeline") in Task 3's template handles it.
