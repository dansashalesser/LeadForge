#!/usr/bin/env bash
# check-harness-deps.sh — verify, heal, and declare every dependency the harness needs
#
# Run automatically by install.sh and update.sh. Safe to run manually anytime.
# Idempotent — a healthy machine prints "ok" for everything and changes nothing.
#
# The problem it solves
# ---------------------
# Harness setup scripts used to `pip install <pkg> -q 2>/dev/null` into a target
# repo's virtualenv and write nothing to that repo's manifest. Two failures fell
# out of that:
#   1. The package was undeclared, so the repo's own `uv sync` / lockfile regen /
#      dependency prune deleted it as an orphan — then the next harness update
#      silently reinstalled it, and the next prune deleted it again.
#   2. Failures were swallowed. "Done." printed whether or not anything installed.
#
# What it does
#   1. harness-requirements.txt -> installed into the harness-owned .venv-tools.
#      Nothing in any target repo can prune these, because no repo has them.
#   2. repo-requirements.txt    -> installed into each registered repo's venv AND
#      declared in that repo's manifest, so a prune stops treating it as an orphan.
#   3. Reports every package as ok / healed / FAILED / skipped. No silent drift.
#
# Env:
#   SDD_SKIP_DEP_DECLARE=1   install into repo venvs, but never touch repo manifests
#   SDD_DEP_CHECK_ONLY=1     report drift only — install nothing, declare nothing
#
# Exit: 0 = every required package importable (or legitimately skipped)
#       1 = at least one package could not be healed

set -u

__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/../lib/resolve-harness-dir.sh"
. "$__here/../lib/venv-tools.sh"
. "$__here/../lib/repo-venv.sh"
. "$__here/../lib/tool-paths.sh"

HARNESS_REQS="$__here/harness-requirements.txt"
REPO_REQS="$__here/repo-requirements.txt"
PROJECTS_FILE="$HARNESS_DIR/projects.txt"
CHECK_ONLY="${SDD_DEP_CHECK_ONLY:-0}"
FAILURES=0

# report <scope> <pkg> <status> — one aligned line per package. Drift is only
# actionable if it is visible, so every package prints, including the healthy ones.
report() {
  printf '  %-28s %-18s %s\n' "$1" "$2" "$3"
}

# read_manifest <file> — emit "<pip-spec> <import-name>" per requirement.
# import-name defaults to the distribution name with '-' -> '_'.
read_manifest() {
  # Capture the path before the loop: `set --` below rewrites $1.
  local file="$1" line spec imp
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    # shellcheck disable=SC2086
    set -- $line
    [ $# -ge 1 ] || continue
    spec="$1"; imp="${2:-}"
    if [ -z "$imp" ]; then
      imp="$(printf '%s' "$spec" | sed -E 's/[<>=!~;[].*$//; s/-/_/g')"
    fi
    printf '%s %s\n' "$spec" "$imp"
  done < "$file"
}

echo "Harness dependency check"

# ── 1. Harness-owned deps -> .venv-tools ───────────────────────────────────────
VPY="$(venv_tools_ensure)" || VPY=""
if [ -z "$VPY" ]; then
  report ".venv-tools" "-" "FAILED (no usable Python >=3.10 — try: uv python install 3.12)"
  FAILURES=$((FAILURES + 1))
else
  while read -r spec imp; do
    [ -n "$spec" ] || continue
    if "$VPY" -c "import $imp" >/dev/null 2>&1; then
      report ".venv-tools" "$spec" "ok"
    elif [ "$CHECK_ONLY" = "1" ]; then
      report ".venv-tools" "$spec" "MISSING (check-only)"
      FAILURES=$((FAILURES + 1))
    elif out="$(pip_cooldown_install "$VPY" --upgrade "$spec" 2>&1)" \
         && "$VPY" -c "import $imp" >/dev/null 2>&1; then
      report ".venv-tools" "$spec" "healed (installed)"
    else
      report ".venv-tools" "$spec" "FAILED"
      printf '%s\n' "${out:-}" | tail -5 | sed 's/^/      /'
      FAILURES=$((FAILURES + 1))
    fi
  done <<EOF
$(read_manifest "$HARNESS_REQS")
EOF
fi

# ── 2. Repo-facing deps -> each registered repo's venv, then declared ──────────
REPO_SPECS="$(read_manifest "$REPO_REQS")"

check_repo() {
  local repo="$1" name spec imp
  name="$(basename "$repo")"

  if [ ! -d "$repo" ]; then
    report "$name" "-" "skipped (directory not found)"
    return
  fi
  # A repo with no Python manifest is not a Python repo — nothing to install into.
  if [ ! -f "$repo/pyproject.toml" ] && [ ! -f "$repo/requirements.txt" ]; then
    report "$name" "-" "skipped (not a Python repo)"
    return
  fi

  while read -r spec imp; do
    [ -n "$spec" ] || continue
    if repo_has_module "$repo" "$imp"; then
      report "$name" "$spec" "ok"
      continue
    fi
    # A repo with nowhere to install is a skip, not a failure — its owner has not
    # created an environment yet. The declaration below still lands, so whoever
    # does create one gets the package with it.
    if ! repo_has_env "$repo"; then
      report "$name" "$spec" "skipped (no virtualenv — declared only)"
      continue
    fi
    if [ "$CHECK_ONLY" = "1" ]; then
      report "$name" "$spec" "MISSING (check-only)"
      FAILURES=$((FAILURES + 1))
      continue
    fi
    local rc=0
    repo_pip_install "$repo" "$spec" || rc=$?
    case "$rc" in
      0) report "$name" "$spec" "healed (installed)" ;;
      2) report "$name" "$spec" "skipped (no virtualenv — declared only)" ;;
      *) report "$name" "$spec" "FAILED"; FAILURES=$((FAILURES + 1)) ;;
    esac
  done <<EOF
$REPO_SPECS
EOF

  # Declaration is the half that makes the fix stick: an installed-but-undeclared
  # package is exactly what a dependency prune deletes. Runs for every Python repo
  # (non-Python repos returned above), regardless of install outcome.
  if [ "${SDD_SKIP_DEP_DECLARE:-0}" != "1" ] && [ "$CHECK_ONLY" != "1" ]; then
    local specs_only
    specs_only="$(printf '%s\n' "$REPO_SPECS" | awk 'NF {print $1}')"
    if [ -n "$specs_only" ]; then
      # shellcheck disable=SC2086
      python3 "$__here/declare-repo-deps.py" "$repo" $specs_only || FAILURES=$((FAILURES + 1))
    fi
  fi
}

if [ -z "$REPO_SPECS" ]; then
  echo "  (no repo-facing requirements declared — skipping per-repo check)"
elif [ ! -s "$PROJECTS_FILE" ]; then
  echo "  (no registered projects — skipping per-repo check)"
else
  while IFS= read -r project || [ -n "$project" ]; do
    [ -n "$project" ] || continue
    # The harness repo is not an install target for repo-facing packages.
    [ "$(cd "$project" 2>/dev/null && pwd -P)" = "$(cd "$HARNESS_DIR" && pwd -P)" ] && continue
    check_repo "$project"
  done < "$PROJECTS_FILE"
fi

# ── 3. Global tooling the harness routes through ──────────────────────────────
# headroom is machine-level, not per-repo: it works through ANTHROPIC_BASE_URL in
# the user's shell rc plus a launchd/systemd proxy service. No repo declares it and
# no repo dependency prune can touch it — the per-repo venv install in
# headroom-setup.sh is convenience only, which is why it is not in
# repo-requirements.txt. What CAN break it is invisible: the binary lands in
# ~/.local/bin, which is off PATH in the environments install.sh/update.sh run
# under, and routing left pointing at a dead proxy fails every single Claude call.
# Report both; fixing them is headroom-setup.sh's job, not this script's.
HR_BIN="$(find_tool headroom || true)"
if [ -z "$HR_BIN" ]; then
  report "global" "headroom" "not installed (optional)"
else
  if command -v headroom >/dev/null 2>&1; then
    report "global" "headroom" "ok"
  else
    # Installed and usable, just not reachable by name in this shell. Reporting
    # only — headroom-setup.sh owns the durable fix (`uv tool update-shell`).
    report "global" "headroom" "ok (installed, not on PATH)"
    echo "      $HR_BIN — put uv's tool-bin dir on PATH:  uv tool update-shell"
  fi

  # Routing wired anywhere the shell or Claude Code will pick it up.
  HR_ROUTED=0
  for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.claude/settings.json"; do
    [ -f "$rc" ] && grep -q "ANTHROPIC_BASE_URL" "$rc" 2>/dev/null && HR_ROUTED=1
  done

  # Probe carefully. `/readyz` answers instantly when warm but can exceed 8s while
  # the compression model is loading or under load, and curl's exit code is the only
  # way to tell "nothing is listening" (7) from "listening but slow" (28). Treating
  # those the same produces a false "proxy is down" on a perfectly healthy machine —
  # measured here: a first probe timed out at 8s while the very next returned HTTP 200.
  HR_HEALTHY=0
  HR_SLOW=0
  if command -v curl >/dev/null 2>&1; then
    hr_probe() {
      curl -fsS --connect-timeout 3 -m 25 \
        "http://127.0.0.1:${HEADROOM_PORT:-8787}/readyz" >/dev/null 2>&1
    }
    hr_rc=0
    hr_probe || hr_rc=$?
    if [ "$hr_rc" -ne 0 ]; then
      # One retry absorbs a cold first hit before anything alarming is printed.
      hr_rc=0
      hr_probe || hr_rc=$?
    fi
    case "$hr_rc" in
      0)  HR_HEALTHY=1 ;;
      28) HR_SLOW=1 ;;   # connected, then timed out — it is up, just slow
    esac
  fi

  if [ "$HR_ROUTED" = "1" ] && [ "$HR_HEALTHY" = "1" ]; then
    report "global" "headroom proxy" "ok (routed, healthy)"
  elif [ "$HR_ROUTED" = "1" ] && [ "$HR_SLOW" = "1" ]; then
    report "global" "headroom proxy" "ok (routed, slow to answer /readyz)"
  elif [ "$HR_ROUTED" = "1" ]; then
    # The one genuinely dangerous state: every Claude API call is aimed at a proxy
    # that refused the connection outright.
    report "global" "headroom proxy" "FAILED (routed but not listening)"
    echo "      ANTHROPIC_BASE_URL points at the proxy, but nothing accepted the connection."
    echo "      Start it:  ${HR_BIN} install apply --preset persistent-service --memory"
    FAILURES=$((FAILURES + 1))
  elif [ "$HR_HEALTHY" = "1" ] || [ "$HR_SLOW" = "1" ]; then
    report "global" "headroom proxy" "running but not routed"
    echo "      Wire it:  ${HR_BIN} init --global --memory claude"
  else
    report "global" "headroom proxy" "not running (optional)"
  fi
fi

echo ""
if [ "$FAILURES" -eq 0 ]; then
  echo "  All harness dependencies present."
else
  echo "  $FAILURES dependency problem(s) above need attention."
  echo "  Re-run: bash $__here/check-harness-deps.sh"
fi
exit $([ "$FAILURES" -eq 0 ] && echo 0 || echo 1)
