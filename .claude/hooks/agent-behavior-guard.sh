#!/bin/bash
# Agent-behavior guard — PreToolUse (matcher: Read|Bash|WebFetch|WebSearch|mcp__.*).
#
# Ported from perplexityai/numbat's rule-engine design (network indicators,
# persistence, chained/sequence findings) — scoped down for a single local
# harness: no rule files, no versioning, no signed bundles. Just the three
# detections none of the existing per-event hooks cover:
#
#   1. network_indicator  — Bash/WebFetch target hits a cloud-metadata SSRF
#                            endpoint (169.254.169.254 etc.)
#   2. persistence         — Bash writes to crontab, shell rc files,
#                            authorized_keys, or a systemd/launchd unit
#   3. chained_secret_egress — a secret-file access (Read, or Bash cat/grep of
#                            one) followed *later in the same session* by an
#                            outbound network call (Bash curl/wget/ssh/etc,
#                            WebFetch, WebSearch)
#
# protected-path-hook.sh only fires on Write|Edit and is stateless per-call;
# none of it sees Read, Bash egress, or correlates across calls. This hook
# fills that gap.
#
# Default mode: MONITOR ONLY — warns to stderr and logs the finding, always
# exits 0. Set SDD_AGENT_GUARD_ENFORCE to a comma-separated list of rule names
# (network_indicator,persistence,chained_secret_egress) or "all" to make
# matching rules hard-block (exit 2), mirroring numbat's monitor->enforce
# promotion without its rule-file machinery.
set -u

STATE_DIR=".claude/memory"
SECRET_LEDGER="$STATE_DIR/.agent-behavior-guard-secret-access.jsonl"
FINDINGS_LOG="$STATE_DIR/agent-security-findings.jsonl"
ENFORCE="${SDD_AGENT_GUARD_ENFORCE:-}"

mkdir -p "$STATE_DIR" 2>/dev/null || true

EVENT_FILE="$(mktemp)"
cat > "$EVENT_FILE"
trap 'rm -f "$EVENT_FILE"' EXIT

EVENT_FILE="$EVENT_FILE" SECRET_LEDGER="$SECRET_LEDGER" FINDINGS_LOG="$FINDINGS_LOG" ENFORCE="$ENFORCE" \
python3 - <<'PY'
import json, os, re, sys, datetime

EVENT_FILE = os.environ["EVENT_FILE"]
SECRET_LEDGER = os.environ["SECRET_LEDGER"]
FINDINGS_LOG = os.environ["FINDINGS_LOG"]
ENFORCE_RAW = os.environ.get("ENFORCE", "")
ENFORCE = set(x.strip() for x in ENFORCE_RAW.split(",") if x.strip())

try:
    e = json.load(open(EVENT_FILE))
except Exception:
    # Fail closed only when a rule is enforced; monitor mode never blocks.
    if ENFORCE:
        print("BLOCKED: agent-behavior-guard could not parse the hook event and enforce mode is on", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)

tool = e.get("tool_name", "") or ""
tinp = e.get("tool_input", {}) or {}
session = e.get("session_id", "") or "unknown"

SECRET_PATH_RE = re.compile(
    r'(^|/)\.env($|\.[^/]*)'
    r'|\.(pem|key|p12|pfx|cert|crt|jks|keystore)$'
    r'|(^|/)(credentials?|\.secrets?|secrets?)([^/]*)?$'
    r'|(^|/)\.aws/(credentials|config)$'
    r'|(^|/)\.ssh/'
)
NETWORK_INDICATOR_RE = re.compile(
    r'169\.254\.169\.254'          # AWS/OpenStack/GCP metadata IP
    r'|169\.254\.170\.2'           # AWS ECS task metadata
    r'|metadata\.google\.internal' # GCP metadata hostname
    r'|metadata\.azure\.com'       # Azure IMDS hostname
    r'|100\.100\.100\.200'         # Alibaba Cloud metadata
)
EGRESS_RE = re.compile(
    r'\bcurl\b|\bwget\b|\bnc\b|\bncat\b|\bscp\b|\brsync\b.*::|\bssh\b[^|;]*@'
    r'|/dev/tcp/|requests\.(get|post)|urllib\.request'
)
PERSISTENCE_RE = re.compile(
    r'crontab\s+-[el]'
    r'|>>\s*~?/?(\.bashrc|\.zshrc|\.bash_profile|\.profile|\.zprofile)\b'
    r'|authorized_keys'
    r'|launchctl\s+(load|bootstrap)'
    r'|systemctl\s+enable'
)

def log_finding(kind, detail, target, enforced):
    rec = {
        "ts": None,
        "session": session,
        "type": kind,
        "detail": detail,
        "target": target[:300],
        "mode": "enforce" if enforced else "monitor",
    }
    try:
        with open(FINDINGS_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass

def warn(kind, detail, target):
    enforced = kind in ENFORCE or "all" in ENFORCE
    log_finding(kind, detail, target, enforced)
    prefix = "BLOCKED" if enforced else "⚠️  agent-behavior-guard"
    stream = sys.stderr
    print("%s: %s" % (prefix, detail), file=stream)
    print("    Target/command: %s" % target[:200], file=stream)
    if enforced:
        print("    Rule '%s' is in enforce mode (SDD_AGENT_GUARD_ENFORCE)." % kind, file=stream)
        sys.exit(2)

def record_secret_access(path):
    try:
        with open(SECRET_LEDGER, "a") as f:
            f.write(json.dumps({"session": session, "path": path}) + "\n")
    except Exception:
        pass

def had_prior_secret_access():
    if not os.path.exists(SECRET_LEDGER):
        return None
    for line in open(SECRET_LEDGER):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("session") == session:
            return rec.get("path")
    return None

# --- Gather the text to scan for this event, per tool ---
command = ""
file_path = ""
if tool == "Bash":
    command = str(tinp.get("command", "") or "")
elif tool == "Read":
    file_path = str(tinp.get("file_path", tinp.get("path", "")) or "")
elif tool in ("WebFetch", "WebSearch"):
    file_path = str(tinp.get("url", tinp.get("query", "")) or "")

target_text = command or file_path
if not target_text:
    sys.exit(0)

# --- Rule 1: network indicator (cloud metadata SSRF) ---
if tool in ("Bash", "WebFetch") and NETWORK_INDICATOR_RE.search(target_text):
    warn("network_indicator", "cloud-metadata SSRF endpoint referenced", target_text)

# --- Rule 2: persistence write ---
if tool == "Bash" and PERSISTENCE_RE.search(command):
    warn("persistence", "command writes to a persistence mechanism (cron/rc-file/authorized_keys/service unit)", command)

# --- Rule 3: chained secret-access -> egress ---
is_secret_access = (
    (tool == "Read" and SECRET_PATH_RE.search(file_path))
    or (tool == "Bash" and SECRET_PATH_RE.search(command))
)
if is_secret_access:
    record_secret_access(file_path or command)

is_egress = (
    tool in ("WebFetch", "WebSearch")
    or (tool == "Bash" and EGRESS_RE.search(command))
)
if is_egress:
    prior = had_prior_secret_access()
    if prior:
        warn(
            "chained_secret_egress",
            "secret-bearing path was accessed earlier this session (%s), now an egress call is being made" % prior,
            target_text,
        )

sys.exit(0)
PY
RC=$?

# The Python's exit code IS the verdict. Before 2026-10-01 this line was a bare
# `exit 0`, so warn()'s sys.exit(2) was discarded and enforce mode never blocked
# anything. A non-zero exit other than 2 means the guard itself failed (python3
# missing, crash) — fail closed if a rule is enforced, stay silent if not.
[ "$RC" -eq 2 ] && exit 2
if [ "$RC" -ne 0 ] && [ -n "$ENFORCE" ]; then
  echo "BLOCKED: agent-behavior-guard failed (exit $RC) with enforce mode on — cannot verify this call" >&2
  exit 2
fi
exit 0

# REGISTRATION (settings.json) — wired in templates/settings.json.template:
# "PreToolUse": [
#   { "matcher": "Read|Bash|WebFetch|WebSearch",
#     "hooks": [ { "type": "command", "command": "bash .claude/hooks/agent-behavior-guard.sh" } ] }
# ]
#
# Findings: .claude/memory/agent-security-findings.jsonl (append-only, monitor+enforce both logged)
# Enforce a rule: SDD_AGENT_GUARD_ENFORCE=network_indicator,persistence,chained_secret_egress (or "all")
