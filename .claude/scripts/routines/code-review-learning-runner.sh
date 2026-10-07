#!/bin/bash
# Self-improving code-review learning runner — compares pr-babysit's logged reviews
# against real human review activity on merged PRs, promotes low-risk findings into
# memory, and reports higher-risk methodology changes for human approval.
# Invoke from the repo's working directory:
#   cd <repo> && bash .claude/scripts/routines/code-review-learning-runner.sh
#
# Invoked once per day by the daily orchestrator (per repo). Cheaply no-ops unless
# there is at least one merged PR with a pr-babysit review log not yet processed.
# Self-paces to weekly (MIN_GAP_DAYS=7) once there IS something to process.
# Race-safe via mkdir lock (portable; flock is Linux-only). Applies to any repo with
# a pr-reviews log, not just the harness. Override cadence with
# CODE_REVIEW_LEARNING_GAP_DAYS; force a run with CODE_REVIEW_LEARNING_FORCE=1.
# Opt out with SDD_SKIP_CODE_REVIEW_LEARNING=1 (checked by the caller, not here).

set -u

REPO_DIR="$(pwd)"
REPO_NAME="$(basename "$REPO_DIR")"
MEMORY_DIR=".claude/memory"
REVIEW_DIR="$MEMORY_DIR/pr-reviews"
PROCESSED_LEDGER="$MEMORY_DIR/.code-review-learning-processed"
PROMPT_TEMPLATE=".claude/scripts/routines/code-review-learning-prompt.md"
STATE_FILE="$MEMORY_DIR/.last-code-review-learning-run"
LOCK_DIR="$MEMORY_DIR/.code-review-learning.lock"
MIN_GAP_DAYS="${CODE_REVIEW_LEARNING_GAP_DAYS:-7}"
TIMESTAMP="$(date -Iseconds)"
TODAY="$(date +%Y-%m-%d)"

log() { echo "[$TIMESTAMP] $REPO_NAME code-review-learning: $*" >&2; }

# --- Guards ---
if [ ! -d "$MEMORY_DIR" ]; then
  log "memory-not-bootstrapped, skipping"
  exit 0
fi
if [ ! -d "$REVIEW_DIR" ]; then
  log "no pr-reviews logged yet, skipping"
  exit 0
fi
if [ ! -f "$PROMPT_TEMPLATE" ]; then
  log "prompt template missing ($PROMPT_TEMPLATE), skipping"
  exit 0
fi
if ! command -v gh >/dev/null 2>&1; then
  log "gh CLI not found, skipping"
  exit 0
fi
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "not a git repo, skipping"
  exit 0
fi

# --- Discover promotable PRs: merged, logged, not yet processed ---
PROMOTABLE_PRS="$(REVIEW_DIR="$REVIEW_DIR" PROCESSED_LEDGER="$PROCESSED_LEDGER" python3 - <<'PY' 2>/dev/null
import os, re, subprocess, json

review_dir = os.environ["REVIEW_DIR"]
ledger_path = os.environ["PROCESSED_LEDGER"]

processed = set()
if os.path.isfile(ledger_path):
    with open(ledger_path) as f:
        processed = {line.strip() for line in f if line.strip()}

candidates = []
for name in os.listdir(review_dir):
    m = re.match(r"pr-(\d+)\.md$", name)
    if m:
        candidates.append(m.group(1))

promotable = []
for n in candidates:
    if n in processed:
        continue
    try:
        out = subprocess.run(
            ["gh", "pr", "view", n, "--json", "state"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            continue
        data = json.loads(out.stdout)
        if data.get("state") == "MERGED":
            promotable.append(n)
    except Exception:
        continue

print(",".join(promotable))
PY
)"

if [ -z "${PROMOTABLE_PRS:-}" ]; then
  log "no promotable merged+logged PRs, skipping"
  exit 0
fi

# --- Race protection (mkdir is atomic on macOS and Linux) ---
# Stale lock: if >2h old it was left by a SIGKILL'd run — safe to remove.
if [ -d "$LOCK_DIR" ]; then
  LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK_DIR" 2>/dev/null || stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$LOCK_AGE" -gt 7200 ]; then
    log "removing stale lock (age=${LOCK_AGE}s)"
    rm -rf "$LOCK_DIR"
  fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "another sweep active, skipping"
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

# --- Cadence guard: skip if last run was < MIN_GAP_DAYS ago (unless forced) ---
if [ "${CODE_REVIEW_LEARNING_FORCE:-0}" != "1" ] && [ -s "$STATE_FILE" ]; then
  LAST_RAW="$(cat "$STATE_FILE")"
  LAST_EPOCH="$(date -j -f "%Y-%m-%dT%H:%M:%S%z" "$LAST_RAW" +%s 2>/dev/null \
              || date -d "$LAST_RAW" +%s 2>/dev/null || echo 0)"
  if [ "$LAST_EPOCH" -gt 0 ]; then
    GAP_DAYS=$(( ($(date +%s) - LAST_EPOCH) / 86400 ))
    if [ "$GAP_DAYS" -lt "$MIN_GAP_DAYS" ]; then
      log "last sweep ${GAP_DAYS}d ago (< ${MIN_GAP_DAYS}d), skipping"
      exit 0
    fi
  fi
fi

# --- Pre-flight: claude CLI must be available ---
if ! command -v claude >/dev/null 2>&1; then
  log "claude CLI not on PATH, aborting"
  exit 1
fi

# --- Substitute placeholders into prompt ---
PROMPT="$(sed -e "s|TODAY_PLACEHOLDER|$TODAY|g" -e "s|PROMOTABLE_PRS_PLACEHOLDER|$PROMOTABLE_PRS|g" "$PROMPT_TEMPLATE")"

# --- Mark started ---
echo "$TIMESTAMP" > "$STATE_FILE"
log "starting sweep (PRs: $PROMOTABLE_PRS)"

# --- Invoke claude ---
echo "$PROMPT" | SDD_HEADLESS=1 claude --print --output-format text --permission-mode bypassPermissions
EXIT=$?

if [ "$EXIT" -eq 0 ]; then
  printf '%s\n' "${PROMOTABLE_PRS//,/$'\n'}" >> "$PROCESSED_LEDGER"
fi

log "completed exit=$EXIT"
exit $EXIT
