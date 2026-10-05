#!/usr/bin/env bash
# verification-retry-hook.sh — Stop hook: catches claimed-but-unverified success.
#
# Spotify Backstage AiKA decomposes "agent mode" into composable pre/post
# processors — a Verification processor (pass/fail check against stated
# criteria, auto-retry up to N rounds) being the one this harness had no
# equivalent of. Existing quality-gate hooks check code as it's written; this
# checks the *claim* made about it at the end of the turn.
#
# Narrow trigger by design: only fires when the assistant's final turn uses
# explicit success language ("tests pass", "all passing", "verified working", ...)
# AND either (a) no test-runner command ran this turn, or (b) the last one that
# did run exited non-zero. A false positive here would block a normal Stop, so
# the pattern list stays deliberately conservative rather than broad.
#
# Blocking uses the Stop-hook JSON contract: {"decision": "block", "reason": "..."}
# forces Claude to continue instead of ending the turn. Capped at
# VERIFICATION_RETRY_MAX (default 2) rounds *per user request* — the round
# counter resets whenever a new real user message appears, and gives up
# (exits 0, logs instead of blocking) once the cap is hit so a genuinely
# unfixable claim cannot loop forever.
#
# Defaults to advisory (log, never blocks): this repo already tried an
# always-blocking Stop hook once (address-check-hook.sh used to exit 2 on
# every turn missing "Husband") and demoted it to a passive log because
# blocking cost a full extra turn every time it fired. Set
# VERIFICATION_RETRY_WARN_ONLY=0 to opt into the actual blocking retry loop
# once a project has confirmed the false-positive rate is low enough to be
# worth the turn cost.
#
# REGISTRATION (Stop, alongside stop-hook.sh):
# {
#   "matcher": "",
#   "hooks": [{"type": "command", "command": "bash \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/verification-retry-hook.sh\""}]
# }

set -u

MAX_ROUNDS="${VERIFICATION_RETRY_MAX:-2}"
WARN_ONLY="${VERIFICATION_RETRY_WARN_ONLY:-1}"
STATE_FILE=".claude/memory/.verification-retry-state"

HOOK_INPUT=$(cat)

RESULT=$(python3 - "$MAX_ROUNDS" 2>/dev/null <<'PYEOF'
import json, sys
from pathlib import Path

max_rounds = int(sys.argv[1])


def tokenize_words(text):
    """Split into runs of alnum chars — a no-regex stand-in for \\w+ matching."""
    words = []
    current = []
    for ch in text:
        if ch.isalnum():
            current.append(ch)
        else:
            if current:
                words.append(''.join(current))
                current = []
    if current:
        words.append(''.join(current))
    return words


def find_latest_transcript():
    base = Path.home() / ".claude" / "projects"
    if not base.is_dir():
        return None
    cwd = Path.cwd().resolve()
    encoded = str(cwd).replace('/.', '/-').replace('/', '-')
    project_dir = base / encoded
    if not project_dir.is_dir():
        candidates = [p for p in base.iterdir() if p.is_dir()]
        if not candidates:
            return None
        project_dir = max(candidates, key=lambda p: p.stat().st_mtime)
    transcripts = list(project_dir.glob("*.jsonl"))
    if not transcripts:
        return None
    return max(transcripts, key=lambda p: p.stat().st_mtime)

path = find_latest_transcript()
if path is None:
    print(json.dumps({"verdict": "skip"}))
    sys.exit(0)

records = []
for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        records.append(json.loads(line))
    except json.JSONDecodeError:
        continue

def is_real_user_turn(rec):
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else rec
    if msg.get("role") != "user":
        return False
    content = msg.get("content", "")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        # A tool_result wrapper is not a real user message.
        return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    return False

TEST_CMD_SINGLE_TOKENS = {"pytest", "py.test", "jest", "vitest", "rspec"}
TEST_CMD_SEQUENCES = [
    ("npm", "run", "test"), ("npm", "test"), ("yarn", "test"),
    ("go", "test"), ("cargo", "test"), ("mvn", "test"), ("dotnet", "test"),
    ("bundle", "exec", "rspec"),
]


def is_test_command(cmd):
    """Case-sensitive, matching the original regex's lack of re.IGNORECASE."""
    tokens = cmd.replace('&&', ' ').replace(';', ' ').replace('|', ' ').split()
    for t in tokens:
        if t in TEST_CMD_SINGLE_TOKENS:
            return True
    for seq in TEST_CMD_SEQUENCES:
        n = len(seq)
        for i in range(len(tokens) - n + 1):
            if tuple(tokens[i:i + n]) == seq:
                return True
    return False


PASS_WORDS = {"pass", "passing", "passed"}
TESTCHECK_WORDS = {"test", "tests", "check", "checks"}


def claims_success(text):
    words = tokenize_words(text.lower())
    n = len(words)
    for i in range(n):
        w = words[i]
        if w == "all" and i + 2 < n and words[i + 1] in TESTCHECK_WORDS and words[i + 2] in PASS_WORDS:
            return True
        if w == "all" and i + 1 < n and words[i + 1] == "passing":
            return True
        if w == "verified" and i + 1 < n and words[i + 1] == "working":
            return True
        if w in TESTCHECK_WORDS:
            if i + 1 < n and words[i + 1] in PASS_WORDS:
                return True
            if i + 2 < n and words[i + 1] == "are" and words[i + 2] in PASS_WORDS:
                return True
    return False

# Walk backwards from the end, collecting the current turn: assistant text/tool_use
# and their tool_results, stopping at the most recent real user message.
turn_texts = []
tool_events = []  # list of (command, is_error)
tool_use_by_id = {}

for rec in reversed(records):
    if is_real_user_turn(rec):
        break
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else rec
    role = msg.get("role", "")
    content = msg.get("content", "")
    if role == "assistant":
        if isinstance(content, str) and content.strip():
            turn_texts.append(content)
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    turn_texts.append(b["text"])
                elif b.get("type") == "tool_use" and b.get("name") == "Bash":
                    cmd = (b.get("input") or {}).get("command", "")
                    tool_use_by_id[b.get("id")] = cmd
    elif role == "user" and isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tool_events.append(b)

combined_text = " ".join(reversed(turn_texts))
made_claim = claims_success(combined_text)

if not made_claim:
    print(json.dumps({"verdict": "no-claim"}))
    sys.exit(0)

# Match tool_results back to their Bash commands, keep only test-runner ones.
test_runs = []
for tr in tool_events:
    cmd = tool_use_by_id.get(tr.get("tool_use_id"), "")
    if not cmd or not is_test_command(cmd):
        continue
    is_error = bool(tr.get("is_error"))
    test_runs.append((cmd, is_error))

if not test_runs:
    print(json.dumps({"verdict": "fail", "reason": "no-test-run", "cmd": None}))
    sys.exit(0)

# test_events were collected walking backwards, so the first one found is the
# most recent test run this turn.
last_cmd, last_failed = test_runs[0]
if last_failed:
    print(json.dumps({"verdict": "fail", "reason": "test-failed", "cmd": last_cmd}))
else:
    print(json.dumps({"verdict": "ok"}))
PYEOF
)

[ -z "$RESULT" ] && exit 0

VERDICT=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('verdict',''))" 2>/dev/null || echo "")

if [ "$VERDICT" != "fail" ]; then
  # Verified, no claim made, or transcript unreadable — either way, a clean
  # outcome resets the retry counter for the next request.
  rm -f "$STATE_FILE" 2>/dev/null
  exit 0
fi

REASON=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reason',''))" 2>/dev/null || echo "")

COUNT=0
[ -f "$STATE_FILE" ] && COUNT=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
[ -z "$COUNT" ] && COUNT=0

if [ "$COUNT" -ge "$MAX_ROUNDS" ]; then
  echo "[VERIFICATION-RETRY] gave up after $MAX_ROUNDS round(s) — claimed success ($REASON) still unverified. Not blocking further; flag this to the user."
  rm -f "$STATE_FILE" 2>/dev/null
  exit 0
fi

COUNT=$((COUNT + 1))
mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null
echo "$COUNT" > "$STATE_FILE"

if [ "$REASON" = "no-test-run" ]; then
  MSG="You claimed tests/checks pass, but no test-runner command ran this turn. Run the actual test command and confirm it exits 0 before ending the turn (retry $COUNT/$MAX_ROUNDS)."
else
  MSG="You claimed tests/checks pass, but the last test command this turn exited non-zero. Investigate the failure and fix it, or correct the claim, before ending the turn (retry $COUNT/$MAX_ROUNDS)."
fi

if [ "$WARN_ONLY" = "1" ]; then
  echo "[VERIFICATION-RETRY] (warn-only) $MSG"
  exit 0
fi

printf '{"decision": "block", "reason": %s}\n' "$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$MSG")"
exit 0
