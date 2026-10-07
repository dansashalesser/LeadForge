#!/bin/bash
# One-time Windows Task Scheduler bootstrap for the SDD daily orchestrator.
# Creates a scheduled task "SDD Daily Orchestrator" that fires at 18:00 local
# and then repeats every 4h, all day (6 fires/day) — not just once. A single
# daily trigger is fragile: if the machine/WSL is asleep at that one precise
# moment, Windows' own missed-run catch-up (StartWhenAvailable) only retries
# ONE missed occurrence and has been observed to report success while doing
# nothing (silent no-op — see daily-orchestrator.sh's fail-loud logging, added
# 2026-08-02, which would have caught this). Every sub-routine self-gates on
# its own last-run state file, so 5 of 6 daily fires are near-instant no-ops —
# repeating cheaply just means the machine only needs to be awake for ANY ONE
# of 6 daily windows instead of one exact moment.
# "RunOnlyIfIdle=false" and "StartWhenAvailable=true" so the task runs as soon
# as possible after a missed start. The SessionStart hook is a backup catch-up
# path: if the runner hasn't fired in >24h, opening a Claude session triggers it,
# and escalates to a full-fleet run if the global orchestrator log is >36h stale.
#
# Idempotent: re-running with no args is a no-op if the task already exists.
# Pass --force to delete and recreate (e.g. after editing the schedule here).

set -eu

# Self-locate the harness root (no hardcoded paths — see scripts/lib/resolve-harness-dir.sh)
__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/../lib/resolve-harness-dir.sh"

TASK_NAME="SDD Daily Orchestrator"
WSL_DISTRO="${WSL_DISTRO_NAME:-Ubuntu}"
ORCHESTRATOR="$HARNESS_DIR/scripts/orchestration/daily-orchestrator.sh"
XML_PATH="/tmp/sdd-orchestrator-task.xml"

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

if ! command -v schtasks.exe >/dev/null 2>&1; then
  echo "ERROR: schtasks.exe not on PATH. Are you in WSL?" >&2
  exit 1
fi

# --- Skip cleanly when there is no WSL distro (Git Bash / MSYS2-only setup) ---
# The scheduled task runs the orchestrator via `wsl.exe -d <distro>`, so it is
# only meaningful when a WSL distro exists. On a WSL-less box it would register a
# task that can never run. Maintenance still happens via the SessionStart hook
# catch-up (>24h), so we skip gracefully (exit 0) instead of failing loudly.
if ! wsl.exe -l -q >/dev/null 2>&1 \
   || [ -z "$(wsl.exe -l -q 2>/dev/null | tr -d '\000\r' | grep -v '^[[:space:]]*$' | head -1)" ]; then
  echo "  Skipping daily scheduled task: no WSL distro found (Git Bash/MSYS2-only setup)."
  echo "  Maintenance still runs via the SessionStart hook when you open Claude (>24h catch-up)."
  exit 0
fi

# --- iconv is required to encode the Task Scheduler XML as UTF-16 ---
if ! command -v iconv >/dev/null 2>&1; then
  echo "  Skipping daily scheduled task: 'iconv' not available to encode the task XML."
  echo "  Maintenance still runs via the SessionStart hook. (Install iconv to enable the task.)"
  exit 0
fi

# --- Idempotency check: skip if task already exists and --force not set ---
if [ "$FORCE" = false ] && schtasks.exe /Query /TN "$TASK_NAME" >/dev/null 2>&1; then
  echo "✓ Task '$TASK_NAME' already exists. Skipping (use --force to recreate)."
  exit 0
fi

# Build the XML. Windows Task Scheduler XML is locale-sensitive; this template
# is the minimal portable form. The trigger uses local time.
cat > "$XML_PATH" <<XML
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>SDD harness daily maintenance — runs daily-orchestrator.sh inside WSL. Fires at 18:00 and repeats every 4h (6x/day); each sub-routine self-gates on its own last-run state so repeat fires are cheap no-ops except the one that's actually due.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T18:00:00</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      <Repetition>
        <Interval>PT4H</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
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
    <WakeToRun>false</WakeToRun>
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

# Delete existing task if present (idempotent — only reached when --force or task missing)
schtasks.exe /Query /TN "$TASK_NAME" >/dev/null 2>&1 && \
  schtasks.exe /Delete /TN "$TASK_NAME" /F >/dev/null 2>&1

# Create from XML
if schtasks.exe /Create /TN "$TASK_NAME" /XML "$WIN_XML_PATH" /F >/dev/null 2>&1; then
  echo "✓ Task '$TASK_NAME' created. Runs at 18:00 local, repeats every 4h (6x/day)."
  echo "  Verify: schtasks.exe /Query /TN \"$TASK_NAME\" /V /FO LIST"
  echo "  Run now: schtasks.exe /Run /TN \"$TASK_NAME\""
  echo "  Delete:  schtasks.exe /Delete /TN \"$TASK_NAME\" /F"
else
  echo "✗ schtasks.exe /Create failed" >&2
  exit 1
fi

rm -f "$XML_PATH"

# ---------------------------------------------------------------------------
# preflight — prove the orchestrator runs, not merely that the task was created.
# ---------------------------------------------------------------------------
# Task creation succeeding says nothing about whether the task can execute. The macOS
# sibling of this script learned that the hard way: a LaunchAgent registered cleanly
# and then exited 126 every day for four days because launchd could not read the
# harness under a TCC-protected folder, with setup reporting success throughout.
#
# The direct WSL equivalent is cheap, so do it: run --dry-run through the same
# `wsl.exe -d <distro> -- bash -lc` path the task itself uses.
#
# WARN-ONLY, unlike the macOS and Linux preflights which exit 1. Verifying the
# *Windows-side* execution (schtasks /Run, then reading LastTaskResult) is the check
# that would actually match those two, and it is not implemented here because it
# could not be tested on this machine. Rather than gate installs on untested Windows
# behaviour, this reports and continues. Verify by hand with the /Query line above.
if command -v wsl.exe >/dev/null 2>&1; then
  preflight_err="$(mktemp)"
  if wsl.exe -d "$WSL_DISTRO" -- bash -lc "$ORCHESTRATOR --dry-run" \
       >/dev/null 2>"$preflight_err"; then
    echo "✓ Preflight passed — the orchestrator runs inside $WSL_DISTRO."
  else
    echo "" >&2
    echo "⚠ PREFLIGHT WARNING — task created, but the orchestrator failed inside WSL." >&2
    [ -s "$preflight_err" ] && sed 's/^/    /' "$preflight_err" >&2
    echo "  The scheduled task will most likely do nothing. Investigate before relying on it." >&2
  fi
  rm -f "$preflight_err"
fi
