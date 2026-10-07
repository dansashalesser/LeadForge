#!/bin/bash
# Tool-failure review runner — promotes recurring tool failures into memory.
# Invoke from the repo's working directory:
#   cd <repo> && bash .claude/scripts/routines/tool-failure-review-runner.sh
#
# Invoked once per day by the daily orchestrator (per repo). The MIN_GAP_DAYS
# guard self-paces it to ~twice a week: it no-ops unless the last successful
# review was >= MIN_GAP_DAYS ago, AND there is at least one promotable entry in
# the ledger. Downtime-tolerant (next tick after the gap runs it); needs no
# separate scheduler. Race-safe via mkdir lock (portable; flock is Linux-only).
# Override cadence with TOOL_FAILURE_GAP_DAYS; force with TOOL_FAILURE_FORCE=1.

set -u

REPO_DIR="$(pwd)"
REPO_NAME="$(basename "$REPO_DIR")"
MEMORY_DIR=".claude/memory"
LEDGER="$MEMORY_DIR/tool-failures.jsonl"
COMMAND_FILE=".claude/commands/kiro/tool-failure-review.md"
STATE_FILE="$MEMORY_DIR/.last-tool-failure-review"
LOCK_DIR="$MEMORY_DIR/.tool-failure-review.lock"
MIN_GAP_DAYS="${TOOL_FAILURE_GAP_DAYS:-3}"
MIN_COUNT="${TOOL_FAILURE_MIN_COUNT:-3}"
TIMESTAMP="$(date -Iseconds)"
TODAY="$(date +%Y-%m-%d)"

log() { echo "[$TIMESTAMP] $REPO_NAME tool-failure-review: $*" >&2; }

# --- Guards ---
if [ ! -d "$MEMORY_DIR" ]; then
  log "memory-not-bootstrapped, skipping"
  exit 0
fi
if [ ! -f "$LEDGER" ]; then
  log "no ledger yet, skipping"
  exit 0
fi
if [ ! -f "$COMMAND_FILE" ]; then
  log "command file missing ($COMMAND_FILE), skipping"
  exit 0
fi

# --- Is there anything promotable? (count >= MIN_COUNT, open, not promoted) ---
PROMOTABLE="$(LEDGER="$LEDGER" MIN_COUNT="$MIN_COUNT" python3 - <<'PY' 2>/dev/null || echo 0
import json, os
led = os.environ["LEDGER"]; mc = int(os.environ["MIN_COUNT"])
n = 0
for line in open(led):
    line = line.strip()
    if not line:
        continue
    try:
        o = json.loads(line)
    except Exception:
        continue
    if o.get("count", 0) >= mc and o.get("status") == "open" and not o.get("promoted"):
        n += 1
print(n)
PY
)"
if [ "${PROMOTABLE:-0}" = "0" ]; then
  log "no promotable failures (>= ${MIN_COUNT} fails), skipping"
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
  log "another review active, skipping"
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

# --- Cadence guard: skip if last run was < MIN_GAP_DAYS ago (unless forced) ---
if [ "${TOOL_FAILURE_FORCE:-0}" != "1" ] && [ -s "$STATE_FILE" ]; then
  LAST_RAW="$(cat "$STATE_FILE")"
  LAST_EPOCH="$(date -j -f "%Y-%m-%dT%H:%M:%S%z" "$LAST_RAW" +%s 2>/dev/null \
              || date -d "$LAST_RAW" +%s 2>/dev/null || echo 0)"
  if [ "$LAST_EPOCH" -gt 0 ]; then
    GAP_DAYS=$(( ($(date +%s) - LAST_EPOCH) / 86400 ))
    if [ "$GAP_DAYS" -lt "$MIN_GAP_DAYS" ]; then
      log "last review ${GAP_DAYS}d ago (< ${MIN_GAP_DAYS}d), skipping"
      exit 0
    fi
  fi
fi

# --- Pre-flight: claude CLI must be available ---
if ! command -v claude >/dev/null 2>&1; then
  log "claude CLI not on PATH, aborting"
  exit 1
fi

# --- Mark started (only after pre-flight passes) ---
echo "$TIMESTAMP" > "$STATE_FILE"
log "starting review ($PROMOTABLE promotable)"

PROMPT="Read and follow the instructions in $COMMAND_FILE to review the accumulated tool-call failure ledger and promote recurring, understood failures into memory. Use min-count $MIN_COUNT. Today is $TODAY."

echo "$PROMPT" | SDD_HEADLESS=1 claude --print --output-format text --permission-mode bypassPermissions
EXIT=$?

log "completed exit=$EXIT"
exit $EXIT
