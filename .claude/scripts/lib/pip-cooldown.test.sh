#!/usr/bin/env bash
# pip-cooldown.test.sh — prove every harness Python install carries the release-age
# cooldown, and that each escape hatch behaves as documented.
#
# Run: bash scripts/lib/pip-cooldown.test.sh
#
# The cooldown is invisible when it works — an install just succeeds — so the only
# way to know it is applied is to record the exact argv each path hands to pip/uv.
# Fake `python` and `uv` binaries do that; nothing touches the network.

set -u

__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/pip-cooldown.sh"
. "$__here/repo-venv.sh"

PASS=0
FAIL=0

check() {
  local label="$1" actual="$2" expected="$3"
  if [ "$actual" = "$expected" ]; then
    printf '  ok    %-46s %s\n' "$label" "$actual"
    PASS=$((PASS + 1))
  else
    printf '  FAIL  %-46s got [%s] want [%s]\n' "$label" "$actual" "$expected"
    FAIL=$((FAIL + 1))
  fi
}

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

# make_python <path> <advertises-flag: yes|no> <install-exit> — fake interpreter
# that logs `pip install` argv to $T/calls and answers --help / --version.
make_python() {
  cat > "$1" <<EOF
#!/usr/bin/env bash
shift 2  # drop "-m pip"
if [ "\$1" = "--version" ]; then echo "pip 24.0 from fake"; exit 0; fi
if [ "\$1" = "install" ] && [ "\${2:-}" = "--help" ]; then
  [ "$2" = yes ] && echo "  --uploaded-prior-to <datetime_or_duration>"
  exit 0
fi
echo "pip \$*" >> "$T/calls"
exit $3
EOF
  chmod +x "$1"
}

PY="$T/py"

echo "pip_cooldown_install"
make_python "$PY" yes 0; : > "$T/calls"
pip_cooldown_install "$PY" --upgrade foo 2>/dev/null
check "default age is P2D" "$(cat "$T/calls")" "pip install --uploaded-prior-to P2D --upgrade foo"

: > "$T/calls"
SDD_PIP_MIN_AGE=P7D pip_cooldown_install "$PY" foo 2>/dev/null
check "SDD_PIP_MIN_AGE overrides" "$(cat "$T/calls")" "pip install --uploaded-prior-to P7D foo"

: > "$T/calls"
SDD_PIP_MIN_AGE=off pip_cooldown_install "$PY" foo 2>/dev/null
check "off drops the flag" "$(cat "$T/calls")" "pip install foo"

make_python "$PY" no 0; : > "$T/calls"
err="$(pip_cooldown_install "$PY" foo 2>&1 >/dev/null)"
check "old pip installs without flag" "$(cat "$T/calls")" "pip install foo"
case "$err" in *"lacks --uploaded-prior-to"*) warned=yes ;; *) warned=no ;; esac
check "old pip warns on stderr" "$warned" "yes"

echo "uv_cooldown_flags"
check "default" "$(uv_cooldown_flags)" "--exclude-newer P2D"
check "off" "$(SDD_PIP_MIN_AGE=off uv_cooldown_flags)" ""

echo "repo_pip_install"
REPO="$T/repo"
mkdir -p "$REPO/.venv/bin"
make_python "$REPO/.venv/bin/python" yes 0; : > "$T/calls"
repo_pip_install "$REPO" raindrop-ai >/dev/null
check "pip path carries cooldown" "$(cat "$T/calls")" "pip install --uploaded-prior-to P2D --upgrade raindrop-ai"

# pip fails (uv venv with no pip seeded) -> uv fallback must carry it too.
make_python "$REPO/.venv/bin/python" yes 1; : > "$T/calls"
mkdir -p "$T/bin"
cat > "$T/bin/uv" <<EOF
#!/usr/bin/env bash
echo "uv \$*" >> "$T/calls"
EOF
chmod +x "$T/bin/uv"
PATH="$T/bin:$PATH" repo_pip_install "$REPO" raindrop-ai >/dev/null
check "uv fallback carries cooldown" "$(tail -1 "$T/calls")" \
  "uv pip install --exclude-newer P2D --python $REPO/.venv/bin/python raindrop-ai"

echo ""
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
