#!/usr/bin/env bash
# dashboard-usage-dedup.test.sh — prove _parse_session_file collapses repeated
# usage blocks instead of summing them.
#
# Run: bash scripts/utils/dashboard-usage-dedup.test.sh
#
# Background: one API response lands in the transcript as one JSONL line per
# CONTENT BLOCK, and every one of those lines repeats the identical
# message.usage object. The pre-2026-08 parser summed line-by-line, inflating
# token totals ~83% on this machine's own logs and carrying that error into the
# USD figures. If a future edit reverts to naive summation, case 1 fails first.
#
# The last case is a format-drift canary, not a correctness test: it reads real
# transcripts and asserts duplicates actually exist. If Claude Code stops
# repeating usage per block (or drops requestId), the dedup silently becomes a
# no-op and that case tells you the parser needs revisiting. It SKIPs rather
# than fails when no transcripts are present, so CI on a clean box still passes.

set -u

__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
DASHBOARD="$__here/dashboard.py"
PASS=0
FAIL=0
SKIP=0

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

check() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    PASS=$((PASS + 1))
    printf '  ok    %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    printf '  FAIL  %s (expected %s, got %s)\n' "$label" "$expected" "$actual"
  fi
}

# Emit one assistant transcript line. $1=id-field-json (may be empty), rest=usage.
line() {
  local ids="$1" inp="$2" out="$3"
  printf '{"type":"assistant","timestamp":"2026-08-30T00:00:00Z"%s,"message":{"model":"claude-opus-5","usage":{"input_tokens":%s,"output_tokens":%s,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}\n' \
    "$ids" "$inp" "$out"
}

# ── Fixtures ──────────────────────────────────────────────────────────────────

# One response, three content blocks, identical usage repeated.
{
  line ',"requestId":"req_A"' 1000 50
  line ',"requestId":"req_A"' 1000 50
  line ',"requestId":"req_A"' 1000 50
} > "$TMP/one_response.jsonl"

# Three distinct responses.
{
  line ',"requestId":"req_A"' 1000 50
  line ',"requestId":"req_B"' 1000 50
  line ',"requestId":"req_C"' 1000 50
} > "$TMP/three_responses.jsonl"

# No requestId — must fall back to message.id.
{
  printf '{"type":"assistant","message":{"id":"msg_A","model":"claude-opus-5","usage":{"input_tokens":1000,"output_tokens":50}}}\n'
  printf '{"type":"assistant","message":{"id":"msg_A","model":"claude-opus-5","usage":{"input_tokens":1000,"output_tokens":50}}}\n'
} > "$TMP/msg_id.jsonl"

# Neither id present — lines must NOT be merged into one another.
{
  printf '{"type":"assistant","message":{"model":"claude-opus-5","usage":{"input_tokens":1000,"output_tokens":50}}}\n'
  printf '{"type":"assistant","message":{"model":"claude-opus-5","usage":{"input_tokens":1000,"output_tokens":50}}}\n'
} > "$TMP/no_id.jsonl"

# Same response, later block carries the larger output count — keep the max.
{
  line ',"requestId":"req_A"' 1000 10
  line ',"requestId":"req_A"' 1000 999
} > "$TMP/max_output.jsonl"

# ── Driver ────────────────────────────────────────────────────────────────────

cat > "$TMP/drive.py" <<'PY'
import importlib.util, json, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("dash", sys.argv[1])
dash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dash)

tmp = Path(sys.argv[2])
out = {}
for name in ("one_response", "three_responses", "msg_id", "no_id", "max_output"):
    out[name] = dash._parse_session_file(tmp / f"{name}.jsonl", "test")

# Format-drift canary against real transcripts.
projects = Path.home() / ".claude" / "projects"
real_files = sorted(projects.glob("*/*.jsonl"))[-200:] if projects.exists() else []
collapsed = 0
naive = dedup = 0
for f in real_files:
    try:
        s = dash._parse_session_file(f, "real")
    except Exception:
        continue
    if s:
        collapsed += s["collapsed"]
        dedup += s["input"] + s["output"] + s["cache_read"] + s["cache_create"]
for f in real_files:
    try:
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            o = json.loads(ln)
            if o.get("type") != "assistant":
                continue
            u = (o.get("message") or {}).get("usage") or {}
            naive += sum(u.get(k, 0) or 0 for k in (
                "input_tokens", "output_tokens",
                "cache_read_input_tokens", "cache_creation_input_tokens"))
    except Exception:
        continue

out["_real"] = {"files": len(real_files), "collapsed": collapsed,
                "naive": naive, "dedup": dedup}
print(json.dumps(out))
PY

RESULT="$(python3 "$TMP/drive.py" "$DASHBOARD" "$TMP")" || {
  echo "  FAIL  driver did not run"; exit 1;
}

field() { printf '%s' "$RESULT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval("d"+sys.argv[1]))' "$1"; }

echo "dashboard usage dedup"

check "3 blocks of one response counted once (input)"  "1000" "$(field '["one_response"]["input"]')"
check "3 blocks of one response counted once (output)" "50"   "$(field '["one_response"]["output"]')"
check "collapsed count reports the 2 dropped blocks"   "2"    "$(field '["one_response"]["collapsed"]')"
check "3 distinct responses are summed"                "3000" "$(field '["three_responses"]["input"]')"
check "no collapse across distinct requestIds"         "0"    "$(field '["three_responses"]["collapsed"]')"
check "falls back to message.id when requestId absent" "1000" "$(field '["msg_id"]["input"]')"
check "id-less lines are never merged together"        "2000" "$(field '["no_id"]["input"]')"
check "collision keeps the max output_tokens"          "999"  "$(field '["max_output"]["output"]')"

# ── Canary ────────────────────────────────────────────────────────────────────

REAL_FILES="$(field '["_real"]["files"]')"
REAL_COLLAPSED="$(field '["_real"]["collapsed"]')"
if [ "$REAL_FILES" -eq 0 ]; then
  SKIP=$((SKIP + 1))
  printf '  skip  format-drift canary (no transcripts in ~/.claude/projects)\n'
elif [ "$REAL_COLLAPSED" -gt 0 ]; then
  PASS=$((PASS + 1))
  NAIVE="$(field '["_real"]["naive"]')"
  DEDUP="$(field '["_real"]["dedup"]')"
  printf '  ok    format-drift canary: %s dupes across %s transcripts\n' \
    "$REAL_COLLAPSED" "$REAL_FILES"
  printf '        naive=%s dedup=%s\n' "$NAIVE" "$DEDUP"
else
  FAIL=$((FAIL + 1))
  printf '  FAIL  format-drift canary: 0 duplicates across %s transcripts —\n' "$REAL_FILES"
  printf '        transcript format likely changed; dedup is now a no-op.\n'
fi

echo
printf '%s passed, %s failed, %s skipped\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ]
