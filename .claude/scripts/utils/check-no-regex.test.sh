#!/usr/bin/env bash
# Tests for scripts/utils/check-no-regex.py.
#
# The guard's whole value is that it fails on a NEW violation. A guard that only
# ever prints "ok" is indistinguishable from one that does nothing, so most of
# these cases plant a violation and assert a non-zero exit.
#
# Offline; every fixture lives in a temp git repo, never the real tree.
#     bash scripts/utils/check-no-regex.test.sh
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
GUARD="$SCRIPT_DIR/check-no-regex.py"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n     %s\n' "$1" "$2"; }

[ -f "$GUARD" ] || { echo "missing: $GUARD"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# fixture <name> <content>  -> writes $WORK/<name>.sh
fixture() { printf '%s\n' "$2" > "$WORK/$1.sh"; }

# check <label> <fixture> <expected_exit>
check() {
    local label="$1" file="$2" want="$3" out got
    out="$(python3 "$GUARD" "$WORK/$file.sh" 2>&1)"
    got=$?
    if [ "$got" -eq "$want" ]; then
        ok "$label"
    else
        bad "$label" "expected exit $want, got $got — $out"
    fi
}

echo "detection"

fixture clean_no_python 'echo hello
grep -q foo bar.txt'
check "shell with no python is clean" clean_no_python 0

fixture clean_python 'python3 - <<PY
print("no regex here")
PY'
check "embedded python without regex is clean" clean_python 0

fixture uses_import 'python3 - <<PY
import re
print(re)
PY'
check "import re is caught" uses_import 1

fixture uses_import_multi 'python3 - <<PY
import re, os
print(os)
PY'
check "import re, os is caught" uses_import_multi 1

fixture uses_search 'python3 - <<PY
print(re.search("x", "y"))
PY'
check "re.search( is caught" uses_search 1

fixture uses_sub 'python3 - <<PY
print(re.sub("a", "b", "c"))
PY'
check "re.sub( is caught" uses_sub 1

fixture uses_regex_pkg 'python3 - <<PY
import regex
print(regex)
PY'
check "import regex is caught" uses_regex_pkg 1

echo "false-positive resistance"

# `require.sub(` contains the literal substring `re.sub(`. This is the exact
# trap that made the first draft of this guard flag its own token list.
fixture require_sub 'python3 - <<PY
print(require.sub("a"))
PY'
check "require.sub( is NOT a regex call" require_sub 0

fixture require_search 'python3 - <<PY
print(require.search("a"))
PY'
check "require.search( is NOT a regex call" require_search 0

fixture import_requests 'python3 - <<PY
import requests
print(requests)
PY'
check "import requests is NOT import re" import_requests 0

fixture core_dot 'python3 - <<PY
print(score.split("a"))
PY'
check "score.split( is NOT re.split(" core_dot 0

# A commented-out violation is not a violation — the guard strips whole-line
# comments, which is what lets a file document the ban without tripping it.
fixture commented 'python3 - <<PY
# import re
# print(re.search("x", "y"))
print("clean")
PY'
check "commented-out regex does not trip the guard" commented 0

# ...and this is why: the rewritten hook documents `import re` in its header.
fixture doc_header '# This file used to call re.search() before the rewrite.
# import re
python3 - <<PY
print("clean")
PY'
check "header comment describing the old regex is fine" doc_header 0

# Regex outside a python-invoking file is out of scope (grep -E is not banned).
fixture grep_only 'grep -E "^[0-9]+$" file.txt'
check "grep -E without python is out of scope" grep_only 0

echo "test-file exclusion"

# This very file contains `import re` and `re.search(` as fixture text. If
# *.test.sh were in scope, the guard would fail on its own test suite.
printf 'python3 - <<PY\nimport re\nprint(re.search("x","y"))\nPY\n' > "$WORK/thing.test.sh"
OUT="$(python3 "$GUARD" "$WORK/thing.test.sh" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then
    ok "*.test.sh is excluded from scanning"
else
    bad "*.test.sh is excluded from scanning" "exit $RC — $OUT"
fi

OUT="$(cd "$SCRIPT_DIR/../.." && python3 "$GUARD" scripts/utils/check-no-regex.test.sh 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then
    ok "the guard does not fail on its own test suite"
else
    bad "the guard does not fail on its own test suite" "exit $RC — $OUT"
fi

echo "repo-wide behaviour"

OUT="$(cd "$SCRIPT_DIR/../.." && python3 "$GUARD" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then
    ok "full repo scan passes (ledger covers all current debt)"
else
    bad "full repo scan passes" "exit $RC — $OUT"
fi
case "$OUT" in
    *"known debt file(s) on the ledger"*) ok "full scan reports the ledger count" ;;
    *) bad "full scan reports the ledger count" "$OUT" ;;
esac

# The file this guard was built alongside must be clean.
OUT="$(cd "$SCRIPT_DIR/../.." && python3 "$GUARD" hooks/claude/test-integrity-guard.sh 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then
    ok "test-integrity-guard.sh is clean (regex removed 2026-09-03)"
else
    bad "test-integrity-guard.sh is clean" "exit $RC — $OUT"
fi

# A ledgered file must still be reported as debt, not silently forgotten.
OUT="$(cd "$SCRIPT_DIR/../.." && python3 "$GUARD" --list 2>&1)"
case "$OUT" in
    *"hooks/claude/stop-hook.sh"*) ok "--list still names ledgered debt files" ;;
    *) bad "--list still names ledgered debt files" "$OUT" ;;
esac

echo
printf 'check-no-regex.test.sh: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
