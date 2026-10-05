#!/bin/bash
# Agent-trace hook — PostToolUse (matcher: Agent)
#
# trace.log had zero reliable producers: population was a manual prose
# instruction buried in two rarely-run commands (harness-validate.md,
# validate-adversarial.md), so the file never actually got created despite
# session-judge, stop-hook.sh, and evolve.md all reading it. This hook makes
# every subagent spawn write its own trace entry — deterministic, not
# dependent on a command author remembering to add the instruction.
#
# Format (kiro/settings/rules/agent-tracing.md), trailing two fields omitted
# (spec marks them optional): YYYY-MM-DD HH:MM | agent | tier | outcome | duration-hint
# Never blocks or errors the tool flow: always exits 0.
set -u

[ -d ".claude/memory" ] || { cat >/dev/null; exit 0; }

EVENT_FILE="$(mktemp)"
cat > "$EVENT_FILE"
trap 'rm -f "$EVENT_FILE"' EXIT

TRACE_LOG=".claude/memory/trace.log"

EVENT_FILE="$EVENT_FILE" TRACE_LOG="$TRACE_LOG" python3 - <<'PY' 2>/dev/null || true
import json, os, sys, datetime

try:
    e = json.load(open(os.environ["EVENT_FILE"]))
except Exception:
    sys.exit(0)

if e.get("tool_name") != "Agent":
    sys.exit(0)

tinp = e.get("tool_input", {}) or {}
agent = tinp.get("subagent_type") or "general-purpose"

tier = tinp.get("model") or "inherit"
duration_hint = {"haiku": "fast", "sonnet": "medium", "opus": "slow"}.get(tier, "medium")

resp = e.get("tool_response")
if resp is None:
    outcome = "dispatched"  # background spawn ack — real result arrives later, out of band
else:
    text = json.dumps(resp) if isinstance(resp, (dict, list)) else str(resp)
    low = text.lower()
    if any(k in low for k in ("error", "denied", "failed", "traceback")):
        outcome = "error"
    else:
        outcome = "pass"

now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
line = f"{now} | {agent} | {tier} | {outcome} | {duration_hint}\n"

TRACE_LOG = os.environ["TRACE_LOG"]
with open(TRACE_LOG, "a") as f:
    f.write(line)

# Housekeeping: archive past 200 lines per agent-tracing.md's own rule.
lines = open(TRACE_LOG).readlines()
if len(lines) > 200:
    glacier_dir = os.path.join(os.path.dirname(TRACE_LOG), "glacier")
    os.makedirs(glacier_dir, exist_ok=True)
    cutoff = len(lines) - 200
    stamp = datetime.date.today().isoformat()
    with open(os.path.join(glacier_dir, f"trace-{stamp}.log"), "a") as f:
        f.writelines(lines[:cutoff])
    with open(TRACE_LOG, "w") as f:
        f.writelines(lines[cutoff:])
PY

exit 0

# REGISTRATION (settings.json) — wired in templates/settings.json.template:
# "PostToolUse": [
#   { "matcher": "Agent",
#     "hooks": [ { "type": "command", "command": "bash .claude/hooks/agent-trace-hook.sh" } ] }
# ]
#
# Known limitation: for background spawns (run_in_background default), the
# PostToolUse event fires at dispatch, not completion — tool_response is the
# spawn ack, not the agent's real result, so outcome is recorded as
# "dispatched" rather than pass/fail. Real per-agent success/failure tracking
# for background agents would need a hook on the completion notification,
# which Claude Code does not currently expose. Foreground agents
# (run_in_background: false) get a real pass/error outcome since
# tool_response holds the finished result.
