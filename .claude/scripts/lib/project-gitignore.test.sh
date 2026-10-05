#!/usr/bin/env bash
# project-gitignore.test.sh — prove the harness entries land in a project's
# .gitignore exactly once, and that a re-run is a safe no-op.
#
# Run: bash scripts/lib/project-gitignore.test.sh
#
# The re-run case is the real test: update.sh runs under `set -e` and calls
# ensure_gitignore on every sync, so a non-zero "nothing to add" return would
# abort every update of an already-configured project.

set -eu

__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/project-gitignore.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

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

# --- Case 1: pre-existing .gitignore, entries appended, re-run is a no-op ----
proj="$TMP/with-gitignore"
mkdir -p "$proj"
printf 'node_modules/\n' > "$proj/.gitignore"

ensure_gitignore "$proj" > /dev/null
before="$(cat "$proj/.gitignore")"
ensure_gitignore "$proj" > /dev/null   # must not abort under set -e
after="$(cat "$proj/.gitignore")"

check "re-run leaves the file unchanged" "$([ "$before" = "$after" ] && echo same || echo changed)" "same"
check "pre-existing content preserved" "$(grep -cxF 'node_modules/' "$proj/.gitignore")" "1"

for entry in "${SDD_GITIGNORE_ENTRIES[@]}"; do
  check "entry present exactly once: $entry" "$(grep -cxF "$entry" "$proj/.gitignore")" "1"
done

# --- Case 2: no .gitignore at all — one is created, no leading blank line ----
proj2="$TMP/no-gitignore"
mkdir -p "$proj2"
ensure_gitignore "$proj2" > /dev/null

check "creates a missing .gitignore" "$([ -f "$proj2/.gitignore" ] && echo yes || echo no)" "yes"
check "first line is the header" "$(head -n 1 "$proj2/.gitignore")" "# SDD harness — local-only, never committed"

# --- Case 3: partial .gitignore — only the missing entries are added ---------
proj3="$TMP/partial"
mkdir -p "$proj3"
printf '.claude/\nspecs/\nCLAUDE.md\n' > "$proj3/.gitignore"
ensure_gitignore "$proj3" > /dev/null

check "existing entry not duplicated" "$(grep -cxF '.claude/' "$proj3/.gitignore")" "1"
check "AGENTS.md added to a legacy install" "$(grep -cxF 'AGENTS.md' "$proj3/.gitignore")" "1"
check "ERRORS.md added to a legacy install" "$(grep -cxF 'ERRORS.md' "$proj3/.gitignore")" "1"

echo ""
if [ $FAIL -eq 0 ]; then
  echo "PASS — $PASS check(s)"
  exit 0
fi
echo "FAIL — $FAIL failure(s), $PASS passed"
exit 1
