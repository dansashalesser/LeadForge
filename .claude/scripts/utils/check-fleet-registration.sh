#!/bin/bash
# =============================================================================
# check-fleet-registration.sh — find harness-installed repos missing from projects.txt.
# =============================================================================
# projects.txt is the single source of truth for WHICH repos the orchestrator visits.
# Nothing enforced that it was complete, so a repo could carry a full harness install
# and still receive zero scheduled routines forever, with no symptom anywhere — the
# dashboard only renders repos it is told about, so an unregistered repo is not "shown
# as failing", it is simply absent.
#
# Scan roots are DERIVED from projects.txt (the parent directory of each registered
# repo) rather than stored anywhere. That keeps projects.txt the only place on disk
# that records a fleet path.
#
# Exit 0 when every harness-installed repo is registered, 1 otherwise.
#
# Run manually:  bash scripts/utils/check-fleet-registration.sh
# Wired into:    daily-orchestrator.sh (logs a warning line) and /kiro:harness-validate
# =============================================================================
set -u

__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/../lib/resolve-harness-dir.sh"

PROJECTS_FILE="$HARNESS_DIR/projects.txt"
QUIET=false
[ "${1:-}" = "--quiet" ] && QUIET=true

if [ ! -f "$PROJECTS_FILE" ]; then
  echo "check-fleet-registration: projects.txt missing at $PROJECTS_FILE" >&2
  exit 1
fi

# A repo counts as harness-installed if it has the per-repo runner the orchestrator
# dispatches to. A bare .claude/settings.local.json means someone used Claude Code
# there, not that the harness is installed — those are not fleet members.
INSTALL_MARKER=".claude/scripts/orchestration/daily-runner.sh"

REGISTERED=""
SCAN_ROOTS=""
while IFS= read -r repo || [ -n "$repo" ]; do
  [ -z "$repo" ] && continue
  case "$repo" in \#*) continue ;; esac
  REGISTERED="$REGISTERED
$repo"
  SCAN_ROOTS="$SCAN_ROOTS
$(dirname "$repo")"
done < "$PROJECTS_FILE"

SCAN_ROOTS="$(printf '%s\n' "$SCAN_ROOTS" | grep -v '^$' | sort -u)"

missing=0
while IFS= read -r root; do
  [ -d "$root" ] || continue
  for candidate in "$root"/*; do
    [ -d "$candidate" ] || continue
    [ -f "$candidate/$INSTALL_MARKER" ] || continue
    if ! printf '%s\n' "$REGISTERED" | grep -qxF "$candidate"; then
      echo "UNREGISTERED: $candidate"
      echo "  has $INSTALL_MARKER but is absent from projects.txt — it receives no"
      echo "  scheduled routines. Add it, or uninstall the harness there."
      missing=$((missing + 1))
    fi
  done
done <<EOF
$SCAN_ROOTS
EOF

if [ "$missing" -gt 0 ]; then
  echo ""
  echo "✗ $missing harness-installed repo(s) not registered in projects.txt."
  exit 1
fi

[ "$QUIET" = false ] && echo "✓ Every harness-installed repo under the scanned roots is registered."
exit 0
