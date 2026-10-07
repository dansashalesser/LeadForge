# shellcheck shell=bash
# =============================================================================
# repo-venv.sh — locate a target repo's Python interpreter and install into it
# =============================================================================
# Source from any setup script:
#   __here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
#   . "$__here/../lib/repo-venv.sh"
#
#   PY="$(repo_python /path/to/repo)"           # "" when the repo has no venv
#   repo_pip_install /path/to/repo raindrop-ai  # prints real errors on failure
#   repo_has_module /path/to/repo raindrop      # 0 = importable
#
# Extracted once raindrop-setup.sh, headroom-setup.sh and check-harness-deps.sh
# all needed the same strategy-A/B/C venv hunt. The duplicated copies each ended
# every install attempt with `-q 2>/dev/null`, so a failed install was
# indistinguishable from a successful one — the harness reported "Done." while
# the package was never there. Failures are reported here, once.
# =============================================================================

# Every install below carries the release-age cooldown (SDD_PIP_MIN_AGE).
. "$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pip-cooldown.sh"

# _rv_timeout <secs> <cmd...> — run with a time limit where one is available.
# macOS ships no `timeout` (it is GNU coreutils, optionally `gtimeout` via brew).
# Without this shim every timed probe on a Mac fails with "command not found",
# which reads as "module missing" and triggers an endless reinstall loop.
_rv_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$secs" "$@"
  else
    "$@"
  fi
}

# repo_python <repo> — echo a python executable belonging to the repo, or nothing.
# Checked in the order a Python project is most likely to keep its venv. Never
# creates a venv: a target repo's environment layout is its owner's decision.
repo_python() {
  local repo="$1" base cand
  for base in "$repo" "$repo/backend" "$repo/app" "$repo/src"; do
    for cand in "$base/.venv/bin/python" "$base/venv/bin/python" \
                "$base/.venv/Scripts/python.exe" "$base/venv/Scripts/python.exe"; do
      if [ -x "$cand" ]; then
        echo "$cand"
        return 0
      fi
    done
  done
  return 1
}

# repo_has_module <repo> <import-name> — 0 when importable in the repo's env.
repo_has_module() {
  local repo="$1" mod="$2" py
  py="$(repo_python "$repo")" || {
    # uv-managed project with no materialised .venv — ask uv, but never let a
    # cold dependency resolve hang the whole update.
    if [ -f "$repo/pyproject.toml" ] && command -v uv >/dev/null 2>&1; then
      (cd "$repo" && _rv_timeout 20 uv run --no-sync python -c "import $mod" >/dev/null 2>&1)
      return $?
    fi
    return 1
  }
  _rv_timeout 20 "$py" -c "import $mod" >/dev/null 2>&1
}

# repo_has_env <repo> — 0 when the repo has somewhere to install Python packages.
# Lets callers report "no virtualenv" as a skip rather than a failure.
repo_has_env() {
  local repo="$1"
  repo_python "$repo" >/dev/null && return 0
  [ -f "$repo/pyproject.toml" ] && command -v uv >/dev/null 2>&1
}

# repo_pip_install <repo> <pkg> [...] — install into the repo's env.
# Returns 0 on success, 1 on a real failure (installer output printed), and 2 when
# the repo has no environment at all — a skip, not a break.
repo_pip_install() {
  local repo="$1"; shift
  local py out
  py="$(repo_python "$repo")" || py=""

  if [ -n "$py" ]; then
    if out="$(pip_cooldown_install "$py" --upgrade "$@" 2>&1)"; then
      return 0
    fi
    # `uv venv` seeds no pip unless asked, so a perfectly healthy uv-managed venv
    # answers `-m pip` with "No module named pip". uv installs into it directly.
    if command -v uv >/dev/null 2>&1; then
      # shellcheck disable=SC2046 # flags are two words by design
      if out="$(uv pip install $(uv_cooldown_flags) --python "$py" "$@" 2>&1)"; then
        return 0
      fi
    fi
  elif [ -f "$repo/pyproject.toml" ] && command -v uv >/dev/null 2>&1; then
    # shellcheck disable=SC2046
    if out="$(cd "$repo" && uv pip install $(uv_cooldown_flags) "$@" 2>&1)"; then
      return 0
    fi
  else
    return 2
  fi
  printf '%s\n' "$out" | tail -5 | sed 's/^/      /'
  return 1
}
