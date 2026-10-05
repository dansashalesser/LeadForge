# shellcheck shell=bash
# =============================================================================
# venv-tools.sh — create/resolve the harness-owned Python venv (.venv-tools)
# =============================================================================
# Source this AFTER lib/resolve-harness-dir.sh (it needs $HARNESS_DIR).
#
#   __here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
#   . "$__here/../lib/resolve-harness-dir.sh"
#   . "$__here/../lib/venv-tools.sh"
#   VPY="$(venv_tools_ensure)" || exit 1
#
# Why a harness-owned venv at all: harness scripts must not depend on whatever
# `python3` happens to resolve to inside a target repo. That interpreter belongs
# to the repo, and the repo's owner may prune it, relock it, or recreate it at
# any time — which silently kills every harness script that imported from it.
# .venv-tools is ours, so nothing in a target repo can take it away.
#
# Location: <harness root>/.venv-tools  (gitignored; pruned by ship-safety-scan).
#   Reachable from any repo as ${SDD_HARNESS_HOME:-$HOME/.claude/sdd-harness}/.venv-tools
#   because ~/.claude/sdd-harness symlinks to the harness root.
#
# Extracted from liteparse-setup.sh once a third consumer appeared
# (liteparse-setup.sh, check-harness-deps.sh, stop-hook.sh's interpreter lookup):
# duplicating this interpreter-selection logic is how subtle "no module named
# encodings" breakage spreads.
# =============================================================================

VENV_TOOLS_DIR="${VENV_TOOLS_DIR:-$HARNESS_DIR/.venv-tools}"

# venv_tools_python — echo the venv's python, or nothing if absent.
# POSIX layout is bin/python, Windows/Git Bash is Scripts/python.exe.
venv_tools_python() {
  if [ -x "$VENV_TOOLS_DIR/bin/python" ]; then
    echo "$VENV_TOOLS_DIR/bin/python"
  elif [ -x "$VENV_TOOLS_DIR/Scripts/python.exe" ]; then
    echo "$VENV_TOOLS_DIR/Scripts/python.exe"
  fi
}

# venv_tools_ensure — echo a WORKING python from the venv, creating it if needed.
# Returns 1 (and echoes nothing) when no usable Python >=3.10 can be found.
venv_tools_ensure() {
  local vpy
  vpy="$(venv_tools_python)"

  # An existing venv whose python cannot even `import sys` is a uv standalone
  # with a base_prefix that no longer exists. Rebuild rather than limp along.
  if [ -n "$vpy" ] && ! "$vpy" -c 'import sys' >/dev/null 2>&1; then
    echo "  .venv-tools has a broken Python (likely a uv standalone) — rebuilding." >&2
    rm -rf "$VENV_TOOLS_DIR"
    vpy=""
  fi

  if [ -n "$vpy" ]; then
    echo "$vpy"
    return 0
  fi

  local created=0

  # 1. uv venv --seed — uv resolves a properly-linked interpreter and seeds pip,
  #    so `python -m pip` works inside the venv without ensurepip.
  if command -v uv >/dev/null 2>&1; then
    if uv venv --python '>=3.10' --seed "$VENV_TOOLS_DIR" >/dev/null 2>&1; then
      created=1
    fi
  fi

  # 2. System Python paths. uv-managed standalones under ~/.local/share/uv root
  #    their stdlib at a prefix that does not exist outside uv, so a venv built
  #    from them dies with "ModuleNotFoundError: No module named 'encodings'".
  #    Detect and skip them.
  if [ "$created" -eq 0 ]; then
    local cand
    for cand in /usr/bin/python3.13 /usr/bin/python3.12 /usr/bin/python3.11 /usr/bin/python3.10 /usr/bin/python3 python3.13 python3.12 python3.11 python3.10 python3; do
      command -v "$cand" >/dev/null 2>&1 || continue
      case "$("$cand" -c 'import sys; print(sys.base_prefix)' 2>/dev/null)" in
        */.local/share/uv/*) continue ;;
      esac
      if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
        if "$cand" -m venv "$VENV_TOOLS_DIR" 2>/dev/null; then
          created=1
          break
        fi
      fi
    done
  fi

  [ "$created" -eq 1 ] || return 1

  vpy="$(venv_tools_python)"
  [ -n "$vpy" ] || return 1
  "$vpy" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
  echo "$vpy"
}

# Installs into this venv go through pip_cooldown_install (release-age cooldown).
. "$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pip-cooldown.sh"
