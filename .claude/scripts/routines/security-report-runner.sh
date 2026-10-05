#!/bin/bash
# Daily security report runner — scans the repo for security issues and writes
# a safety report to .claude/reports/security/<date>-security-report.md.
# Invoke from the repo's working directory:
#   cd <repo> && bash .claude/scripts/routines/security-report-runner.sh
#
# Invoked once per day by the daily orchestrator (per repo). MIN_GAP_DAYS=1 so
# it runs at most once per day and no-ops between runs. Downtime-tolerant.
# Race-safe via mkdir lock. Override cadence with SECURITY_REPORT_GAP_DAYS;
# force a run with SECURITY_REPORT_FORCE=1. Opt out with SDD_SKIP_SECURITY_REPORT=1.

set -u

REPO_DIR="$(pwd)"
REPO_NAME="$(basename "$REPO_DIR")"
MEMORY_DIR=".claude/memory"
REPORT_DIR=".claude/reports/security"
STATE_FILE="$MEMORY_DIR/.last-security-report-run"
LOCK_DIR="$MEMORY_DIR/.security-report.lock"
PROMPT_TEMPLATE=".claude/scripts/routines/security-report-prompt.md"
MIN_GAP_DAYS="${SECURITY_REPORT_GAP_DAYS:-1}"
TIMESTAMP="$(date -Iseconds)"
TODAY="$(date +%Y-%m-%d)"

log() { echo "[$TIMESTAMP] $REPO_NAME security-report: $*" >&2; }

# --- Guards ---
if [ ! -d "$MEMORY_DIR" ]; then
  log "memory-not-bootstrapped, skipping"
  exit 0
fi
if [ ! -f "$PROMPT_TEMPLATE" ]; then
  log "prompt-template-missing ($PROMPT_TEMPLATE), skipping"
  exit 1
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
  log "another scan active, skipping"
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

# --- Cadence guard ---
if [ "${SECURITY_REPORT_FORCE:-0}" != "1" ] && [ -s "$STATE_FILE" ]; then
  LAST_RAW="$(cat "$STATE_FILE")"
  LAST_EPOCH="$(date -j -f "%Y-%m-%dT%H:%M:%S%z" "$LAST_RAW" +%s 2>/dev/null \
              || date -d "$LAST_RAW" +%s 2>/dev/null || echo 0)"
  if [ "$LAST_EPOCH" -gt 0 ]; then
    GAP_DAYS=$(( ($(date +%s) - LAST_EPOCH) / 86400 ))
    if [ "$GAP_DAYS" -lt "$MIN_GAP_DAYS" ]; then
      log "last scan ${GAP_DAYS}d ago (< ${MIN_GAP_DAYS}d), skipping"
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

log "starting security scan"

# --- Invoke claude ---
# stdout is tee'd to a log file because the orchestrator wrapper that calls this
# script redirects our stdout to /dev/null and only captures stderr — without this,
# a failure's actual output (as opposed to permission-check noise on stderr) is
# unrecoverable after the fact.
echo "$PROMPT" | SDD_HEADLESS=1 claude --print --output-format text --permission-mode bypassPermissions \
  | tee "$MEMORY_DIR/.last-security-report-output.log"
EXIT=${PIPESTATUS[0]}

# --- Mark done only on success — a failed scan must NOT consume the daily
# cadence gate, or every retry until MIN_GAP_DAYS elapses silently no-ops. ---
if [ "$EXIT" -eq 0 ]; then
  echo "$TIMESTAMP" > "$STATE_FILE"
fi

log "completed exit=$EXIT"
exit $EXIT
