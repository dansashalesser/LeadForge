#!/usr/bin/env bash
# Tests for scripts/session/trust_score.py — consensus_delta (pass^k spread gate)
# and the apply CLI's multi-sample plumbing.
#
# Offline: every case runs against a temp HOME-like tree, never the real
# .claude/memory. Run from anywhere:
#     bash scripts/session/trust_score.test.sh
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
TARGET="$SCRIPT_DIR/trust_score.py"

PASS=0
FAIL=0

ok()   { PASS=$((PASS + 1)); printf '  ok   %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL %s\n     %s\n' "$1" "$2"; }

[ -f "$TARGET" ] || { echo "missing: $TARGET"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------------------
# Unit: consensus_delta
# ---------------------------------------------------------------------------
echo "consensus_delta"

cat > "$WORK/unit.py" <<PY
import sys
sys.path.insert(0, sys.argv[1])
from trust_score import consensus_delta, JUDGE_SPREAD_LIMIT

cases = [
    # (samples, expected_delta, expected_spread, expected_inconclusive, label)
    ([-1.0],                -1.0, None,  False, "single sample passes through, no spread"),
    ([0.0],                  0.0, None,  False, "single zero passes through"),
    ([-1.0, -1.0, -1.0],    -1.0,  0.0,  False, "unanimous k=3"),
    ([-1.0, -2.0, -1.0],    -1.0,  1.0,  False, "tight k=3 takes median"),
    ([-3.0, -2.0, -1.0],    -2.0,  2.0,  False, "spread exactly at limit still applies"),
    ([-4.0, -2.0, -1.0],     0.0,  3.0,  True,  "spread above limit -> inconclusive, delta 0"),
    ([2.0, 0.0],             1.0,  2.0,  False, "even k averages the middle pair"),
    ([3.0, 0.0],             0.0,  3.0,  True,  "even k, wide spread -> inconclusive"),
    ([4.0, -4.0, 0.0],       0.0,  8.0,  True,  "maximally split judge -> inconclusive"),
]

fails = 0
for samples, want_d, want_s, want_i, label in cases:
    got_d, got_s, got_i = consensus_delta(samples)
    if (got_d, got_s, got_i) != (want_d, want_s, want_i):
        print("MISMATCH|%s|got=%r want=%r" % (label, (got_d, got_s, got_i), (want_d, want_s, want_i)))
        fails += 1
    else:
        print("OK|%s" % label)

try:
    consensus_delta([])
except ValueError:
    print("OK|empty sample list raises ValueError")
except Exception as e:
    print("MISMATCH|empty raises ValueError|got %r" % (e,)); fails += 1
else:
    print("MISMATCH|empty raises ValueError|returned instead"); fails += 1

# The inconclusive verdict must be exactly 0.0 — not "small". A near-zero
# delta still moves a cumulative score every day it fires.
d, _, i = consensus_delta([4.0, -4.0])
if not (i and d == 0.0):
    print("MISMATCH|inconclusive delta is exactly 0.0|got %r" % d); fails += 1
else:
    print("OK|inconclusive delta is exactly 0.0")

if JUDGE_SPREAD_LIMIT <= 0:
    print("MISMATCH|JUDGE_SPREAD_LIMIT positive|got %r" % JUDGE_SPREAD_LIMIT); fails += 1
else:
    print("OK|JUDGE_SPREAD_LIMIT is positive (%s)" % JUDGE_SPREAD_LIMIT)

sys.exit(1 if fails else 0)
PY

while IFS='|' read -r status label detail; do
    case "$status" in
        OK)       ok "$label" ;;
        MISMATCH) bad "$label" "${detail:-}" ;;
    esac
done < <(python3 "$WORK/unit.py" "$SCRIPT_DIR" 2>&1)

# ---------------------------------------------------------------------------
# CLI: apply accepts repeated --delta and records the samples
# ---------------------------------------------------------------------------
echo "apply CLI"

run_apply() {
    # Runs `apply` in an isolated cwd so HISTORY/HOT_MEMORY relative paths
    # land in the temp tree, not the real repo.
    local dir="$1"; shift
    mkdir -p "$dir/.claude/memory"
    ( cd "$dir" && python3 "$TARGET" apply "$@" 2>&1 )
}

# Single --delta keeps working (backward compatibility with every existing
# caller and with every doc that shows the one-sample form).
D1="$WORK/single"
OUT="$(run_apply "$D1" --delta -1.0 --summary "one sample")"
case "$OUT" in
    *'"status": "applied"'*) ok "single --delta still applies" ;;
    *) bad "single --delta still applies" "$OUT" ;;
esac
case "$OUT" in
    *'"spread": null'*) ok "single sample reports spread null, not 0.0" ;;
    *) bad "single sample reports spread null, not 0.0" "$OUT" ;;
esac

# Three tight samples -> median applied.
D2="$WORK/tight"
OUT="$(run_apply "$D2" --delta -1.0 --delta -2.0 --delta -1.0 --summary "tight")"
case "$OUT" in
    *'"status": "applied"'*) ok "k=3 tight -> applied" ;;
    *) bad "k=3 tight -> applied" "$OUT" ;;
esac
case "$OUT" in
    *'"delta_applied": -1.0'*) ok "k=3 tight applies the median" ;;
    *) bad "k=3 tight applies the median" "$OUT" ;;
esac

# Three split samples -> inconclusive, score unchanged.
D3="$WORK/split"
OUT="$(run_apply "$D3" --delta -4.0 --delta -2.0 --delta -1.0 --summary "split")"
case "$OUT" in
    *'"status": "inconclusive"'*) ok "k=3 split -> inconclusive" ;;
    *) bad "k=3 split -> inconclusive" "$OUT" ;;
esac
case "$OUT" in
    *'"delta_applied": 0.0'*) ok "inconclusive applies 0.0" ;;
    *) bad "inconclusive applies 0.0" "$OUT" ;;
esac
case "$OUT" in
    *'"score": 20.0'*) ok "inconclusive leaves score at the default start" ;;
    *) bad "inconclusive leaves score at the default start" "$OUT" ;;
esac

# The samples must survive into the JSONL record — an inconclusive day that
# does not say what it saw is indistinguishable from a day nothing ran.
if grep -q '"samples": \[-4.0, -2.0, -1.0\]' "$D3/.claude/memory/trust-score.jsonl" 2>/dev/null; then
    ok "raw samples persisted to trust-score.jsonl"
else
    bad "raw samples persisted to trust-score.jsonl" "$(cat "$D3/.claude/memory/trust-score.jsonl" 2>&1)"
fi
if grep -q '"inconclusive": true' "$D3/.claude/memory/trust-score.jsonl" 2>/dev/null; then
    ok "inconclusive flag persisted to trust-score.jsonl"
else
    bad "inconclusive flag persisted to trust-score.jsonl" "$(cat "$D3/.claude/memory/trust-score.jsonl" 2>&1)"
fi

# Same-day idempotency must survive the multi-sample change.
OUT="$(run_apply "$D2" --delta -3.0 --delta -3.0 --delta -3.0 --summary "second run")"
case "$OUT" in
    *'"status": "skipped"'*) ok "same-day re-run still skips" ;;
    *) bad "same-day re-run still skips" "$OUT" ;;
esac

# `apply` with no --delta at all must fail loudly rather than default to 0.
D4="$WORK/nodelta"
mkdir -p "$D4/.claude/memory"
if ( cd "$D4" && python3 "$TARGET" apply --summary "x" ) >/dev/null 2>&1; then
    bad "apply without --delta exits non-zero" "exited 0"
else
    ok "apply without --delta exits non-zero"
fi

# auto-score is deterministic and must keep working through the list plumbing.
D5="$WORK/auto"
mkdir -p "$D5/.claude/memory"
OUT="$( cd "$D5" && python3 "$TARGET" auto-score 2>&1 )"
case "$OUT" in
    *'"status": "applied"'*|*'"status": "skipped"'*) ok "auto-score still runs through cmd_apply" ;;
    *) bad "auto-score still runs through cmd_apply" "$OUT" ;;
esac

# ---------------------------------------------------------------------------
echo
printf 'trust_score.test.sh: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
