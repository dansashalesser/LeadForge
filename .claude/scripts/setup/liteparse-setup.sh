#!/usr/bin/env bash
# liteparse-setup.sh — own liteparse in a dedicated, version-pinned harness venv
#
# Run automatically by install.sh and update.sh. Safe to run manually anytime.
# Idempotent — running multiple times has no side effects.
#
# Why a venv (not `pip install --user`):
#   liteparse needs Python >=3.10 with a binary wheel, but the ambient `python3`
#   is often Apple's 3.9 (too old) or a PEP-668 'externally-managed' Homebrew/
#   Debian interpreter that refuses `--user`. A self-owned venv sidesteps both:
#   a locked >=3.10 interpreter, no --break-system-packages, no "which python3"
#   roulette. The document-parsing skill points at this venv's python / lit.
#
# Location: <harness root>/.venv-tools  (gitignored; pruned by ship-safety-scan).
#   Resolvable from any repo as ${SDD_HARNESS_HOME:-$HOME/.claude/sdd-harness}/.venv-tools
#   because ~/.claude/sdd-harness symlinks to the harness root.

set -u

__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/../lib/resolve-harness-dir.sh"
# Venv creation and interpreter selection live in lib/venv-tools.sh — shared with
# check-harness-deps.sh, which installs the rest of the harness's own dependencies
# into this same venv.
. "$__here/../lib/venv-tools.sh"

# --- Fast path: venv already has an importable liteparse ---
VPY="$(venv_tools_python)"
if [ -n "$VPY" ] && "$VPY" -c 'import liteparse' >/dev/null 2>&1; then
  echo "  liteparse already installed (.venv-tools: $("$VPY" --version 2>&1))."
  exit 0
fi

# --- Create/repair the venv if needed ---
VPY="$(venv_tools_ensure)" || VPY=""
if [ -z "$VPY" ]; then
  echo "  liteparse skipped — could not create venv (no working Python >=3.10; try: uv python install 3.12)."
  exit 1
fi

# --- Install liteparse inside the venv (no --user / --break-system-packages needed) ---
if pip_cooldown_install "$VPY" -q --upgrade liteparse >/dev/null \
   && "$VPY" -c 'import liteparse' >/dev/null 2>&1; then
  echo "  liteparse installed (.venv-tools: $("$VPY" --version 2>&1))."
  exit 0
fi

echo "  liteparse skipped — install into .venv-tools failed (build a wheel-compatible Python >=3.10)."
exit 1
