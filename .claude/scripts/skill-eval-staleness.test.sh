#!/usr/bin/env bash
# Tests for scripts/skill-eval-staleness.py.
#
# The scanner's value is that it flags a verdict that no longer holds. A scanner
# that only ever prints "all current" is indistinguishable from one that does
# nothing, so most cases plant a stale verdict and assert it is named.
#
# Offline; every fixture lives in a temp skill root, never ~/.claude/skills.
#     bash scripts/skill-eval-staleness.test.sh
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SCANNER="$SCRIPT_DIR/skill-eval-staleness.py"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n     %s\n' "$1" "$2"; }

[ -f "$SCANNER" ] || { echo "missing: $SCANNER"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ROOT="$WORK/skills"

# skill <slug> <skill_md_body>
skill() {
    mkdir -p "$ROOT/$1"
    printf '%s\n' "$2" > "$ROOT/$1/SKILL.md"
}

# verdict <slug> <measured_on_model> <hash|auto>
verdict() {
    local slug="$1" model="$2" hash="$3"
    if [ "$hash" = "auto" ]; then
        hash="$(shasum -a 256 "$ROOT/$slug/SKILL.md" | cut -d' ' -f1)"
    fi
    cat > "$ROOT/$slug/eval-verdict.json" <<JSON
{
  "skill": "$slug",
  "verdict": "PASS",
  "date": "2026-01-01",
  "scenarios": 3,
  "treatment_runs_per_scenario": 3,
  "measured_on_model": "$model",
  "skill_md_sha256": "$hash"
}
JSON
}

# run <model> [extra args...] -> sets OUT and RC
run() {
    local model="$1"; shift
    OUT="$(python3 "$SCANNER" --current-model "$model" --dir "$ROOT" "$@" 2>&1)"
    RC=$?
}

# expect_contains <label> <needle>
expect_contains() {
    case "$OUT" in
        *"$2"*) ok "$1" ;;
        *) bad "$1" "expected output to contain '$2' — got: $OUT" ;;
    esac
}

# expect_missing <label> <needle>
expect_missing() {
    case "$OUT" in
        *"$2"*) bad "$1" "expected output NOT to contain '$2' — got: $OUT" ;;
        *) ok "$1" ;;
    esac
}

# expect_rc <label> <want>
expect_rc() {
    if [ "$RC" -eq "$2" ]; then ok "$1"; else bad "$1" "expected exit $2, got $RC — $OUT"; fi
}

echo "required argument"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill placeholder 'body'
OUT="$(python3 "$SCANNER" --dir "$ROOT" 2>&1)"; RC=$?
expect_rc "missing --current-model exits 2" 2
expect_contains "missing --current-model explains itself" "no default"

SDD_CURRENT_MODEL=claude-opus-5 \
  OUT="$(SDD_CURRENT_MODEL=claude-opus-5 python3 "$SCANNER" --dir "$ROOT" 2>&1)"; RC=$?
expect_rc "SDD_CURRENT_MODEL satisfies the requirement" 0

echo
echo "classification"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill current_skill 'measured on the model now running'
verdict current_skill "claude-opus-5" auto
run claude-opus-5
expect_rc "matching model is not flagged" 0
expect_contains "matching model reports all valid" "Every recorded verdict"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill old_skill 'measured on a previous model'
verdict old_skill "claude-opus-4-6" auto
run claude-opus-5
expect_contains "different model is flagged stale" "stale-model"
expect_contains "stale finding names the skill" "old_skill"
expect_contains "stale finding names the model measured on" "claude-opus-4-6"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill legacy_skill 'verdict predates the field'
cat > "$ROOT/legacy_skill/eval-verdict.json" <<'JSON'
{"skill": "legacy_skill", "verdict": "PASS", "date": "2026-01-01"}
JSON
run claude-opus-5
expect_contains "verdict with no measured_on_model is unknown-model" "unknown-model"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill literal_unknown 'field present but unknown'
verdict literal_unknown "unknown" auto
run claude-opus-5
expect_contains "literal 'unknown' is treated as unknown-model" "unknown-model"
expect_missing "literal 'unknown' is not also called stale" "stale-model"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill edited_skill 'the body that was measured'
verdict edited_skill "claude-opus-5" auto
printf '%s\n' 'a different body, edited after measurement' > "$ROOT/edited_skill/SKILL.md"
run claude-opus-5
expect_contains "edited SKILL.md is flagged hash-mismatch" "hash-mismatch"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill both_wrong 'edited AND measured on an old model'
verdict both_wrong "claude-opus-4-6" auto
printf '%s\n' 'edited after measurement' > "$ROOT/both_wrong/SKILL.md"
run claude-opus-5
expect_contains "hash-mismatch wins over stale-model" "hash-mismatch"
expect_missing "a skill carries only one finding" "stale-model"

echo
echo "ungated skills"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill vendored_a 'no eval-verdict.json at all'
skill vendored_b 'also never gated'
skill gated 'has a verdict'
verdict gated "claude-opus-5" auto
run claude-opus-5
expect_rc "skills without a verdict do not fail the scan" 0
expect_contains "ungated skills are counted" "Skills never gated:   2"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill only_ungated_a 'never gated'
skill only_ungated_b 'never gated either'
run claude-opus-5
expect_rc "a tree with no verdicts at all still exits 0" 0
expect_contains "zero verdicts says nothing was checked" "nothing to"
expect_missing "zero verdicts does not claim everything passed" "Every recorded verdict"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill broken_json 'verdict file is not valid JSON'
printf '%s\n' 'not json {' > "$ROOT/broken_json/eval-verdict.json"
run claude-opus-5
expect_rc "unparseable verdict is treated as ungated, not a crash" 0
expect_contains "unparseable verdict counts as never gated" "Skills never gated:   1"

echo
echo "exit codes"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill stale_one 'stale'
verdict stale_one "claude-opus-4-6" auto
run claude-opus-5
expect_rc "findings alone do not fail without --strict" 0
run claude-opus-5 --strict
expect_rc "--strict exits 1 on a finding" 1

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill fresh_one 'current'
verdict fresh_one "claude-opus-5" auto
run claude-opus-5 --strict
expect_rc "--strict exits 0 when nothing is flagged" 0

rm -rf "$ROOT"; mkdir -p "$ROOT"
run claude-opus-5
expect_rc "empty skill root exits 2" 2

echo
echo "json output"

rm -rf "$ROOT"; mkdir -p "$ROOT"
skill j_stale 'stale'
verdict j_stale "claude-opus-4-6" auto
skill j_fresh 'current'
verdict j_fresh "claude-opus-5" auto
run claude-opus-5 --json
expect_rc "--json exits 0" 0
if printf '%s' "$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["flagged"]==1 and d["current"]==1 and d["findings"]["stale-model"]==["j_stale"] else 1)'; then
    ok "--json reports counts and findings"
else
    bad "--json reports counts and findings" "unexpected payload: $OUT"
fi

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
