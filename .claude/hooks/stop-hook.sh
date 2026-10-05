#!/bin/bash
# SDD Harness stop hook
# 1. Check if harness has updates since last install
# 2. Nudge /kiro:housekeeping if observations.md > 50 entries
# 3. Nudge when cache-cost dominates spend after compaction (writes handoff automatically)

# Claude Code passes {transcript_path, ...} JSON on stdin for Stop hooks.
# Read once, up front — before any early-exit path — so it's available below.
HOOK_INPUT=$(cat 2>/dev/null || true)
TRANSCRIPT_PATH=$(printf '%s' "$HOOK_INPUT" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('transcript_path', ''))
except Exception:
    print('')
" 2>/dev/null)

# Profile guard — skip all checks if profile is minimal
SDD_PROFILE="${SDD_PROFILE:-standard}"
if [ "$SDD_PROFILE" = "minimal" ]; then
  exit 0
fi

# Locate the harness via the single stored pointer (see scripts/lib/harness-pointer.sh).
# A *missing* pointer means the harness was never installed globally — silent skip is
# correct. A pointer that names a directory which no longer exists means the harness
# MOVED, and every cross-repo hook on this machine is dead. That used to be an
# indistinguishable `exit 0`, so a move could disable the harness fleet-wide with no
# symptom for weeks. Say so instead.
POINTER_RAW="$(cat "$HOME/.sdd-harness-root" 2>/dev/null || true)"
if [ -z "$POINTER_RAW" ]; then
  exit 0
fi
if [ ! -d "$POINTER_RAW" ]; then
  echo "[HARNESS-POINTER-STALE] ~/.sdd-harness-root points at $POINTER_RAW, which does not exist."
  echo "  The harness has moved. Cross-repo hooks are inactive until you re-run:"
  echo "  bash <harness>/update.sh"
  exit 0
fi
HARNESS_DIR="$POINTER_RAW"
LAST_CHECK_FILE=".claude/.last-harness-check"

# --- Harness update check ---
if [ -d "$HARNESS_DIR/.git" ]; then
  HARNESS_LAST_COMMIT=$(cd "$HARNESS_DIR" && git log -1 --format="%aI" 2>/dev/null)
  if [ -f "$LAST_CHECK_FILE" ]; then
    LAST_CHECK=$(cat "$LAST_CHECK_FILE" | tr -d '[:space:]')
    if [[ "$HARNESS_LAST_COMMIT" > "$LAST_CHECK" ]]; then
      echo ""
      echo "SDD harness has updates since your last install."
      echo "Run: $HARNESS_DIR/update.sh"
      echo ""
    fi
  fi
fi

# --- Memory health check ---
OBS_FILE=".claude/memory/observations.md"
if [ -f "$OBS_FILE" ]; then
  ENTRY_COUNT=$(grep -c "^- [0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}" "$OBS_FILE" 2>/dev/null || echo 0)
  if [ "$ENTRY_COUNT" -gt 50 ]; then
    echo ""
    echo "observations.md has $ENTRY_COUNT entries (limit: 50)."
    echo "Run: /kiro:housekeeping"
    echo ""
  fi
fi

# --- Memory-gap detection (re-explanation signal) ---
# Scan the current session's transcript for phrases indicating the user had to
# re-explain context — each hit = a memory the harness should have saved.
#
# The detector classifies via `claude --print`, i.e. a nested headless session,
# which fires this same hook when it ends. SDD_HEADLESS marks those children (the
# detector sets it, as do scripts/routines/*-runner.sh), so skipping here is what
# stops the recursion. It also keeps routine sessions — which have no user in them
# to re-explain anything — out of the measurement.
#
# Failures are NOT silent: the detector emits a `[detector-down]` observation and
# exits 4. A swallowed failure showed up on the dashboard as "0 memory gaps",
# which is indistinguishable from a clean session, and it stayed that way for the
# whole time the API-key-based detector was deployed without a key.
DETECTOR=".claude/scripts/session/detect_reexplanation.py"
if [ "${SDD_HEADLESS:-0}" != "1" ] && [ -f "$DETECTOR" ] && [ -f "$OBS_FILE" ]; then
  (
    today=$(date +%Y-%m-%d)
    # Idempotency guard: one detector verdict per day, success or failure.
    if ! grep -qF -e "- $today [memory-gap]:" -e "- $today [detector-down]:" "$OBS_FILE" 2>/dev/null; then
      SDD_HEADLESS=1 python3 "$DETECTOR" --auto-transcript --emit observation --record-metric >> "$OBS_FILE" 2>/dev/null
    fi
  ) &
fi

# --- Session depth tracking (context health) ---
SESSION_HISTORY=".claude/memory/.session-history"
if [ -d ".claude/memory" ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$SESSION_HISTORY"
  if [ -f "$SESSION_HISTORY" ]; then
    tail -30 "$SESSION_HISTORY" > "${SESSION_HISTORY}.tmp" && mv "${SESSION_HISTORY}.tmp" "$SESSION_HISTORY"
  fi
fi

# --- Agent failure pattern detection (self-tightening loop) ---
TRACE_LOG=".claude/memory/trace.log"
if [ -f "$TRACE_LOG" ]; then
  # Check for 3+ consecutive failures for the same agent type
  REPEAT_FAILURES=$(awk -F'|' '
    {
      gsub(/^[ \t]+|[ \t]+$/, "", $2); agent=$2
      gsub(/^[ \t]+|[ \t]+$/, "", $4); outcome=$4
    }
    outcome ~ /fail|error|no-go/ {
      if (agent == last_agent) { count++ } else { count=1 }
      last_agent=agent
      if (count >= 3) { print agent; found=1; exit }
    }
  ' "$TRACE_LOG" 2>/dev/null)

  if [ -n "$REPEAT_FAILURES" ]; then
    echo ""
    echo "Agent '$REPEAT_FAILURES' has 3+ consecutive failures in trace.log."
    echo "Run: /kiro:evolve to investigate friction patterns and propose improvements."
    echo ""
  fi
fi

# --- Loop-debt detection (cognitive surrender proxy) ---
# Emit [loop-debt] if skill updates ran ≥3 days ago with no session-charge since.
# Signal: loop shipped improvements the human never engaged with.
if [ -f "$OBS_FILE" ]; then
  today=$(date +%Y-%m-%d)
  if ! grep -q "^- $today \[loop-debt\]:" "$OBS_FILE" 2>/dev/null; then
    python3 - "$OBS_FILE" "$today" <<'PYEOF' >> "$OBS_FILE" 2>/dev/null || true
import sys, re, datetime
obs_path, today_str = sys.argv[1], sys.argv[2]
today = datetime.date.fromisoformat(today_str)
pat = re.compile(r"^- (\d{4}-\d{2}-\d{2}) \[([^\]]+)\]:")
last_update = last_charge = None
for line in open(obs_path).read().splitlines():
    m = pat.match(line)
    if not m:
        continue
    d = datetime.date.fromisoformat(m.group(1))
    tag = m.group(2)
    if tag == "skill-update" and (last_update is None or d > last_update):
        last_update = d
    if tag == "session-charge" and (last_charge is None or d > last_charge):
        last_charge = d
if last_update is None:
    sys.exit(0)
days_since = (today - last_update).days
if days_since < 3:
    sys.exit(0)
if last_charge and last_charge >= last_update:
    sys.exit(0)
print(f"- {today_str} [loop-debt]: skill updates from {last_update} ({days_since}d ago) with no session-charge since — possible cognitive surrender")
PYEOF
  fi
fi

# --- learnings.jsonl promoter (deterministic fallback for reflect-agent Step 6) ---
# reflect-agent's Step 6 tells the LLM to append a curated learning, but that's a
# prose instruction buried at the end of a 6-step agent — attention decays, so the
# file was never actually created. This mechanically promotes today's
# highest-signal observation if reflect-agent didn't already write one today.
# Best-effort only: reflect-agent's own curated entry always wins (idempotency
# guard checks the date first, so a manual write earlier today skips this).
LEARN_FILE=".claude/memory/learnings.jsonl"
if [ -f "$OBS_FILE" ] && [ -d ".claude/memory" ]; then
  today=$(date +%Y-%m-%d)
  if ! grep -q "\"date\": \"$today\"" "$LEARN_FILE" 2>/dev/null; then
    python3 - "$OBS_FILE" "$LEARN_FILE" "$today" <<'PYEOF' 2>/dev/null || true
import sys, re, json, datetime
obs_path, learn_path, today = sys.argv[1], sys.argv[2], sys.argv[3]
pat = re.compile(r"^- (\d{4}-\d{2}-\d{2}) \[([^\]]+)\]:\s*(.*)$")
PRIORITY = ["judge", "skill-update", "skill-update-flagged", "skill-update-repair",
            "seed-target", "memory-gap", "loop-debt", "stale-action-item", "routine-note"]

def rank(tag):
    base = tag.split(":")[0]
    return PRIORITY.index(base) if base in PRIORITY else len(PRIORITY)

todays = []
for line in open(obs_path).read().splitlines():
    m = pat.match(line)
    if m and m.group(1) == today:
        todays.append((m.group(2), m.group(3)))
if not todays:
    sys.exit(0)
todays.sort(key=lambda t: rank(t[0]))
tag, text = todays[0]
if rank(tag) == len(PRIORITY):
    sys.exit(0)  # low-signal tag only (e.g. routine-note noise) — not worth promoting
entry = {
    "date": today,
    "situation": f"[{tag}] {text[:160]}",
    "insight": text[:200],
    "applies_when": f"a future run encounters a [{tag}]-tagged situation similar to this",
}
with open(learn_path, "a") as f:
    f.write(json.dumps(entry) + "\n")
PYEOF
  fi
fi

# --- Stale action-item escalator ---
# action-items.md due-dates were never mechanically checked — items past due sat
# silently until a human happened to re-read the file. This surfaces them as
# observations so they flow through the same review loop as everything else.
ACTION_FILE=".claude/memory/action-items.md"
if [ -f "$ACTION_FILE" ] && [ -f "$OBS_FILE" ]; then
  today=$(date +%Y-%m-%d)
  if ! grep -q "^- $today \[stale-action-item\]:" "$OBS_FILE" 2>/dev/null; then
    python3 - "$ACTION_FILE" "$OBS_FILE" "$today" <<'PYEOF' >> "$OBS_FILE" 2>/dev/null || true
import sys, re, datetime
action_path, obs_path, today_str = sys.argv[1], sys.argv[2], sys.argv[3]
today = datetime.date.fromisoformat(today_str)
pat = re.compile(r"^- \[ \] (.+?) \| due:(\d{4}-\d{2}-\d{2})")
stale = []
for line in open(action_path).read().splitlines():
    m = pat.match(line)
    if not m:
        continue
    desc, due_str = m.group(1), m.group(2)
    due = datetime.date.fromisoformat(due_str)
    if due < today:
        stale.append((due, desc))
if not stale:
    sys.exit(0)
stale.sort()
due, desc = stale[0]
days_over = (today - due).days
print(f"- {today_str} [stale-action-item]: '{desc[:100]}' overdue {days_over}d (due {due}) — see .claude/memory/action-items.md")
PYEOF
  fi
fi

# --- Cache-cost dominance nudge (long-session cost blowup) ---
# Article: sessions that compact at least once and run cache-read+write >=70%
# of total tokens are cache-cost-dominated. A nudge alone is a warning nobody
# acts on — instead this writes a resumable handoff snapshot unconditionally,
# the moment the threshold trips, so the next session/model can pick up free
# of the human having to notice or remember to run anything.
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ] && [ -f "$OBS_FILE" ]; then
  today=$(date +%Y-%m-%d)
  if ! grep -q "^- $today \[cache-cost\]:" "$OBS_FILE" 2>/dev/null; then
    CACHE_COST_RESULT=$(python3 - "$TRANSCRIPT_PATH" <<'PYEOF' 2>/dev/null
import sys, json
transcript_path = sys.argv[1]
cache_read = cache_write = input_tok = output_tok = 0
compacted = False
with open(transcript_path, encoding="utf-8", errors="ignore") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("isCompactSummary") or "compactMetadata" in entry:
            compacted = True
        if entry.get("type") != "assistant":
            continue
        usage = (entry.get("message") or {}).get("usage")
        if not usage:
            continue
        cache_read  += usage.get("cache_read_input_tokens", 0) or 0
        cache_write += usage.get("cache_creation_input_tokens", 0) or 0
        input_tok   += usage.get("input_tokens", 0) or 0
        output_tok  += usage.get("output_tokens", 0) or 0
total = cache_read + cache_write + input_tok + output_tok
if total == 0 or not compacted:
    sys.exit(0)
ratio = (cache_read + cache_write) / total
if ratio >= 0.70:
    pct = round(ratio * 100)
    print(f"{pct}|{cache_read + cache_write}|{total}")
PYEOF
)
    if [ -n "$CACHE_COST_RESULT" ]; then
      IFS='|' read -r pct cache_tot grand_tot <<< "$CACHE_COST_RESULT"
      echo "- $today [cache-cost]: session cache tokens at ${pct}% of total (${cache_tot}/${grand_tot}) after >=1 compaction. Handoff snapshot written automatically." >> "$OBS_FILE"
      python3 "$HARNESS_DIR/scripts/session/write_handoff.py" \
        --trigger cache-cost --transcript-path "$TRANSCRIPT_PATH" \
        --out ".claude/memory/handoff/latest.md" 2>/dev/null || true
      echo ""
      echo "Cache tokens dominate cost (${pct}% after compaction) — handoff snapshot written automatically."
      echo "Safe to close this session now; next one resumes free via .claude/memory/handoff/latest.md."
      echo ""
    fi
  fi
fi
