#!/bin/bash
# Tool-failure capture hook — PostToolUseFailure (matcher: Bash|mcp__.*)
#
# Records every FAILING Bash or MCP tool call into a per-repo ledger keyed by a
# normalized command *signature*, so recurring failures cluster instead of
# scattering. The ledger feeds two consumers:
#   • tool-failure-recall.sh  — warns before a known-failing shape is re-run
#   • tool-failure-review (routine) — promotes recurring entries into memory
#
# Adapted from ReMe's "tool memory / procedural memory" (success-failure
# experience) concept — made deterministic via a hook so it fires every time.
# Never blocks or errors the tool flow: always exits 0.
set -u

# Only operate inside a harness-bootstrapped repo.
[ -d ".claude/memory" ] || { cat >/dev/null; exit 0; }

# Buffer the event to a temp file — the python program is fed via heredoc on
# stdin, so the event cannot also come from stdin. Passing a file path also
# sidesteps env-var size limits on large tool outputs.
EVENT_FILE="$(mktemp)"
cat > "$EVENT_FILE"
trap 'rm -f "$EVENT_FILE"' EXIT

LEDGER=".claude/memory/tool-failures.jsonl"

EVENT_FILE="$EVENT_FILE" LEDGER="$LEDGER" python3 - <<'PY' 2>/dev/null || true
import json, sys, os, re, tempfile, datetime

LEDGER = os.environ["LEDGER"]
try:
    e = json.load(open(os.environ["EVENT_FILE"]))
except Exception:
    sys.exit(0)

tool = e.get("tool_name", "") or ""
tinp = e.get("tool_input", {}) or {}

is_bash = (tool == "Bash")
is_mcp = tool.startswith("mcp__")
if not (is_bash or is_mcp):
    sys.exit(0)

# --- Extract a short error excerpt from whatever failure field is present ---
resp = e.get("tool_response")
if resp is None:
    resp = e.get("tool_error", e.get("error", ""))
if isinstance(resp, dict):
    err = str(resp.get("stderr") or resp.get("error") or resp.get("content") or resp)
else:
    err = str(resp)
err = re.sub(r"\s+", " ", err).strip()[:300]

def norm_bash(cmd):
    """Collapse a command to a stable shape: strip volatile paths/strings/numbers."""
    s = cmd.strip()
    s = re.sub(r'"[^"]*"', '<str>', s)
    s = re.sub(r"'[^']*'", '<str>', s)
    s = re.sub(r'\S*/\S*', '<path>', s)        # any token containing a slash
    s = re.sub(r'\b[0-9a-f]{7,}\b', '<hash>', s)
    s = re.sub(r'\b\d+\b', '<n>', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:120]

if is_bash:
    cmd = str(tinp.get("command", "")).strip()
    if not cmd:
        sys.exit(0)
    sig = norm_bash(cmd)
    sample = cmd[:200]
else:
    keys = sorted(tinp.keys()) if isinstance(tinp, dict) else []
    sig = "%s(%s)" % (tool, ",".join(keys))
    sample = json.dumps(tinp)[:200]

now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

# --- Load existing ledger ---
entries = {}
if os.path.exists(LEDGER):
    for line in open(LEDGER):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
            entries[o["sig"]] = o
        except Exception:
            pass

o = entries.get(sig)
if o is None:
    o = {"sig": sig, "tool": tool, "count": 0, "first_seen": now, "last_seen": now,
         "last_error": err, "samples": [], "status": "open", "remedy": None, "promoted": False}
o["count"] = o.get("count", 0) + 1
o["last_seen"] = now
o["last_error"] = err or o.get("last_error", "")
o["tool"] = tool
# A failure recurring after it was marked resolved means the fix didn't hold —
# re-open it so recall warns again and the next review re-diagnoses.
if o.get("status") == "resolved":
    o["status"] = "open"
    o["promoted"] = False
samples = o.get("samples", [])
if sample not in samples:
    samples.append(sample)
    samples = samples[-5:]
o["samples"] = samples
entries[sig] = o

# --- Atomic rewrite ---
d = os.path.dirname(LEDGER) or "."
fd, tmp = tempfile.mkstemp(dir=d)
with os.fdopen(fd, "w") as f:
    for v in entries.values():
        f.write(json.dumps(v) + "\n")
os.replace(tmp, LEDGER)
PY

exit 0

# REGISTRATION (settings.json) — wired in templates/settings.json.template:
# "PostToolUseFailure": [
#   { "matcher": "Bash|mcp__.*",
#     "hooks": [ { "type": "command", "command": "bash .claude/hooks/tool-failure-capture.sh" } ] }
# ]
