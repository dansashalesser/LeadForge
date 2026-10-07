#!/usr/bin/env bash
# token-forensics.test.sh — cache-bust detection and cost weighting.
#
# The cache-bust detector reporting 0 on real data is ambiguous: it could mean
# "no session switched model" or "the detector never fires". These cases plant a
# known switch so a real positive is proven, and plant a <synthetic> placeholder
# so the false-positive filter is proven too.
#
# Run: bash scripts/utils/token-forensics.test.sh

set -uo pipefail

HERE="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
TOOL="$HERE/token-forensics.py"
PASS=0
FAIL=0

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# token-forensics scans ~/.claude/projects, so point HOME at a throwaway tree.
PROJ="$WORK/.claude/projects/fake-repo"
mkdir -p "$PROJ"

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Emit one assistant line. Args: seq, session, model, cache_create, cache_read
#
# `seq` must be unique per line. token-forensics deduplicates on requestId (that
# is the whole point of its "naive vs deduplicated" figure), so two lines sharing
# one id collapse into a single request — correct behavior, but it silently
# changes the expected totals below if the ids are derived from the payload.
line() {
  python3 -c '
import json, sys
seq, sid, model, cc, cr, ts = sys.argv[1:7]
print(json.dumps({
    "type": "assistant",
    "timestamp": ts,
    "sessionId": sid,
    "requestId": f"req-{seq}",
    "message": {
        "id": f"msg-{seq}",
        "model": model,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": int(cr),
            "cache_creation_input_tokens": int(cc),
        },
    },
}))' "$1" "$2" "$3" "$4" "$5" "$NOW"
}

# Emit one hook attachment line. Args: class, hookName, command, content
hook() {
  python3 -c '
import json, sys
cls, name, command, content, ts = sys.argv[1:6]
att = {"type": cls, "hookName": name, "hookEvent": name.split(":")[0], "content": content}
if command:
    att["command"] = command
print(json.dumps({"type": "attachment", "timestamp": ts, "attachment": att}))' \
    "$1" "$2" "$3" "$4" "$NOW"
}

# Session A: opus → haiku mid-session, with a large re-prefill. One real bust.
# Also carries one hook of each class, at known positions, so amplification
# (chars x requests that followed) has an exact expected value.
{
  hook hook_additional_context SessionStart "" "$(printf 'x%.0s' $(seq 1 100))"
  line 1 sessA claude-opus-5 1000 5000
  line 2 sessA claude-opus-5 500 6000
  hook hook_success UserPromptSubmit \
    'bash "/tmp/hooks/prompt-quality-check.sh"' "$(printf 'y%.0s' $(seq 1 40))"
  line 3 sessA claude-haiku-4-5-20251001 50000 100
} > "$PROJ/sessA.jsonl"

# Session B: never switches, but has <synthetic> placeholders interleaved.
# Must produce ZERO busts — this is the false-positive guard.
{
  line 4 sessB claude-opus-5 1000 5000
  line 5 sessB "<synthetic>" 0 0
  line 6 sessB claude-opus-5 200 7000
  line 7 sessB "<synthetic>" 0 0
} > "$PROJ/sessB.jsonl"

OUT="$(HOME="$WORK" python3 "$TOOL" --days 0 --json 2>/dev/null)"

jqv() { printf '%s' "$OUT" | jq -r "$1"; }

check() {
  local label="$1" want="$2" got="$3"
  if [ "$want" = "$got" ]; then
    echo "  ok    $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $label — wanted '$want', got '$got'"
    FAIL=$((FAIL + 1))
  fi
}

echo "token-forensics cache-bust + cost weighting"

# ── Cache-bust detection actually fires ──────────────────────────────────────
check "detects exactly one real model switch" "1" "$(jqv '.cache_bust_count')"
check "names the model switched from" "claude-opus-5" "$(jqv '.cache_busts[0].from_model')"
check "names the model switched to" \
  "claude-haiku-4-5-20251001" "$(jqv '.cache_busts[0].to_model')"
check "records the re-prefill size" "50000" "$(jqv '.cache_busts[0].reprefill_tokens')"
check "flags a re-prefill over the threshold" "true" "$(jqv '.cache_busts[0].large_reprefill')"
check "attributes the bust to the right session" "sessA" "$(jqv '.cache_busts[0].session')"

# ── <synthetic> placeholders are not model switches ──────────────────────────
check "no bust attributed to sessB" "" \
  "$(printf '%s' "$OUT" | jq -r '[.cache_busts[] | select(.session=="sessB")] | .[].session' )"
check "<synthetic> excluded from models_seen" "false" \
  "$(printf '%s' "$OUT" | jq -r '.models_seen | has("<synthetic>")')"
check "real models still counted" "true" \
  "$(printf '%s' "$OUT" | jq -r '.models_seen | has("claude-opus-5")')"

# ── Cost weighting ───────────────────────────────────────────────────────────
# 5 real assistant turns + 2 synthetic. Totals: input 7*10=70, output 7*20=140,
# cache_read 5000+6000+100+5000+0+7000+0=23100, cache_create 1000+500+50000+1000+0+200+0=52700
# weighted = 70*1 + 140*5 + 23100*0.1 + 52700*2 = 70 + 700 + 2310 + 105400 = 108480
check "weighted cost applies the price ratios" "true" "$(jqv '.weighted_cost == 108480')"
check "cache_read weight is 0.1" "true" "$(jqv '.cost_weights.cache_read == 0.1')"
check "output weight is 5" "true" "$(jqv '.cost_weights.output == 5')"
check "cache_create weight is 2" "true" "$(jqv '.cost_weights.cache_create == 2')"
check "breakdown percentages sum to ~100" "100" \
  "$(printf '%s' "$OUT" | jq -r '[.cost_breakdown[].pct_of_cost] | add | round')"

# ── Hook injection attribution ───────────────────────────────────────────────
# additional_context: 100 chars before request 1 → 3 requests follow → 300.
# success: 40 chars after request 2 → 1 request follows → 40.
check "additional_context hook counted" "100" \
  "$(jqv '.hooks_by_amplified_chars.hook_additional_context[0].injected_chars')"
check "additional_context amplified by requests after it" "300" \
  "$(jqv '.hooks_by_amplified_chars.hook_additional_context[0].amplified_chars')"
check "hook_success labelled with its script" "UserPromptSubmit prompt-quality-check.sh" \
  "$(jqv '.hooks_by_amplified_chars.hook_success[0].hook')"
check "hook_success amplified by requests after it" "40" \
  "$(jqv '.hooks_by_amplified_chars.hook_success[0].amplified_chars')"
check "hook lines do not change token totals" "true" "$(jqv '.weighted_cost == 108480')"

echo
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
