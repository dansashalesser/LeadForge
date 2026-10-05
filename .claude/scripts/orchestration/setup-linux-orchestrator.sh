#!/bin/bash
# Registers the SDD daily orchestrator via crontab on Linux (non-WSL).
# Fires at 18:00 local time every day.
#
# Idempotent: re-running is a no-op if the cron entry already exists.
# Pass --force to remove and re-add the entry.

set -eu

# Self-locate the harness root (no hardcoded paths — see scripts/lib/resolve-harness-dir.sh)
__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/../lib/resolve-harness-dir.sh"

ORCHESTRATOR="$HARNESS_DIR/scripts/orchestration/daily-orchestrator.sh"
CRON_MARKER="sdd-daily-orchestrator"
CRON_ENTRY="0 18 * * * bash -lc \"$ORCHESTRATOR\" # $CRON_MARKER"

FORCE=false
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=true ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

if [ ! -x "$ORCHESTRATOR" ]; then
  echo "ERROR: orchestrator not found or not executable at $ORCHESTRATOR" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# preflight — prove the orchestrator runs under a cron-like environment.
# ---------------------------------------------------------------------------
# Installing a crontab entry proves nothing about whether the entry will work. cron
# runs with a near-empty environment and a minimal PATH, so a command that works
# perfectly in an interactive shell can fail every night with no visible symptom —
# the same class of silent failure that left the macOS LaunchAgent exiting 126 for
# four days behind a green install. Approximate cron's environment and refuse to
# report success unless a --dry-run comes back clean.
preflight() {
  local errbuf
  errbuf="$(mktemp)"
  local code=0
  env -i HOME="$HOME" PATH="/usr/local/bin:/usr/bin:/bin" SHELL="/bin/bash" \
    /bin/bash -lc "$ORCHESTRATOR --dry-run" >/dev/null 2>"$errbuf" || code=$?

  if [ "$code" -eq 0 ]; then
    rm -f "$errbuf"
    return 0
  fi

  echo "" >&2
  echo "✗ PREFLIGHT FAILED — cron entry registered, but the orchestrator exits $code" >&2
  echo "  under a cron-like environment (empty env, PATH=/usr/local/bin:/usr/bin:/bin)." >&2
  if [ -s "$errbuf" ]; then
    echo "  stderr:" >&2
    sed 's/^/    /' "$errbuf" >&2
  fi
  echo "" >&2
  echo "  Most often this is a command that is only on PATH for interactive shells." >&2
  echo "  Either use absolute paths in the failing script, or set PATH= explicitly" >&2
  echo "  at the top of the crontab." >&2
  rm -f "$errbuf"
  return 1
}

# Idempotency: skip re-registering if the marker is already present — but still run
# the preflight, because "already registered" is exactly the state a silently-failing
# cron job reports.
if [ "$FORCE" = false ] && crontab -l 2>/dev/null | grep -qF "$CRON_MARKER"; then
  echo "✓ Cron entry '$CRON_MARKER' already registered. Skipping registration (use --force to recreate)."
  preflight || exit 1
  echo "✓ Preflight passed — the orchestrator runs under a cron-like environment."
  exit 0
fi

# Remove any existing entry with this marker, then add the fresh one
( crontab -l 2>/dev/null | grep -vF "$CRON_MARKER"; echo "$CRON_ENTRY" ) | crontab -

echo "✓ Cron entry registered. Daily run at 18:00 local time."
echo "  Verify: crontab -l | grep $CRON_MARKER"
echo "  Remove: crontab -l | grep -vF '$CRON_MARKER' | crontab -"

# Registration succeeded — now prove it can actually run. Non-zero exit here is
# deliberate: a scheduler that cannot execute is not a successful install.
preflight || exit 1
echo "✓ Preflight passed — the orchestrator runs under a cron-like environment."
