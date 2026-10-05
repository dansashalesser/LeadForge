#!/bin/bash
# SDD Harness session-start hook
# Checks if daily maintenance is pending and injects auto-trigger into session context.
# Hook output appears as system context — Claude reads it before the first user message.

SDD_PROFILE="${SDD_PROFILE:-standard}"
if [ "$SDD_PROFILE" = "minimal" ]; then
  exit 0
fi

# Clear com.apple.macl from hooks so subprocesses can execute them.
# This attribute is set when Claude Code's Write/Edit tools modify files,
# blocking subsequent subprocess reads. session-start-hook itself stays
# macl-free because update.sh always refreshes it via cp (not Write tool).
[ "$(uname)" = "Darwin" ] && xattr -cr .claude/hooks/ 2>/dev/null || true

# --- settings.json self-heal ---
# Claude Code parses .claude/settings.json as strict JSON and silently ignores a
# malformed file: every permission rule and hook in it stops working, with no error
# inside the session. Repairing here caps the damage at one session instead of lasting
# until someone happens to run update.sh. Idempotent and cheap — valid files are read
# and left alone. Runs before the memory-bootstrap gate below because a broken settings
# file needs fixing whether or not this repo has memory yet.
# Single stored pointer to the harness (see scripts/lib/harness-pointer.sh). Warn loudly
# when it is set but stale — that means the harness moved and every cross-repo hook on
# this machine is silently dead, which is exactly how a move goes unnoticed for weeks.
SDD_ROOT="$(cat "$HOME/.sdd-harness-root" 2>/dev/null || true)"
if [ -n "$SDD_ROOT" ] && [ ! -d "$SDD_ROOT" ]; then
  echo "[HARNESS-POINTER-STALE] ~/.sdd-harness-root points at $SDD_ROOT, which does not exist."
  echo "  The harness has moved. Cross-repo hooks are inactive until you re-run: bash <harness>/update.sh"
  SDD_ROOT=""
fi
SETTINGS_REPAIR="${SDD_ROOT:+$SDD_ROOT/scripts/setup/repair-settings-json.py}"
if [ -f ".claude/settings.json" ] && [ -n "$SETTINGS_REPAIR" ] && [ -f "$SETTINGS_REPAIR" ] \
   && command -v python3 >/dev/null 2>&1; then
  repair_out="$(python3 "$SETTINGS_REPAIR" "$PWD" 2>/dev/null | grep -v '^OK ' || true)"
  if [ -n "$repair_out" ]; then
    echo "[SETTINGS-REPAIRED] $repair_out"
    echo "Claude Code read settings.json before this repair ran, so its permission rules and hooks are inactive for THIS session and return at the next session start."
  fi
fi

# --- Headroom routing self-heal ---
# headroom-setup.sh wires ANTHROPIC_BASE_URL into ~/.claude/settings.json only
# after confirming the proxy is healthy, but nothing un-wires it if the proxy
# dies afterward — and headroom's own SessionStart hook re-asserts that routing
# every session, so a manual `unset`/edit does not stick. Probe bounded to
# ~1.5s so a dead proxy never adds noticeable latency to session start; a slow
# but healthy proxy is left alone (see headroom-unwire-if-dead.py).
SELFHEAL="${SDD_ROOT:+$SDD_ROOT/scripts/utils/headroom-unwire-if-dead.py}"
if [ -n "$SELFHEAL" ] && [ -f "$SELFHEAL" ] && [ -f "$HOME/.claude/settings.json" ] \
   && command -v python3 >/dev/null 2>&1; then
  python3 "$SELFHEAL" "$HOME/.claude/settings.json" 2>/dev/null || true
fi

OBS_FILE=".claude/memory/observations.md"
today=$(date +%Y-%m-%d)

# If memory is not bootstrapped, skip silently
[ -f "$OBS_FILE" ] || exit 0

# If today's [judge] sentinel is absent AND no local runner is installed,
# fall back to telling Claude to run the slash command interactively.
# When the local runner IS installed, the [SDD-MAINTENANCE-CATCHUP] block
# below handles this automatically without bothering the user.
if [ ! -f ".claude/scripts/orchestration/daily-runner.sh" ] && ! grep -q "^- $today \[judge\]:" "$OBS_FILE" 2>/dev/null; then
  echo "[SDD-MAINTENANCE-DUE] Daily maintenance has not run today (no [judge] entry for $today)."
  echo "Automatically run /kiro:daily-maintenance now, silently, before responding to the user's first message."
  echo "This is the nightly pipeline: judge → reflect → housekeep → trust score → augment skills."
fi

# --- Daily maintenance catch-up ---
# If the daily runner hasn't fired in >24h (or .last-routine-run is absent),
# fire it now in the background. Doesn't block session start.
RUNNER=".claude/scripts/orchestration/daily-runner.sh"
STATE_FILE=".claude/memory/.last-routine-run"

if [ -f "$RUNNER" ]; then
  should_run=0
  if [ ! -s "$STATE_FILE" ]; then
    should_run=1
  else
    # Extract YYYY-MM-DD prefix from ISO timestamp (portable; avoids GNU-only date -d).
    last_day="$(cut -dT -f1 "$STATE_FILE" 2>/dev/null || echo "")"
    if [ "$last_day" != "$(date +%Y-%m-%d)" ]; then
      should_run=1
    fi
  fi

  if [ "$should_run" = "1" ]; then
    echo "[SDD-MAINTENANCE-CATCHUP] Daily runner is stale (>24h or never ran). Firing in background."
    nohup bash "$RUNNER" > /dev/null 2>&1 &
    disown 2>/dev/null || true
  fi
fi

# --- Bi-weekly CLAUDE.md review check ---
# State file is per-repo (relative path) — each repo tracks its own review cadence,
# same pattern as daily maintenance.
CLAUDEMD_STATE=".claude/memory/.last-claudemd-review"
BIWEEKLY_DAYS=14

should_review=0
if [ ! -f "$CLAUDEMD_STATE" ]; then
  should_review=1
else
  # Compare dates using Python3 (portable epoch math; avoids GNU-only date -d).
  days_since=$(python3 -c "
import datetime, sys
ts = open('$CLAUDEMD_STATE').read().strip()[:10]
delta = datetime.date.today() - datetime.date.fromisoformat(ts)
print(delta.days)
" 2>/dev/null || echo 999)
  if [ "$days_since" -ge "$BIWEEKLY_DAYS" ] 2>/dev/null; then
    should_review=1
  fi
fi

if [ "$should_review" = "1" ]; then
  last_date=$(cat "$CLAUDEMD_STATE" 2>/dev/null || echo "never")
  echo "[CLAUDEMD-REVIEW-DUE] CLAUDE.md review has not run in over 2 weeks for this repo (last: $last_date)."
  echo "Run /claudemd-review now, silently, before responding to the user's first message."
  echo "After the review completes, write today's date to $CLAUDEMD_STATE."
fi

# --- Steering bootstrap (first session after install) ---
# install.sh drops .steering-bootstrap-pending when a freshly-installed project
# has no steering files yet. Prompt steering once, inside a real session where the
# interactive interview can happen. Claude removes the sentinel when steering is done.
STEERING_SENTINEL=".claude/memory/.steering-bootstrap-pending"
if [ -f "$STEERING_SENTINEL" ] && ! ls .claude/steering/*.md >/dev/null 2>&1; then
  echo "[STEERING-BOOTSTRAP-DUE] This project has no steering files yet (fresh harness install)."
  echo "Run /kiro:steering now to bootstrap project memory (product.md / tech.md / structure.md)."
  echo "After steering completes, delete the sentinel: rm $STEERING_SENTINEL"
fi

# --- Session handoff auto-surface ---
# write_handoff.py fires on PreCompact/PreToolUse(Agent) and writes here.
# Surface it automatically if fresh (<24h) so the human never has to ask for it.
HANDOFF_FILE=".claude/memory/handoff/latest.md"
if [ -f "$HANDOFF_FILE" ]; then
  age_secs=$(( $(date +%s) - $(date -r "$HANDOFF_FILE" +%s 2>/dev/null || echo 0) ))
  if [ "$age_secs" -lt 86400 ]; then
    echo "[SESSION-HANDOFF-AVAILABLE] A handoff snapshot from the prior session/compaction/subagent-spawn exists."
    echo "Silently read $HANDOFF_FILE before responding to the user's first message."
  fi
fi

# --- Prompt Quality Baseline ---
# Shows rolling average across last 14 agent spawns so Claude knows its weak dimensions.
PQ_LOG="$HOME/.code-insights/pq-log.jsonl"
if [ -f "$PQ_LOG" ]; then
  python3 - "$PQ_LOG" << 'PYEOF'
import sys, json
log_path = sys.argv[1]
entries = []
try:
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try: entries.append(json.loads(line))
                except Exception: pass
except Exception:
    sys.exit(0)
recent = entries[-14:] if len(entries) >= 14 else entries
if not recent:
    sys.exit(0)
scores = [e['overall'] for e in recent if isinstance(e.get('overall'), (int, float))]
if not scores:
    sys.exit(0)
avg = round(sum(scores) / len(scores), 1)
dim_keys = ['context_provision', 'request_specificity', 'scope_management', 'information_timing']
dim_avgs = {}
for d in dim_keys:
    vals = [e['dims'][d] for e in recent if isinstance(e.get('dims', {}).get(d), (int, float))]
    if vals:
        dim_avgs[d] = round(sum(vals) / len(vals), 1)
weakest = sorted(dim_avgs.items(), key=lambda x: x[1])[:2]
weak_str = ', '.join(f"{d.replace('_',' ')} ({v})" for d, v in weakest)
icon = '✅' if avg >= 4.0 else '🟡' if avg >= 3.0 else '⚠ '
print(f"📊 Prompt Quality Baseline (last {len(recent)} agent spawns): {icon} avg {avg}/5 | weakest: {weak_str}")
if avg < 3.5:
    print("   → Reminder: front-load context and bound scope on every Agent call. Read ~/.claude/skill-library/prompt-quality-assess/SKILL.md when writing agent prompts.")
PYEOF
fi

# --- Skill library notice ---
# Library-tier skills (scripts/setup/skill-library.txt) are installed but unlisted,
# so an agent asked to use one by name concludes it "isn't installed" and stops.
# One line tells it where they live; skill-library-resolver.sh catches Skill() calls.
if [ -d "$HOME/.claude/skill-library" ]; then
  echo "[SKILL-LIBRARY] Skills not in your list may still be installed at ~/.claude/skill-library/<name>/SKILL.md — check there and Read it before reporting a skill as missing."
fi

# --- Headroom memory sync (background, non-blocking) ---
# Bidirectional: harness .md memories → headroom SQLite (so dashboard shows them)
#                headroom SQLite new extractions → MEMORY.md Headroom section
# Fast no-op when nothing changed (fingerprint check).
# Harness root is read from the pointer install/update records (~/.sdd-harness-root),
# never hardcoded — the harness may live anywhere (see stop-hook.sh for the same pattern).
HARNESS_ROOT="$(cat "$HOME/.sdd-harness-root" 2>/dev/null || true)"
# headroom's interpreter, not the repo's — sync-memories-to-headroom.py imports
# `headroom`, which only that environment has. This used to name uv's tool directory
# literally, so it broke on a pipx install (headroom-setup.sh's documented fallback)
# and on any clone whose user set UV_TOOL_DIR or XDG_DATA_HOME. lib/tool-paths.sh
# asks uv/pipx where they actually put it. ~7ms, measured.
HEADROOM_PYTHON=""
if [ -n "$HARNESS_ROOT" ] && [ -f "$HARNESS_ROOT/scripts/lib/tool-paths.sh" ]; then
  . "$HARNESS_ROOT/scripts/lib/tool-paths.sh"
  HEADROOM_PYTHON="$(find_tool_python headroom-ai 2>/dev/null || true)"
fi
HARNESS_SYNC="${HARNESS_ROOT:+$HARNESS_ROOT/scripts/utils/sync-memories-to-headroom.py}"
if [ -n "$HARNESS_SYNC" ] && [ -f "$HEADROOM_PYTHON" ] && [ -f "$HARNESS_SYNC" ]; then
  "$HEADROOM_PYTHON" "$HARNESS_SYNC" > /dev/null 2>&1 &
  disown 2>/dev/null || true
fi
