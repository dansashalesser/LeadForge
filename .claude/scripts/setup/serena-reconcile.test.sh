#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# Functional tests for serena-reconcile.sh. No framework, no network — `claude`
# and `uvx` are stubbed on PATH, HOME points at a throwaway dir, and project
# fixtures are real git repos under $TMPDIR.
#
#   bash scripts/setup/serena-reconcile.test.sh
#
# Exits non-zero on any failure.
# ──────────────────────────────────────────────────────────────────────────────
set -u

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
RECON="$SCRIPT_DIR/serena-reconcile.sh"
ROOT="$(mktemp -d)"
PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); echo "PASS: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL: $1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want=$2 got=$3)"; fi }

# ── Stubs ─────────────────────────────────────────────────────────────────────
# `claude mcp add|remove serena --scope user ...` edits $HOME/.claude.json the
# way the real CLI does; every call is logged. `uvx ... serena project create`
# writes a project.yml listing the --language args.
BIN="$ROOT/bin"; mkdir -p "$BIN"
cat > "$BIN/claude" <<'SH'
#!/bin/bash
echo "claude $*" >> "$HOME/calls.log"
[ "$1 $2" = "mcp remove" ] && python3 - "$HOME/.claude.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); d.get("mcpServers", {}).pop("serena", None)
json.dump(d, open(sys.argv[1], "w"))
PY
if [ "$1 $2" = "mcp add" ]; then
  shift 3; while [ "$1" != "--" ]; do shift; done; shift
  cmd="$1"; shift
  python3 - "$HOME/.claude.json" "$cmd" "$@" <<'PY'
import json, sys, os
p = sys.argv[1]
d = json.load(open(p)) if os.path.exists(p) else {}
d.setdefault("mcpServers", {})["serena"] = {"type": "stdio", "command": sys.argv[2], "args": sys.argv[3:], "env": {}}
json.dump(d, open(p, "w"))
PY
fi
exit 0
SH
cat > "$BIN/uvx" <<'SH'
#!/bin/bash
echo "uvx $*" >> "$HOME/calls.log"
args=("$@"); proj=""; langs=()
for ((i=0; i<${#args[@]}; i++)); do
  case "${args[$i]}" in
    create) proj="${args[$((i+1))]}" ;;
    --language) langs+=("${args[$((i+1))]}") ;;
  esac
done
[ -n "$proj" ] || exit 0
mkdir -p "$proj/.serena"
{ echo "project_name: demo"; echo "language_servers:"; for l in "${langs[@]}"; do echo "- $l"; done; echo "encoding: utf-8"; } > "$proj/.serena/project.yml"
SH
chmod +x "$BIN/claude" "$BIN/uvx"

run() { HOME="$H" PATH="$BIN:$PATH" SDD_SERENA_VERSION=9.9.9 bash "$RECON" "$@"; }
new_home() { H="$ROOT/home-$1"; mkdir -p "$H"; : > "$H/calls.log"; }
serena_args() { python3 -c "import json;print(' '.join(json.load(open('$H/.claude.json'))['mcpServers']['serena']['args']))"; }
PINNED="--from serena-agent==9.9.9 serena start-mcp-server --context claude-code --open-web-dashboard False"

make_repo() {  # make_repo <name> <file>...
  local p="$ROOT/$1"; shift
  mkdir -p "$p"; git -C "$p" init -q
  local f; for f in "$@"; do mkdir -p "$(dirname "$p/$f")"; : > "$p/$f"; done
  git -C "$p" add -A >/dev/null 2>&1
  echo "$p"
}
yml_list() { python3 - "$1" <<'PY'
import sys
L = open(sys.argv[1]).read().split("\n")
i = next(i for i, l in enumerate(L) if ":" in l and l.split(":", 1)[0] in ("language_servers", "languages"))
rest = L[i].split(":", 1)[1].strip()
if rest.startswith("[") and rest.endswith("]"): print(" ".join(x.strip() for x in rest[1:-1].split(",") if x.strip()))
else:
    out = []; j = i + 1
    while j < len(L) and L[j].lstrip()[:1] == "-" and L[j].lstrip()[1:2].isspace(): out.append(L[j].split("-", 1)[1].strip()); j += 1
    print(" ".join(out))
PY
}

# ── Global mode ───────────────────────────────────────────────────────────────
new_home git
echo '{"projects":{"/x":{}},"mcpServers":{"serena":{"type":"stdio","command":"uvx","args":["--from","git+https://github.com/oraios/serena","serena","start-mcp-server","--context","claude-code"],"env":{}},"other":{"command":"x"}}}' > "$H/.claude.json"
out="$(run --global)"
check "global: git+ registration repinned" "$PINNED" "$(serena_args)"
check "global: other servers untouched" "x" "$(python3 -c "import json;print(json.load(open('$H/.claude.json'))['mcpServers']['other']['command'])")"
check "global: other top-level keys untouched" "yes" "$(python3 -c "import json;print('yes' if '/x' in json.load(open('$H/.claude.json'))['projects'] else 'no')")"
check "global: reports the migration" "yes" "$(echo "$out" | grep -q 'repinned' && echo yes || echo no)"
: > "$H/calls.log"; out="$(run --global)"
check "global: second run is a no-op" "0" "$(grep -c '^claude' "$H/calls.log")"
check "global: second run says already pinned" "yes" "$(echo "$out" | grep -q 'already pinned' && echo yes || echo no)"

new_home noflag
echo '{"mcpServers":{"serena":{"type":"stdio","command":"uvx","args":["--from","serena-agent==9.9.9","serena","start-mcp-server","--context","claude-code"],"env":{}}}}' > "$H/.claude.json"
run --global >/dev/null
check "global: pinned but missing dashboard flag is fixed" "$PINNED" "$(serena_args)"

new_home absent
echo '{"mcpServers":{}}' > "$H/.claude.json"
run --global >/dev/null
check "global: absent registration gets registered" "$PINNED" "$(serena_args)"
check "global: absent means no remove call" "0" "$(grep -c 'mcp remove' "$H/calls.log")"

new_home broken
echo '{not json' > "$H/.claude.json"
out="$(run --global)"
check "global: invalid ~/.claude.json is left alone" "{not json" "$(cat "$H/.claude.json")"
check "global: invalid json makes no CLI call" "0" "$(grep -c '^claude' "$H/calls.log")"

# ── Project mode ──────────────────────────────────────────────────────────────
new_home proj
P="$(make_repo tsonly src/app.ts services/api.py)"
mkdir -p "$P/.serena"
printf 'project_name: demo\nlanguage_servers:\n- typescript\n\nencoding: utf-8\n' > "$P/.serena/project.yml"
out="$(run "$P")"
check "project: missing python is appended, order kept" "typescript python" "$(yml_list "$P/.serena/project.yml")"
check "project: other keys untouched" "encoding: utf-8" "$(grep '^encoding' "$P/.serena/project.yml")"
check "project: reports what it added" "yes" "$(echo "$out" | grep -q 'added python' && echo yes || echo no)"
before="$(cat "$P/.serena/project.yml")"; out="$(run "$P")"
check "project: second run changes nothing" "$before" "$(cat "$P/.serena/project.yml")"
check "project: second run is silent" "" "$out"

P="$(make_repo extra a.py)"
mkdir -p "$P/.serena"; printf 'language_servers:\n- rust\n- python\n' > "$P/.serena/project.yml"
run "$P" >/dev/null
check "project: never removes a language" "rust python" "$(yml_list "$P/.serena/project.yml")"

P="$(make_repo inline a.py b.tsx)"
mkdir -p "$P/.serena"; printf 'languages: [python]\n' > "$P/.serena/project.yml"
run "$P" >/dev/null
check "project: inline list form is extended" "python typescript" "$(yml_list "$P/.serena/project.yml")"

P="$(make_repo fresh a.py web/index.js)"
: > "$H/calls.log"; run "$P" >/dev/null
check "project: missing yml is created via pinned serena" "python typescript" "$(yml_list "$P/.serena/project.yml")"
check "project: create uses the pinned release" "1" "$(grep -c 'serena-agent==9.9.9 serena project create' "$H/calls.log")"

P="$(make_repo nolang README.md)"
: > "$H/calls.log"; run "$P" >/dev/null
check "project: repo with no py/ts gets no .serena" "no" "$([ -d "$P/.serena" ] && echo yes || echo no)"
check "project: repo with no py/ts makes no call" "0" "$(grep -c . "$H/calls.log")"

P="$(make_repo untracked)"; : > "$P/stray.py"
run "$P" >/dev/null
check "project: untracked files do not count" "no" "$([ -d "$P/.serena" ] && echo yes || echo no)"

P="$(make_repo scalar a.py)"
mkdir -p "$P/.serena"; printf 'language: python\n' > "$P/.serena/project.yml"
out="$(run "$P")"; rc=$?
check "project: pre-list scalar format is left alone" "language: python" "$(cat "$P/.serena/project.yml")"
check "project: scalar format warns" "yes" "$(echo "$out" | grep -q 'no language_servers list' && echo yes || echo no)"
check "project: always exits 0" "0" "$rc"

P="$(make_repo override a.py)"
mkdir -p "$P/.serena"; printf 'language_servers:\n- python\n' > "$P/.serena/project.yml"
echo '{"mcpServers":{"serena":{"command":"uvx","args":["--from","git+https://github.com/oraios/serena"]}}}' > "$P/.mcp.json"
out="$(run "$P")"
check "project: warns on a git+ project-scoped override" "yes" "$(echo "$out" | grep -q 'overrides the pinned' && echo yes || echo no)"

# ── Check mode ────────────────────────────────────────────────────────────────
new_home check
echo '{"mcpServers":{}}' > "$H/.claude.json"
P="$(make_repo chk a.py)"
mkdir -p "$P/.serena"; printf 'language_servers:\n- python\n' > "$P/.serena/project.yml"
run "$P" --check; check "check: unpinned global fails" "1" "$?"
run --global >/dev/null
run "$P" --check; check "check: pinned + complete yml passes" "0" "$?"
printf 'language_servers:\n- typescript\n' > "$P/.serena/project.yml"
run "$P" --check; check "check: incomplete yml fails" "1" "$?"
check "check: does not modify the yml" "- typescript" "$(sed -n 2p "$P/.serena/project.yml")"

# ── Usage ─────────────────────────────────────────────────────────────────────
run "$ROOT/does-not-exist" >/dev/null 2>&1; check "usage: missing dir exits 2" "2" "$?"

rm -rf "$ROOT"
echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
