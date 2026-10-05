#!/bin/bash
# Daily briefing runner — synthesizes a prioritized status digest from
# .claude/memory/manager/{projects,people}.md plus whatever live sources are
# connected this session, and writes it to .claude/reports/daily-briefings/<date>.md.
# Invoke from the repo's working directory:
#   cd <repo> && bash .claude/scripts/routines/daily-briefing-runner.sh
#
# Invoked once per day by the daily orchestrator (per repo). MIN_GAP_DAYS=1 so it
# runs at most once per day and no-ops between runs. Downtime-tolerant. Race-safe
# via mkdir lock. Override cadence with DAILY_BRIEFING_GAP_DAYS; force a run with
# DAILY_BRIEFING_FORCE=1. Opt out with SDD_SKIP_DAILY_BRIEFING=1.
#
# Deterministic bootstrap guard: the ledger (.claude/memory/manager/) is created
# interactively via `/kiro:daily-briefing`, never by this headless runner — if
# neither projects.md nor people.md exists yet, skip without spending an LLM call
# rather than asking Claude to invent a briefing from nothing.

set -u

REPO_DIR="$(pwd)"
REPO_NAME="$(basename "$REPO_DIR")"
MEMORY_DIR=".claude/memory"
MANAGER_DIR="$MEMORY_DIR/manager"
REPORT_DIR=".claude/reports/daily-briefings"
STATE_FILE="$MEMORY_DIR/.last-daily-briefing-run"
LOCK_DIR="$MEMORY_DIR/.daily-briefing.lock"
PROMPT_TEMPLATE=".claude/scripts/routines/daily-briefing-prompt.md"
MIN_GAP_DAYS="${DAILY_BRIEFING_GAP_DAYS:-1}"
TIMESTAMP="$(date -Iseconds)"
TODAY="$(date +%Y-%m-%d)"

log() { echo "[$TIMESTAMP] $REPO_NAME daily-briefing: $*" >&2; }

# --- Guards ---
if [ ! -d "$MEMORY_DIR" ]; then
  log "memory-not-bootstrapped, skipping"
  exit 0
fi
if [ ! -f "$PROMPT_TEMPLATE" ]; then
  log "prompt-template-missing ($PROMPT_TEMPLATE), skipping"
  exit 1
fi

# --- Deterministic ledger-bootstrap guard (no LLM call) ---
if [ ! -f "$MANAGER_DIR/projects.md" ] && [ ! -f "$MANAGER_DIR/people.md" ]; then
  mkdir -p "$REPORT_DIR"
  cat > "$REPORT_DIR/$TODAY-SKIPPED.md" <<EOF
# Daily Briefing — $TODAY

Skipped: no \`projects.md\`/\`people.md\` yet under \`$MANAGER_DIR/\`.
Run \`/kiro:daily-briefing\` interactively once to bootstrap the ledger.
EOF
  log "ledger-not-bootstrapped, skipping (wrote $REPORT_DIR/$TODAY-SKIPPED.md)"
  exit 0
fi

# --- Race protection ---
# Stale lock: if >2h old it was left by a SIGKILL'd run — safe to remove.
if [ -d "$LOCK_DIR" ]; then
  LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK_DIR" 2>/dev/null || stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$LOCK_AGE" -gt 7200 ]; then
    log "removing stale lock (age=${LOCK_AGE}s)"
    rm -rf "$LOCK_DIR"
  fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "another briefing run active, skipping"
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

# --- Cadence guard ---
if [ "${DAILY_BRIEFING_FORCE:-0}" != "1" ] && [ -s "$STATE_FILE" ]; then
  LAST_RAW="$(cat "$STATE_FILE")"
  LAST_EPOCH="$(date -j -f "%Y-%m-%dT%H:%M:%S%z" "$LAST_RAW" +%s 2>/dev/null \
              || date -d "$LAST_RAW" +%s 2>/dev/null || echo 0)"
  if [ "$LAST_EPOCH" -gt 0 ]; then
    GAP_DAYS=$(( ($(date +%s) - LAST_EPOCH) / 86400 ))
    if [ "$GAP_DAYS" -lt "$MIN_GAP_DAYS" ]; then
      log "last briefing ${GAP_DAYS}d ago (< ${MIN_GAP_DAYS}d), skipping"
      exit 0
    fi
  fi
fi

# --- Pre-flight ---
if ! command -v claude >/dev/null 2>&1; then
  log "claude CLI not on PATH, aborting"
  exit 1
fi

mkdir -p "$REPORT_DIR"

# --- Substitute today's date into prompt ---
PROMPT="$(sed "s|TODAY_PLACEHOLDER|$TODAY|g" "$PROMPT_TEMPLATE")"

log "starting daily briefing"

# --- Invoke claude ---
# stdout is tee'd to a log file because the orchestrator wrapper that calls this
# script redirects our stdout to /dev/null and only captures stderr — without this,
# a failure's actual output (as opposed to permission-check noise on stderr) is
# unrecoverable after the fact.
echo "$PROMPT" | SDD_HEADLESS=1 claude --print --output-format text --permission-mode bypassPermissions \
  | tee "$MEMORY_DIR/.last-daily-briefing-output.log"
EXIT=${PIPESTATUS[0]}

# --- Mark done only on success — a failed run must NOT consume the daily
# cadence gate, or every retry until MIN_GAP_DAYS elapses silently no-ops. ---
if [ "$EXIT" -eq 0 ]; then
  echo "$TIMESTAMP" > "$STATE_FILE"
fi

log "completed exit=$EXIT"
exit $EXIT
