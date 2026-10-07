#!/usr/bin/env bash
# Context Priming Hook — injects hot-memory on UserPromptSubmit
#
# Not every prompt: hot-memory is ~2k tokens and each injection stays in the
# transcript, so injecting on every prompt re-sends one more copy on every
# later API call. It is injected on the 1st prompt of a session, then every
# SDD_HOT_MEMORY_EVERY prompts (default 10), and on the first prompt after a
# compaction (the summary may have dropped it).
#
# Counter lives in .claude/memory/.prompt-hook/<session_id>.json. Any failure to
# read the event or the counter injects — a missed injection is invisible, an
# extra one costs 2k tokens.
# Must be fast (<1s) since it fires on every prompt

HOT_MEMORY=".claude/memory/hot-memory.md"
STATE_DIR=".claude/memory/.prompt-hook"

EVENT_FILE="$(mktemp)"
cat > "$EVENT_FILE"
trap 'rm -f "$EVENT_FILE"' EXIT

[ -s "$HOT_MEMORY" ] || exit 0

DECISION="$(EVENT_FILE="$EVENT_FILE" STATE_DIR="$STATE_DIR" python3 - <<'PY'
import json, os, sys, time
from pathlib import Path

def inject(why):
    if why:
        print(f"[prompt-hook] {why} — injecting hot-memory", file=sys.stderr)
    print("inject")
    sys.exit(0)

try:
    every = int(os.environ.get("SDD_HOT_MEMORY_EVERY", "10"))
except ValueError:
    inject("SDD_HOT_MEMORY_EVERY is not an integer")
if every < 1:
    inject("SDD_HOT_MEMORY_EVERY < 1")

try:
    event = json.loads(Path(os.environ["EVENT_FILE"]).read_text())
except (OSError, ValueError) as exc:
    inject(f"unreadable hook event ({exc})")
sid = event.get("session_id") or ""
if not sid or "/" in sid:
    inject("no usable session_id")

# Compaction count from the transcript; a change resets the cadence.
compacts = 0
transcript = event.get("transcript_path") or ""
if transcript:
    try:
        compacts = Path(transcript).read_bytes().count(b'"compact_boundary"')
    except OSError:
        pass

state_dir = Path(os.environ["STATE_DIR"])
state_file = state_dir / f"{sid}.json"
fresh = {"n": 0, "compacts": compacts}
state = fresh
if state_file.is_file():
    try:
        state = json.loads(state_file.read_text())
    except ValueError:
        state = None
    if not isinstance(state, dict):
        # Restart the cadence (n=1 injects) rather than inject forever.
        print(f"[prompt-hook] corrupt counter {state_file} — reset", file=sys.stderr)
        state = fresh
if state.get("compacts") != compacts:
    state = {"n": 0, "compacts": compacts}

n = int(state.get("n", 0)) + 1
try:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"n": n, "compacts": compacts}))
    if n == 1:
        # New session: drop counters untouched for a week.
        cutoff = time.time() - 7 * 86400
        for old in state_dir.glob("*.json"):
            if old.stat().st_mtime < cutoff:
                old.unlink()
except OSError as exc:
    inject(f"cannot write counter ({exc})")

print("inject" if (n - 1) % every == 0 else "skip")
PY
)"

[ "$DECISION" = "skip" ] && exit 0

echo "--- Active Context (hot-memory) ---"
cat "$HOT_MEMORY"
echo "--- End Active Context ---"
