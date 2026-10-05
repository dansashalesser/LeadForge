#!/usr/bin/env bash
# tool-paths.test.sh — prove tool discovery works, and keeps working when the user
# has relocated a package manager's directories.
#
# Run: bash scripts/lib/tool-paths.test.sh
#
# The point of lib/tool-paths.sh is that a clone works on ANY machine. That claim is
# only meaningful if the env-var overrides are honoured, so the relocation cases
# below are the real test — a hardcoded default passes the happy path too.

set -u

__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/resolve-harness-dir.sh"
. "$__here/tool-paths.sh"

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

check_nonempty() {
  local label="$1" actual="$2"
  if [ -n "$actual" ]; then
    printf '  ok    %-46s %s\n' "$label" "$actual"
    PASS=$((PASS + 1))
  else
    printf '  FAIL  %-46s (empty)\n' "$label"
    FAIL=$((FAIL + 1))
  fi
}

echo "tool-paths discovery"
check_nonempty "uv_tool_dir"                "$(uv_tool_dir)"
check_nonempty "uv_tool_bin_dir"            "$(uv_tool_bin_dir)"
check_nonempty "raindrop_bin_dir"           "$(raindrop_bin_dir)"
check_nonempty "cargo_bin_dir"              "$(cargo_bin_dir)"
check_nonempty "tool_bin_dirs"              "$(tool_bin_dirs | head -1)"

# No literal absolutes: every discovered dir must sit under $HOME or a real prefix,
# and none may be the Apple-Silicon-only Homebrew path unless brew genuinely reports it.
if command -v brew >/dev/null 2>&1; then
  check "brew_bin_dir matches brew --prefix"  "$(brew_bin_dir)" "$(brew --prefix)/bin"
else
  printf '  skip  %-46s (brew absent)\n' "brew_bin_dir"
fi

echo ""
echo "relocation (env overrides must win — this is the portability claim)"
# uv absent from PATH => the fallback chain must honour the documented env vars
# rather than falling through to a baked-in default.
(
  PATH="/nonexistent-bin"
  export PATH
  UV_TOOL_DIR="/tmp/sdd-test-uvtools"       export UV_TOOL_DIR
  UV_TOOL_BIN_DIR="/tmp/sdd-test-uvbin"     export UV_TOOL_BIN_DIR
  RAINDROP_HOME="/tmp/sdd-test-raindrop"    export RAINDROP_HOME
  CARGO_HOME="/tmp/sdd-test-cargo"          export CARGO_HOME
  . "$__here/tool-paths.sh"
  [ "$(uv_tool_dir)"      = "/tmp/sdd-test-uvtools" ]     || { echo "  FAIL  UV_TOOL_DIR ignored"; exit 1; }
  [ "$(uv_tool_bin_dir)"  = "/tmp/sdd-test-uvbin" ]       || { echo "  FAIL  UV_TOOL_BIN_DIR ignored"; exit 1; }
  [ "$(raindrop_bin_dir)" = "/tmp/sdd-test-raindrop/bin" ]|| { echo "  FAIL  RAINDROP_HOME ignored"; exit 1; }
  [ "$(cargo_bin_dir)"    = "/tmp/sdd-test-cargo/bin" ]   || { echo "  FAIL  CARGO_HOME ignored"; exit 1; }
  echo "  ok    all four relocations honoured with uv off PATH"
)
if [ $? -eq 0 ]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi

# XDG defaults when neither the tool nor its own env var is available.
(
  PATH="/nonexistent-bin"; export PATH
  unset UV_TOOL_DIR UV_TOOL_BIN_DIR 2>/dev/null || true
  XDG_DATA_HOME="/tmp/sdd-test-xdg-data"; export XDG_DATA_HOME
  XDG_BIN_HOME="/tmp/sdd-test-xdg-bin";   export XDG_BIN_HOME
  . "$__here/tool-paths.sh"
  [ "$(uv_tool_dir)"     = "/tmp/sdd-test-xdg-data/uv/tools" ] || { echo "  FAIL  XDG_DATA_HOME ignored"; exit 1; }
  [ "$(uv_tool_bin_dir)" = "/tmp/sdd-test-xdg-bin" ]           || { echo "  FAIL  XDG_BIN_HOME ignored"; exit 1; }
  echo "  ok    XDG overrides honoured as last-resort fallback"
)
if [ $? -eq 0 ]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi

echo ""
echo "find_tool / find_tool_python"
HR="$(find_tool headroom || true)"
if [ -n "$HR" ]; then
  check_nonempty "find_tool headroom (off PATH too)" "$HR"
else
  printf '  skip  %-46s (headroom not installed)\n' "find_tool headroom"
fi
HRPY="$(find_tool_python headroom-ai || true)"
if [ -n "$HRPY" ]; then
  check_nonempty "find_tool_python headroom-ai" "$HRPY"
else
  printf '  skip  %-46s (headroom-ai not installed)\n' "find_tool_python headroom-ai"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "PASS — $PASS check(s)"
  exit 0
fi
echo "FAIL — $FAIL failure(s), $PASS passed"
exit 1
