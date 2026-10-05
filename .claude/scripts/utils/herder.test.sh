#!/usr/bin/env bash
# herder.test.sh — herder module + dashboard herder API.
#
# Split into two tiers:
#   OFFLINE — always run. Pure-function behaviour and the dashboard endpoint
#             authorization checks, using a dashboard started on a spare port so
#             a dashboard you already have open is never disturbed.
#   LIVE    — only with HERDER_LIVE=1. Actually starts and stops a Claude Code
#             session through Herdr. Costs real subscription tokens, so it is
#             opt-in rather than default.
#
# Run:      bash scripts/utils/herder.test.sh
# Live too: HERDER_LIVE=1 bash scripts/utils/herder.test.sh

set -uo pipefail

HERE="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
HARNESS="$(cd -P -- "$HERE/../.." && pwd -P)"
PORT="${HERDER_TEST_PORT:-4573}"
PASS=0
FAIL=0
DASH_PID=""

# herdr lives wherever the user's package manager put it. Ask lib/tool-paths.sh
# — it queries uv/pipx/brew for their real layouts — instead of guessing
# ~/.local/bin, which is only correct for one of them and silently wrong on a
# machine that installed herdr any other way.
if [ -f "$HARNESS/scripts/lib/tool-paths.sh" ]; then
  . "$HARNESS/scripts/lib/tool-paths.sh"
  ensure_tool_bin_on_path
fi

cleanup() {
  [ -n "$DASH_PID" ] && kill "$DASH_PID" 2>/dev/null
  return 0
}
trap cleanup EXIT

check() {
  local label="$1" want="$2" got="$3"
  if [ "$want" = "$got" ]; then
    echo "  ok    $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $label — wanted '$want', got '$got'"
    FAIL=$((FAIL + 1))
  fi
}

echo "herder module + dashboard API"

# ── Module loads and reports honestly when Herdr is absent ───────────────────
check "module imports" "ok" \
  "$(cd "$HERE" && python3 -c 'import herder; print("ok")' 2>&1 | tail -1)"

check "claude is a supported kind" "True" \
  "$(cd "$HERE" && python3 -c 'import herder; print("claude" in herder.AGENT_KINDS)')"

# Modes come from the agent's own validator, not a list in herder.py. With claude
# on PATH this must report discovered=true and include the modes it really takes;
# the hardcoded list this replaced was missing auto/manual/dontAsk entirely.
check "permission modes are discovered from the agent" "True" \
  "$(cd "$HERE" && python3 -c '
import herder, shutil
d = herder.discover_permission_modes("claude")
if not shutil.which("claude"):
    print(not d["discovered"] and len(d["modes"]) > 0)   # honest fallback
else:
    print(d["discovered"] and "bypassPermissions" in d["modes"] and "plan" in d["modes"])')"

check "an unprobeable agent is flagged, not silently faked" "True" \
  "$(cd "$HERE" && python3 -c '
import herder
d = herder.discover_permission_modes("definitelynotarealagent")
print(d["discovered"] is False and "fallback" in d["source"])')"

# Models come from the dashboard's live-refreshed pricing catalogue.
check "models are discovered per agent kind" "True" \
  "$(cd "$HERE" && python3 -c '
import herder
c = herder.discover_models("claude")
print(c["discovered"] and len(c["models"]) > 0 and "opus" in c["aliases"])')"

check "an unmapped agent kind yields no invented models" "True" \
  "$(cd "$HERE" && python3 -c '
import herder
d = herder.discover_models("definitelynotarealagent")
print(d["discovered"] is False and d["models"] == [])')"

# The transcript-blinding env var must be scrubbed, or herder-spawned sessions
# become invisible to token-forensics.py and session-judge.
check "CLAUDE_CODE_CHILD_SESSION is scrubbed" "True" \
  "$(cd "$HERE" && python3 -c 'import herder; print("CLAUDE_CODE_CHILD_SESSION" in herder.SCRUB_ENV)')"

check "scrubbed var absent from spawn env" "True" \
  "$(cd "$HERE" && CLAUDE_CODE_CHILD_SESSION=1 python3 -c \
     'import herder; print("CLAUDE_CODE_CHILD_SESSION" not in herder._clean_env())')"

# Bad input is refused before any subprocess runs.
check "rejects a non-directory repo" "True" \
  "$(cd "$HERE" && python3 -c '
import herder
try:
    herder.spawn("/definitely/not/a/real/dir", "x")
    print(False)
except herder.HerderError as e:
    print("not a directory" in str(e))')"

check "rejects an unknown agent kind" "True" \
  "$(cd "$HERE" && python3 -c '
import herder
try:
    herder.spawn("'"$HARNESS"'", "x", kind="notarealagent")
    print(False)
except herder.HerderError as e:
    print("unsupported agent kind" in str(e))')"

# Rejection is against the DISCOVERED set. When the agent could not be probed the
# check is deliberately permissive — turning a probe failure into a bogus
# "unsupported" error would be worse than letting the agent itself refuse.
check "rejects a mode the agent does not accept" "True" \
  "$(cd "$HERE" && python3 -c '
import herder
if not herder.discover_permission_modes("claude")["discovered"]:
    print(True)   # nothing to assert against; probe unavailable
    raise SystemExit
try:
    herder.spawn("'"$HARNESS"'", "x", permission_mode="yolo")
    print(False)
except herder.HerderError as e:
    print("not accepted by claude" in str(e))')"

# Generated agent names must be unique, or the second spawn collides.
check "generated names are unique" "True" \
  "$(cd "$HERE" && python3 -c '
import herder
print(herder._unique_name("a") != herder._unique_name("a"))')"

check "generated names sanitize separators" "True" \
  "$(cd "$HERE" && python3 -c '
import herder
n = herder._unique_name("my repo/name")
print(" " not in n and "/" not in n)')"

# Herdr 0.8.2: must start with a lowercase letter, then [a-z0-9-_], 1-32 chars.
# "boxB" was rejected live with invalid_agent_name until the lowercasing landed.
check "generated names satisfy Herdr's name rule" "True" \
  "$(cd "$HERE" && python3 -c '
import herder
ok = True
for label in ["boxB", "UPPER", "my repo/name", "2fix", "", "-x-", "a"*80, "Wörk"]:
    n = herder._unique_name(label)
    ok = ok and (
        1 <= len(n) <= 32
        and n[0].isalpha() and n[0].islower()
        and all(c.islower() or c.isdigit() or c in "-_" for c in n)
    )
print(ok)')"

# ── Prompt-box completion (/skill, @path) ────────────────────────────────────
# Throwaway repo + throwaway HOME, so the answers depend only on this fixture and
# not on whatever ~/.claude happens to hold on this machine.
CTREE="$(mktemp -d)"
mkdir -p "$CTREE/repo/.claude/skills/alpha-skill" "$CTREE/repo/.claude/commands/ns" \
         "$CTREE/repo/src/deep" "$CTREE/home/.claude/skills/user-skill"
printf -- '---\nname: alpha-skill\ndescription: >\n  folded line one\n  line two\n---\n' \
  > "$CTREE/repo/.claude/skills/alpha-skill/SKILL.md"
printf -- '---\ndescription: "namespaced cmd"\n---\n' > "$CTREE/repo/.claude/commands/ns/cmd.md"
printf -- '---\ndescription: from home\n---\n' > "$CTREE/home/.claude/skills/user-skill/SKILL.md"
touch "$CTREE/repo/README.md" "$CTREE/repo/src/deep/README.md" "$CTREE/repo/ignored.log"
printf '*.log\n' > "$CTREE/repo/.gitignore"
git -C "$CTREE/repo" init -q

complete_py() {
  (cd "$HERE" && HOME="$CTREE/home" python3 -c "
import herder
d = herder.complete('$CTREE/repo', '$1', '$2')
print(' '.join(i['name'] + ('=' + i['description'] if i.get('description') else '') for i in d['items']))")
}

check "skills: project, nested command, user, folded description" \
  "ns:cmd=namespaced cmd user-skill=from home alpha-skill=folded line one line two" \
  "$(complete_py skill '')"
check "skills: query filters" "ns:cmd=namespaced cmd" "$(complete_py skill 'ns:')"
check "files: shallow match ranks first" \
  "README.md src/deep/README.md" "$(complete_py file 'readme')"
check "files: gitignored paths are not offered" "" "$(complete_py file 'log')"
check "files: directories are offered with a trailing slash" \
  "src/ src/deep/ src/deep/README.md" "$(complete_py file 'src/')"
check "unknown kind is an error, not an empty list" "HerderError" \
  "$(cd "$HERE" && python3 -c "
import herder
try:
    herder.complete('$CTREE/repo', 'nope', '')
except herder.HerderError:
    print('HerderError')")"
rm -rf "$CTREE"

# ── Session attribution ──────────────────────────────────────────────────────
# Regression for a live misattribution: resolution used to pick the newest
# transcript modified after the spawn. With an interactive session already open
# in the target repo, that file is always newest, so a freshly spawned agent was
# credited with 94,259,775 tokens belonging to the session that spawned it.
# Set difference against the pre-spawn snapshot has no such failure mode.
check "ignores an already-active session in the target repo" "True" \
  "$(cd "$HERE" && python3 -c '
import json, pathlib, tempfile, time, os
import herder

tmp = tempfile.mkdtemp()
repo = pathlib.Path(tmp) / "repo"; repo.mkdir()
proj = pathlib.Path(tmp) / "projects" / str(repo).replace("/", "-")
proj.mkdir(parents=True)
herder._project_slug_dir = lambda p, _proj=proj: _proj

busy = proj / "already-open-session.jsonl"
busy.write_text(json.dumps({"type": "agent-name", "agentName": "some-other-session"}))
known = sorted(herder._transcript_stems(repo))

spawned = proj / "the-new-session.jsonl"
spawned.write_text(json.dumps({"type": "agent-name", "agentName": "herder:mine-1"}))
# The pre-existing session keeps being written, so it is the NEWEST file — the
# case that made the original mtime-based rule pick the wrong one.
time.sleep(0.02)
os.utime(busy, None)

print(herder._resolve_session_id(repo, known, "mine-1") == "the-new-session")')"

# A stale process holding pre-fix code re-resolved on every poll and wrote the
# interactive session's id over a correct one. Any id inside known_before is wrong
# by construction, so it is discarded on read rather than trusted.
check "discards a poisoned session_id that predates the spawn" "True" \
  "$(cd "$HERE" && python3 -c '
import json, pathlib, tempfile
import herder
tmp = tempfile.mkdtemp()
repo = pathlib.Path(tmp) / "repo"; repo.mkdir()
proj = pathlib.Path(tmp) / "projects" / "p"; proj.mkdir(parents=True)
herder._project_slug_dir = lambda p, _proj=proj: _proj
(proj / "interactive.jsonl").write_text(
    json.dumps({"type": "agent-name", "agentName": "an-interactive-session"}))
(proj / "spawned.jsonl").write_text(
    json.dumps({"type": "agent-name", "agentName": "herder:x-1"}))
entry = {
    "name": "x-1", "cwd": str(repo), "spawn_at": 0,
    "known_before": ["interactive"],
    "session_id": "interactive",          # poisoned by a stale writer
}
herder._write_ledger = lambda e: None      # keep the test off disk
print(herder._ensure_session_id(entry)["session_id"] == "spawned")')"

# Three agents spawned seconds apart each claimed a sibling's transcript under the
# positional heuristic. Matching the recorded --name tag keeps them separate.
check "concurrent agents resolve to their own transcript" "True" \
  "$(cd "$HERE" && python3 -c '
import json, pathlib, tempfile
import herder
tmp = tempfile.mkdtemp()
repo = pathlib.Path(tmp) / "repo"; repo.mkdir()
proj = pathlib.Path(tmp) / "p"; proj.mkdir(parents=True)
herder._project_slug_dir = lambda p, _proj=proj: _proj
for stem, agent in [("t-a", "alpha-1"), ("t-b", "beta-2"), ("t-c", "gamma-3")]:
    (proj / (stem + ".jsonl")).write_text(
        json.dumps({"type": "agent-name", "agentName": "herder:" + agent}) + "\n")
ok = all(herder._resolve_session_id(repo, [], a) == s
         for s, a in [("t-a", "alpha-1"), ("t-b", "beta-2"), ("t-c", "gamma-3")])
print(ok)')"

# A shared ledger file is rewritten by any process holding older code.
check "a mismatched session_id is corrected on read" "True" \
  "$(cd "$HERE" && python3 -c '
import json, pathlib, tempfile
import herder
tmp = tempfile.mkdtemp()
repo = pathlib.Path(tmp) / "repo"; repo.mkdir()
proj = pathlib.Path(tmp) / "p"; proj.mkdir(parents=True)
herder._project_slug_dir = lambda p, _proj=proj: _proj
herder._write_ledger = lambda e: None
for stem, agent in [("mine", "alpha-1"), ("theirs", "gamma-3")]:
    (proj / (stem + ".jsonl")).write_text(
        json.dumps({"type": "agent-name", "agentName": "herder:" + agent}) + "\n")
entry = {"name": "alpha-1", "cwd": str(repo), "known_before": [],
         "session_id": "theirs"}          # a sibling'"'"'s transcript, wrongly stored
print(herder._ensure_session_id(entry)["session_id"] == "mine")')"

check "reports no session rather than guessing when none appeared" "True" \
  "$(cd "$HERE" && python3 -c '
import pathlib, tempfile
import herder
tmp = tempfile.mkdtemp()
repo = pathlib.Path(tmp) / "repo"; repo.mkdir()
proj = pathlib.Path(tmp) / "projects" / "p"; proj.mkdir(parents=True)
herder._project_slug_dir = lambda p, _proj=proj: _proj
busy = proj / "already-open.jsonl"; busy.write_text("{}")
known = sorted(herder._transcript_stems(repo))
print(herder._resolve_session_id(repo, known) is None)')"

# ── Dashboard endpoint authorization ─────────────────────────────────────────
echo "  … starting dashboard on port $PORT (this takes ~30s)"
python3 "$HERE/dashboard.py" --no-open --port "$PORT" >/tmp/herder-test-dash.log 2>&1 &
DASH_PID=$!

READY=""
for _ in $(seq 1 60); do
  sleep 1
  if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then READY=1; break; fi
done

if [ -z "$READY" ]; then
  echo "  FAIL  dashboard did not start on port $PORT"
  echo "        $(tail -3 /tmp/herder-test-dash.log)"
  FAIL=$((FAIL + 1))
else
  TOKEN="$(curl -fsS "http://127.0.0.1:$PORT/" \
           | grep -o "var HERDER_TOKEN = '[^']*'" | head -1 | cut -d"'" -f2)"

  check "served page carries a token" "True" \
    "$(python3 -c "print(len('$TOKEN') > 20)")"

  code() { curl -s -o /dev/null -w "%{http_code}" "$@"; }

  check "herder-status without a token is refused" "403" \
    "$(code "http://127.0.0.1:$PORT/api/herder-status")"

  check "herder-list without a token is refused" "403" \
    "$(code "http://127.0.0.1:$PORT/api/herder-list")"

  check "herder-spawn without a token is refused" "403" \
    "$(code -X POST -H 'Content-Type: application/json' \
       -d '{"repo":"/tmp","label":"x"}' "http://127.0.0.1:$PORT/api/herder-spawn")"

  check "a wrong token is refused" "403" \
    "$(code -H 'X-Herder-Token: not-the-token' "http://127.0.0.1:$PORT/api/herder-status")"

  check "a valid token is accepted" "200" \
    "$(code -H "X-Herder-Token: $TOKEN" "http://127.0.0.1:$PORT/api/herder-status")"

  # A stolen token must still not work from another site.
  check "a foreign Origin is refused even with a valid token" "403" \
    "$(code -H "X-Herder-Token: $TOKEN" -H 'Origin: https://evil.example' \
       "http://127.0.0.1:$PORT/api/herder-status")"

  check "the dashboard's own Origin is accepted" "200" \
    "$(code -H "X-Herder-Token: $TOKEN" -H "Origin: http://localhost:$PORT" \
       "http://127.0.0.1:$PORT/api/herder-status")"

  # No CORS header: these endpoints act on the machine, so no other site should
  # be able to read their replies either.
  check "herder replies carry no CORS header" "0" \
    "$(curl -s -D- -o /dev/null -H "X-Herder-Token: $TOKEN" \
       "http://127.0.0.1:$PORT/api/herder-status" | grep -ci 'access-control')"
fi

# ── LIVE tier ────────────────────────────────────────────────────────────────
if [ "${HERDER_LIVE:-0}" = "1" ]; then
  echo "  … LIVE: spawning a real Claude session"
  SPAWN="$(python3 "$HERE/herder.py" spawn "$HARNESS" livetest 2>&1)"
  WS="$(printf '%s' "$SPAWN" | python3 -c \
       'import json,sys
try:
    print(json.load(sys.stdin).get("workspace_id",""))
except Exception:
    print("")' 2>/dev/null)"
  check "live spawn returns a workspace id" "True" \
    "$(python3 -c "print(len('$WS') > 0)")"
  if [ -n "$WS" ]; then
    check "spawned agent appears in the roster" "True" \
      "$(python3 "$HERE/herder.py" list | python3 -c \
         'import json,sys; print(any("livetest" in a["name"] for a in json.load(sys.stdin)))')"
    python3 "$HERE/herder.py" stop "$WS" >/dev/null 2>&1
    check "stopped agent leaves the roster" "True" \
      "$(python3 "$HERE/herder.py" list | python3 -c \
         'import json,sys; print(not any("livetest" in a["name"] for a in json.load(sys.stdin)))')"
  fi
else
  echo "  skip  LIVE tier (set HERDER_LIVE=1 — spawns a real session, costs tokens)"
fi

echo
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
