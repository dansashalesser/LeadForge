#!/bin/bash
# Tool-failure recall hook — PreToolUse (matcher: Bash|mcp__.*), SOFT gate.
#
# Before a Bash/MCP call runs, computes the same signature as the capture hook
# and looks it up in the per-repo ledger. If that command shape has failed
# repeatedly and is still open, it prints a short advisory so Claude reconsiders
# before repeating a known-failing call. It NEVER blocks (always exits 0) — it
# only surfaces memory. Closes the loop: capture remembers, recall reminds.
#
# Noise control: warns only when count >= MIN_FAILS, last_seen is recent, and
# at most once per session per signature.
set -u

LEDGER=".claude/memory/tool-failures.jsonl"
SEEN=".claude/memory/.tool-failure-recall-seen"

[ -f "$LEDGER" ] || { cat >/dev/null; exit 0; }

# Buffer the event to a temp file (the python program owns stdin via heredoc).
EVENT_FILE="$(mktemp)"
cat > "$EVENT_FILE"
trap 'rm -f "$EVENT_FILE"' EXIT

EVENT_FILE="$EVENT_FILE" LEDGER="$LEDGER" SEEN="$SEEN" python3 - <<'PY' 2>/dev/null || true
import json, sys, os, re, datetime

LEDGER = os.environ["LEDGER"]
SEEN = os.environ["SEEN"]
MIN_FAILS = int(os.environ.get("TOOL_FAILURE_MIN_FAILS", "2"))
MAX_AGE_DAYS = int(os.environ.get("TOOL_FAILURE_MAX_AGE_DAYS", "45"))

try:
    e = json.load(open(os.environ["EVENT_FILE"]))
except Exception:
    sys.exit(0)

tool = e.get("tool_name", "") or ""
tinp = e.get("tool_input", {}) or {}
session = e.get("session_id", "") or ""

is_bash = (tool == "Bash")
is_mcp = tool.startswith("mcp__")
if not (is_bash or is_mcp):
    sys.exit(0)

def norm_bash(cmd):
    s = cmd.strip()
    s = re.sub(r'"[^"]*"', '<str>', s)
    s = re.sub(r"'[^']*'", '<str>', s)
    s = re.sub(r'\S*/\S*', '<path>', s)
    s = re.sub(r'\b[0-9a-f]{7,}\b', '<hash>', s)
    s = re.sub(r'\b\d+\b', '<n>', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:120]

if is_bash:
    cmd = str(tinp.get("command", "")).strip()
    if not cmd:
        sys.exit(0)
    sig = norm_bash(cmd)
else:
    keys = sorted(tinp.keys()) if isinstance(tinp, dict) else []
    sig = "%s(%s)" % (tool, ",".join(keys))

# --- Find the ledger entry ---
o = None
for line in open(LEDGER):
    line = line.strip()
    if not line:
        continue
    try:
        cur = json.loads(line)
    except Exception:
        continue
    if cur.get("sig") == sig:
        o = cur
        break

if o is None or o.get("status") != "open":
    sys.exit(0)
if o.get("count", 0) < MIN_FAILS:
    sys.exit(0)

# --- Recency gate ---
try:
    last = datetime.datetime.strptime(o.get("last_seen", "")[:19], "%Y-%m-%dT%H:%M:%S")
    if (datetime.datetime.now() - last).days > MAX_AGE_DAYS:
        sys.exit(0)
except Exception:
    pass

# --- Once-per-session-per-signature dedupe ---
key = "%s|%s" % (session, sig)
seen = set()
if os.path.exists(SEEN):
    seen = set(x.strip() for x in open(SEEN) if x.strip())
if key in seen:
    sys.exit(0)
seen.add(key)
lines = list(seen)[-500:]
with open(SEEN, "w") as f:
    f.write("\n".join(lines) + "\n")

# --- Emit advisory (stdout → shown to Claude; non-blocking) ---
count = o.get("count", 0)
last_err = (o.get("last_error") or "").strip()
remedy = (o.get("remedy") or "").strip()
date = (o.get("last_seen") or "")[:10]

print("⚠️  Tool-failure memory — this command shape has failed %d× in this repo (last: %s)." % (count, date))
print("    Signature: %s" % sig)
if last_err:
    print("    Last error: %s" % last_err[:200])
if remedy:
    print("    Recorded remedy: %s" % remedy)
print("    Reconsider before running: change the approach, or confirm what is now different.")
print("    If it succeeds this time, note the fix — the tool-failure-review routine will promote it to memory.")
PY

exit 0

# REGISTRATION (settings.json) — wired in templates/settings.json.template:
# "PreToolUse": [
#   { "matcher": "Bash|mcp__.*",
#     "hooks": [ { "type": "command", "command": "bash .claude/hooks/tool-failure-recall.sh" } ] }
# ]
