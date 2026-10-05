#!/usr/bin/env bash
# startup-payload-audit.test.sh — the ceiling ratchet and @import resolution.
#
# The ratchet's whole value is that it only moves down, so each case plants a
# known payload size and checks where the ceiling lands: initialized on the first
# run, lowered on shrink, held-and-flagged on growth, reset by --rebaseline.
#
# Run: bash scripts/routines/startup-payload-audit.test.sh

set -uo pipefail

HERE="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
AUDIT="$HERE/startup-payload-audit.sh"
PASS=0
FAIL=0

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

REPO="$WORK/repo"
mkdir -p "$REPO/.claude/memory"
REPORT="$REPO/.claude/reports/context/startup-payload.json"

# Write a CLAUDE.md of exactly N tokens (chars/4) plus one real and one ghost import.
payload() {
  printf '%*s' "$(( $1 * 4 ))" '' | tr ' ' 'x' > "$REPO/CLAUDE.md"
}

audit() {
  # HOME isolates the auto-memory MEMORY.md lookup from the real one.
  (cd "$REPO" && HOME="$WORK" bash "$AUDIT" "$@" 2>"$WORK/stderr")
}

field() { jq -r ".$1" "$REPORT"; }

check() {
  local label="$1" want="$2" got="$3"
  if [ "$want" = "$got" ]; then
    echo "  ok    $label"; PASS=$((PASS + 1))
  else
    echo "  FAIL  $label — wanted '$want', got '$got'"; FAIL=$((FAIL + 1))
  fi
}

echo "startup-payload-audit ratchet"

payload 1000
audit --force >/dev/null
check "first run initializes the ceiling" "initialized" "$(field ceiling_event)"
check "ceiling starts at the total" "1000" "$(field ceiling)"
check "no delta without a previous ceiling" "null" "$(field delta)"

payload 900
audit --force >/dev/null
check "shrink lowers the ceiling" "900" "$(field ceiling)"
check "shrink is reported as lowered" "lowered" "$(field ceiling_event)"
check "delta is negative on shrink" "-100" "$(field delta)"

payload 950
audit --force >/dev/null
check "growth is flagged" "true" "$(field over_ceiling)"
check "growth does not raise the ceiling" "900" "$(field ceiling)"
check "delta shows the growth" "50" "$(field delta)"
check "growth warns on stderr" "yes" "$(grep -q 'past its ceiling' "$WORK/stderr" && echo yes || echo no)"

audit --force >/dev/null
check "ceiling file kept the old ceiling after growth" "true" "$(field over_ceiling)"

audit --rebaseline >/dev/null
check "--rebaseline accepts the growth" "950" "$(field ceiling)"
check "--rebaseline is reported" "rebaselined" "$(field ceiling_event)"
check "--rebaseline clears the flag" "false" "$(field over_ceiling)"

# ── @import resolution (regex-free parser) ───────────────────────────────────
echo "extra" > "$REPO/real.md"
{ printf 'x\n  @real.md\n@missing.md\nnot @inline.md\n@ \n'; } > "$REPO/CLAUDE.md"
audit --rebaseline >/dev/null
check "indented @import resolves" "true" \
  "$(jq -r '[.files[].path] | index("@real.md") != null' "$REPORT")"
check "missing @import is a ghost; inline and bare @ are not imports" '["@missing.md"]' \
  "$(jq -c '.ghosts' "$REPORT")"

echo "not json" > "$REPO/.claude/reports/context/startup-payload-ceiling.json"
audit --force >/dev/null
check "corrupt ceiling fails instead of silently rebaselining" "1" "$?"

echo
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
