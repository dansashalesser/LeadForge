#!/bin/bash
# Registers the SDD daily orchestrator as a macOS LaunchAgent.
# Fires at 18:00 local time every day via launchd.
#
# Idempotent: re-running is a no-op if the agent is already loaded.
# Pass --force to unload and re-register (e.g. after editing the schedule).

set -eu

# Self-locate the harness root (no hardcoded paths — see scripts/lib/resolve-harness-dir.sh)
__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/../lib/resolve-harness-dir.sh"

LABEL="com.sdd.daily-orchestrator"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
ORCHESTRATOR="$HARNESS_DIR/scripts/orchestration/daily-orchestrator.sh"
LOG_DIR="$HARNESS_DIR/logs"

PROBE_LABEL="com.sdd.orchestrator-preflight"

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
# preflight — prove launchd can actually EXECUTE the orchestrator.
# ---------------------------------------------------------------------------
# `launchctl load` returning 0 only means the job was registered. It says nothing
# about whether the job can run. The gap is not theoretical: with the harness under
# ~/Documents, launchd (which holds no Full Disk Access) was refused at exec time with
#
#   /bin/bash: .../daily-orchestrator.sh: Operation not permitted
#
# every day for four days, exiting 126, while setup reported success and `launchctl
# list` showed the job present. macOS protects ~/Documents, ~/Desktop and ~/Downloads
# under TCC, and that grant is per-machine and never travels with a clone — so a fleet
# can be silently dead on a brand-new machine with a green install.
#
# So: register a throwaway agent that runs the orchestrator with --dry-run in the exact
# same launchd context, and refuse to report success unless it comes back clean.
preflight() {
  local probe_plist="$PLIST_DIR/$PROBE_LABEL.plist"
  local probe_sh="$LOG_DIR/.preflight-probe.sh"
  local result="$LOG_DIR/.preflight-result"
  local probe_err="$LOG_DIR/.preflight-probe.err"

  mkdir -p "$PLIST_DIR" "$LOG_DIR"
  rm -f "$result" "$probe_err"

  cat > "$probe_sh" <<PROBE
#!/bin/bash
"$ORCHESTRATOR" --dry-run > /dev/null 2>"$probe_err"
echo \$? > "$result"
PROBE
  chmod +x "$probe_sh"

  cat > "$probe_plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${PROBE_LABEL}</string>
    <key>ProgramArguments</key>
    <array><string>/bin/bash</string><string>-lc</string><string>${probe_sh}</string></array>
    <key>StandardErrorPath</key><string>${probe_err}</string>
    <key>RunAtLoad</key><true/>
</dict>
</plist>
PLIST

  launchctl bootout "gui/$(id -u)/$PROBE_LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$probe_plist" 2>/dev/null || {
    echo "  (preflight could not register a probe agent — skipping check)" >&2
    rm -f "$probe_plist" "$probe_sh"
    return 0
  }

  local waited=0
  while [ ! -f "$result" ] && [ "$waited" -lt 30 ]; do
    sleep 1
    waited=$((waited + 1))
  done

  local code=""
  [ -f "$result" ] && code="$(cat "$result" 2>/dev/null)"

  launchctl bootout "gui/$(id -u)/$PROBE_LABEL" 2>/dev/null || true
  rm -f "$probe_plist" "$probe_sh" "$result"

  if [ "$code" = "0" ]; then
    rm -f "$probe_err"
    return 0
  fi

  echo "" >&2
  echo "✗ PREFLIGHT FAILED — the agent is registered but launchd cannot run it." >&2
  if [ -z "$code" ]; then
    echo "  The probe never completed (waited ${waited}s)." >&2
  else
    echo "  Orchestrator exited $code under launchd." >&2
  fi
  if [ -s "$probe_err" ]; then
    echo "  stderr:" >&2
    sed 's/^/    /' "$probe_err" >&2
  fi

  case "$HARNESS_DIR" in
    "$HOME"/Documents/*|"$HOME"/Desktop/*|"$HOME"/Downloads/*)
      echo "" >&2
      echo "  CAUSE: the harness lives under a TCC-protected folder:" >&2
      echo "    $HARNESS_DIR" >&2
      echo "  macOS denies launchd agents access to ~/Documents, ~/Desktop and ~/Downloads." >&2
      echo "  FIX (preferred): move the harness somewhere unprotected, e.g. ~/GitHub/," >&2
      echo "  then re-run install.sh so every stored path is refreshed." >&2
      echo "  FIX (alternative): grant Full Disk Access to /bin/bash in" >&2
      echo "  System Settings → Privacy & Security. Per-machine, cannot be scripted." >&2
      ;;
    *)
      echo "" >&2
      echo "  Check the stderr above and $probe_err." >&2
      ;;
  esac
  return 1
}

# Idempotency: skip re-registering if already loaded and not forcing — but still run
# the preflight, because "already loaded" is exactly the state a silently-dead job
# reports.
if [ "$FORCE" = false ] && launchctl list "$LABEL" >/dev/null 2>&1; then
  echo "✓ LaunchAgent '$LABEL' already loaded. Skipping registration (use --force to recreate)."
  preflight || exit 1
  echo "✓ Preflight passed — launchd can execute the orchestrator."
  exit 0
fi

# Unload existing agent if present (covers --force path and stale plist)
if launchctl list "$LABEL" >/dev/null 2>&1; then
  launchctl unload -w "$PLIST_PATH" 2>/dev/null || true
fi

mkdir -p "$PLIST_DIR" "$LOG_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-lc</string>
        <string>caffeinate -i ${ORCHESTRATOR}</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>18</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/orchestrator.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/orchestrator.stderr.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST

if launchctl load -w "$PLIST_PATH"; then
  echo "✓ LaunchAgent '$LABEL' registered. Daily run at 18:00 local time."
  echo "  Plist: $PLIST_PATH"
  echo "  Verify: launchctl list $LABEL"
  echo "  Run now: launchctl start $LABEL"
  echo "  Remove: launchctl unload -w $PLIST_PATH && rm $PLIST_PATH"
else
  echo "✗ launchctl load failed" >&2
  exit 1
fi

# Registration succeeded — now prove it can actually run. Non-zero exit here is
# deliberate: a scheduler that cannot execute is not a successful install.
preflight || exit 1
echo "✓ Preflight passed — launchd can execute the orchestrator."
