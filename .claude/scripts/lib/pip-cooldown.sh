# shellcheck shell=bash
# =============================================================================
# pip-cooldown.sh — release-age cooldown for every harness-driven Python install
# =============================================================================
# Source from any setup lib or script:
#   . "$__here/../lib/pip-cooldown.sh"
#
#   pip_cooldown_install "$PY" --upgrade raindrop-ai   # python -m pip install ...
#   uv pip install $(uv_cooldown_flags) raindrop-ai    # uv equivalent
#
# Why: harness specs are unpinned, so an install resolves to whatever was
# published today — and a malicious release is usually yanked within hours.
# Skipping anything newer than SDD_PIP_MIN_AGE (ISO 8601 duration, default P2D)
# closes that window with no pins to bump. Same idea as pi's `.npmrc`
# min-release-age=2 (github.com/earendil-works/pi), applied to pip and uv.
#
#   SDD_PIP_MIN_AGE=P7D   widen the window
#   SDD_PIP_MIN_AGE=off   disable — for a same-day security fix you need now
#
# Call sites: lib/venv-tools.sh consumers (check-harness-deps.sh, liteparse-setup.sh)
# and lib/repo-venv.sh repo_pip_install.
# =============================================================================

# pip_cooldown_install <py> <pip install args...>
# A pip too old for --uploaded-prior-to installs without the cooldown and says
# so on stderr, rather than failing the install outright.
pip_cooldown_install() {
  local py="$1"; shift
  local age="${SDD_PIP_MIN_AGE:-P2D}"
  if [ "$age" = "off" ]; then
    "$py" -m pip install "$@"
  elif "$py" -m pip install --help 2>/dev/null | grep -qF -- '--uploaded-prior-to'; then
    "$py" -m pip install --uploaded-prior-to "$age" "$@"
  else
    echo "  WARNING: $("$py" -m pip --version 2>&1 | cut -d' ' -f1-2) lacks --uploaded-prior-to — installing without the ${age} release-age cooldown." >&2
    "$py" -m pip install "$@"
  fi
}

# uv_cooldown_flags — echo the uv flags for the cooldown, or nothing when off.
# uv rejects an unparseable duration loudly, so a bad SDD_PIP_MIN_AGE fails the
# install with uv's own message instead of silently skipping the cooldown.
uv_cooldown_flags() {
  local age="${SDD_PIP_MIN_AGE:-P2D}"
  [ "$age" = "off" ] || printf -- '--exclude-newer %s' "$age"
}
