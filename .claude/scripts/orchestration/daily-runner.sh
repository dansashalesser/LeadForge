#!/bin/bash
# Local daily maintenance runner — one repo's daily loop.
# Designed to be invoked from this repo's working directory:
#   cd <repo> && bash .claude/scripts/orchestration/daily-runner.sh
#
# Idempotent: writes today's date to .claude/memory/.last-routine-run at START;
# a second invocation on the same day exits in <1s.
# Race-safe: mkdir-based lock (portable; flock is Linux-only).

set -u

REPO_DIR="$(pwd)"
REPO_NAME="$(basename "$REPO_DIR")"
MEMORY_DIR=".claude/memory"
STATE_FILE="$MEMORY_DIR/.last-routine-run"
LOCK_DIR="$MEMORY_DIR/.runner.lock"
PROMPT_TEMPLATE=".claude/scripts/routines/daily-maintenance-prompt.md"
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

# --- Race protection (mkdir is atomic; works on macOS and Linux) ---
# Stale lock: if >2h old it was left by a SIGKILL'd run — safe to remove.
if [ -d "$LOCK_DIR" ]; then
  # mtime epoch: GNU stat (-c %Y) → BSD/macOS stat (-f %m) → 0. Portable across OSes.
  LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK_DIR" 2>/dev/null || stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$LOCK_AGE" -gt 7200 ]; then
    log "removing stale lock (age=${LOCK_AGE}s)"
    rm -rf "$LOCK_DIR"
  fi
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "another runner active, skipping"
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM

# --- Date check (cheap short-circuit) ---
# Extract YYYY-MM-DD prefix from ISO timestamp — portable, no GNU date needed.
TODAY="$(date +%Y-%m-%d)"
if [ -s "$STATE_FILE" ]; then
  LAST_DAY="$(cut -dT -f1 "$STATE_FILE" 2>/dev/null || echo "")"
  if [ "$LAST_DAY" = "$TODAY" ]; then
    log "already ran today ($LAST_DAY), skipping"
    exit 0
  fi
fi

# --- Pre-flight: claude CLI must be available ---
if ! command -v claude >/dev/null 2>&1; then
  log "claude CLI not on PATH, aborting"
  exit 1
fi

# --- Substitute today's date into prompt ---
PROMPT="$(sed "s|TODAY_PLACEHOLDER|$TODAY|" "$PROMPT_TEMPLATE")"

# --- Sync mandatory tools to all repos ---
SYNC_SCRIPT="$HOME/.claude/scripts/sync-mandatory-tools.sh"
if [ -x "$SYNC_SCRIPT" ]; then
  log "syncing mandatory tools to repos"
  bash "$SYNC_SCRIPT" 2>&1 | while IFS= read -r line; do log "$line"; done
fi

# --- Mark started (commits us to today; do this only after pre-flight passes) ---
echo "$TIMESTAMP" > "$STATE_FILE"
log "starting daily maintenance"

# --- Invoke claude (capture output for the optional channel summary) ---
OUTPUT_FILE="$(mktemp 2>/dev/null || echo "$MEMORY_DIR/.daily-runner.out")"
echo "$PROMPT" | SDD_HEADLESS=1 claude --print --output-format text --permission-mode bypassPermissions | tee "$OUTPUT_FILE"
EXIT=${PIPESTATUS[1]}

# --- Optional: post a summary to chat channels ---
# No-op unless ~/.env.channels exists (so callers run unconditionally).
# Opt out with SDD_SKIP_CHANNEL_NOTIFY=1.
NOTIFY=".claude/scripts/integrations/channels/notify.py"
if [ "${SDD_SKIP_CHANNEL_NOTIFY:-0}" != "1" ] && [ -f "$HOME/.env.channels" ] \
   && [ -f "$NOTIFY" ] && command -v python3 >/dev/null 2>&1; then
  SUMMARY="$(tail -n 20 "$OUTPUT_FILE" 2>/dev/null)"
  python3 "$NOTIFY" --title "Daily maintenance — $REPO_NAME (exit=$EXIT)" "$SUMMARY" \
    >/dev/null 2>&1 || log "channel notify failed"
fi
rm -f "$OUTPUT_FILE"

log "completed exit=$EXIT"
exit $EXIT
