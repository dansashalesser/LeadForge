#!/bin/bash
# caveman-savings-hook.sh — Stop hook: empirically estimates caveman-mode
# token savings by asking a cheap model to re-expand the last terse response
# back into normal prose, then diffing text length between the two.
#
# REGISTRATION (~/.claude/settings.json or .claude/settings.json, "Stop"):
#   { "type": "command", "command": "bash .claude/hooks/caveman-savings-hook.sh" }
#
# Why word-count estimation, not the API's usage.output_tokens:
# usage.output_tokens for the ORIGINAL turn includes any extended-thinking
# tokens that produced it — invisible tokens with no relationship to the
# rendered response length. The nested haiku expansion call does no extended
# thinking, so its usage.output_tokens is clean. Diffing "dirty" against
# "clean" produced nonsense (a real run of this hook during development
# logged -96 tokens "saved" purely from that mismatch). Fix: derive BOTH
# sides from visible response text via the same word-count heuristic
# (~1.3 tokens/word), so the comparison is internally consistent even
# though it's an estimate rather than an exact BPE token count.
#
# Gap-day guard (once per day, not once per session) — each measurement costs
# a real haiku call that reloads CLAUDE.md context (~$0.10-0.20), so this is
# bounded to one sample/day regardless of session count, same convention as
# the other .last-*-run state files in .claude/memory/.
#
# Recursion guard — the measurement call is a real `claude --print` (not
# --bare, which requires an API key not present in this environment), so it
# WILL re-fire Stop hooks on its own exit unless guarded. SDD_CAVEMAN_MEASURING
# short-circuits this hook when it's itself running inside that nested call.
set -euo pipefail

[ "${SDD_CAVEMAN_MEASURING:-0}" = "1" ] && exit 0

MEMORY_DIR=".claude/memory"
LEDGER="$MEMORY_DIR/caveman-savings.jsonl"
STATE_FILE="$MEMORY_DIR/.last-caveman-savings-run"

# --- Is caveman mode active right now? (harness flag takes precedence) ---
mode=""
if [ -f "$HOME/.claude/.caveman-active" ]; then
  mode="$(cat "$HOME/.claude/.caveman-active" 2>/dev/null || true)"
elif [ -f "$HOME/.config/caveman/config.json" ]; then
  mode="$(jq -r '.defaultMode // empty' "$HOME/.config/caveman/config.json" 2>/dev/null || true)"
fi
[ -n "$mode" ] || exit 0
[ -d "$MEMORY_DIR" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0
command -v claude >/dev/null 2>&1 || exit 0

# --- Gap-day guard ---
TODAY="$(date +%Y-%m-%d)"
if [ -f "$STATE_FILE" ] && [ "$(cat "$STATE_FILE" 2>/dev/null)" = "$TODAY" ]; then
  exit 0
fi

HOOK_INPUT="$(cat)"
TRANSCRIPT="$(echo "$HOOK_INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || true)"
[ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ] || exit 0

# --- Pull the last assistant text turn's visible rendered text ---
# (Deliberately NOT using usage.output_tokens here — see header note on
# thinking-token contamination.)
READ_RESULT="$(python3 - "$TRANSCRIPT" <<'PYEOF'
import json, sys
path = sys.argv[1]
last_text = ""
try:
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message", {})
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
                text = " ".join(p for p in parts if p)
            if text.strip():
                last_text = text
except Exception:
    pass
print(json.dumps({"text": last_text[:4000]}))
PYEOF
)"

ASSISTANT_TEXT="$(echo "$READ_RESULT" | jq -r '.text')"
[ -n "$ASSISTANT_TEXT" ] || exit 0
ACTUAL_WORDS=$(echo "$ASSISTANT_TEXT" | wc -w)
[ "$ACTUAL_WORDS" -gt 0 ] || exit 0
ACTUAL_TOKENS=$(awk -v w="$ACTUAL_WORDS" 'BEGIN{printf "%d", w*1.3}')

EXPAND_PROMPT="Rewrite the following terse, filler-stripped technical response into normal complete-sentence prose, as if a 'terse mode' were switched off. Preserve every technical fact, code block, and file reference exactly. Do not add new information, explanation, or caveats beyond restoring normal phrasing and filler words. Return ONLY the rewritten text, nothing else.

---
$ASSISTANT_TEXT"

BASELINE_OUT="$(echo "$EXPAND_PROMPT" | SDD_CAVEMAN_MEASURING=1 SDD_HEADLESS=1 \
  claude --print --model claude-haiku-4-5-20251001 --output-format json 2>/dev/null)" || exit 0
BASELINE_TEXT="$(echo "$BASELINE_OUT" | jq -r '.result // empty' 2>/dev/null || true)"
[ -n "$BASELINE_TEXT" ] || exit 0
BASELINE_WORDS=$(echo "$BASELINE_TEXT" | wc -w)
[ "$BASELINE_WORDS" -gt 0 ] || exit 0
BASELINE_TOKENS=$(awk -v w="$BASELINE_WORDS" 'BEGIN{printf "%d", w*1.3}')

SAVED=$((BASELINE_TOKENS - ACTUAL_TOKENS))
PCT="$(awk -v s="$SAVED" -v b="$BASELINE_TOKENS" 'BEGIN{ if (b>0) printf "%.1f", (s/b)*100; else print "0.0" }')"
TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

jq -nc --arg ts "$TS" --arg mode "$mode" --arg method "word-count-estimate" \
  --argjson actual "$ACTUAL_TOKENS" --argjson baseline "$BASELINE_TOKENS" \
  --argjson saved "$SAVED" --argjson pct "$PCT" \
  '{ts:$ts, mode:$mode, method:$method, actual_tokens:$actual, baseline_tokens:$baseline, saved_tokens:$saved, saved_pct:$pct}' \
  >> "$LEDGER"

echo "$TODAY" > "$STATE_FILE"
exit 0
