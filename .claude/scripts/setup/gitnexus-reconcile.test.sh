#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# Functional tests for gitnexus-reconcile.sh. No framework, no network, no npx —
# builds throwaway project fixtures under $TMPDIR and asserts on the results.
#
#   bash scripts/setup/gitnexus-reconcile.test.sh
#
# Exits non-zero on any failure.
# ──────────────────────────────────────────────────────────────────────────────
set -u

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
RECON="$SCRIPT_DIR/gitnexus-reconcile.sh"
ROOT="$(mktemp -d)"
PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); echo "PASS: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL: $1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want=$2 got=$3)"; fi }

BLOCK='<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **demo** (10 symbols).

- **MUST run impact analysis before editing any symbol.**

| Task | Skill |
|------|-------|
| Understand architecture | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Already correct | `~/.claude/skills/gitnexus-guide/SKILL.md` |
<!-- gitnexus:end -->'

make_proj() {
  local name="$1" with_block="$2" with_index="$3" with_mcp="$4"
  local p="$ROOT/$name"
  mkdir -p "$p/.claude"
  {
    echo "# Demo"
    echo ""
    echo "## Commands"
    echo "run the thing"
    echo ""
    [ "$with_block" = yes ] && echo "$BLOCK"
    echo ""
    echo "## Tail section"
    echo "still here"
  } > "$p/CLAUDE.md"
  printf '{\n  "permissions": {\n    "allow": [\n      "Bash(npx gitnexus *)"\n    ]\n  }\n}\n' > "$p/.claude/settings.json"
  [ "$with_index" = yes ] && mkdir -p "$p/.gitnexus"
  [ "$with_mcp" = yes ] && printf '{\n  "mcpServers": {\n    "gitnexus": {\n      "command": "npx",\n      "args": ["-y", "gitnexus", "mcp"]\n    }\n  }\n}\n' > "$p/.mcp.json"
  echo "$p"
}

echo "=== 1. block + no index -> stripped ==="
P="$(make_proj dead-noindex yes no no)"
bash "$RECON" "$P" >/dev/null
check "block removed"        "0" "$(grep -c 'gitnexus:start' "$P/CLAUDE.md")"
check "head preserved"       "1" "$(grep -c '^## Commands' "$P/CLAUDE.md")"
check "tail preserved"       "1" "$(grep -c '^## Tail section' "$P/CLAUDE.md")"
check "single blank seam"    "0" "$(awk 'prev=="" && $0=="" {n++} {prev=$0} END{print n+0}' "$P/CLAUDE.md")"

echo "=== 2. block + index, no MCP -> self-healed (wired, block kept) ==="
P="$(make_proj heal-nomcp yes yes no)"
bash "$RECON" "$P" >/dev/null
check "block kept"           "1" "$(grep -c 'gitnexus:start' "$P/CLAUDE.md")"
check "mcp.json created"     "1" "$(grep -c '"gitnexus": {' "$P/.mcp.json" 2>/dev/null)"
check "paths repaired"       "0" "$(grep -c '[^/]\.claude/skills/' "$P/CLAUDE.md")"

echo "=== 2b. block + index, MCP unwirable (broken .mcp.json) -> stripped ==="
P="$(make_proj dead-nomcp yes yes no)"
printf '{ "mcpServers": ' > "$P/.mcp.json"
bash "$RECON" "$P" >/dev/null 2>&1
check "block removed"        "0" "$(grep -c 'gitnexus:start' "$P/CLAUDE.md")"

echo "=== 3. block + index + MCP -> kept, compacted ==="
P="$(make_proj live yes yes yes)"
bash "$RECON" "$P" >/dev/null
check "block kept"           "1" "$(grep -c 'gitnexus:start' "$P/CLAUDE.md")"
check "block closed"         "1" "$(grep -c 'gitnexus:end' "$P/CLAUDE.md")"
check "repo name carried"    "1" "$(grep -c 'Indexed as \*\*demo\*\*' "$P/CLAUDE.md")"
check "impact rule"          "1" "$(grep -c 'MUST run `impact' "$P/CLAUDE.md")"
check "detect_changes rule"  "1" "$(grep -c 'MUST run `detect_changes' "$P/CLAUDE.md")"
check "UNKNOWN rule"         "1" "$(grep -c 'risk: UNKNOWN` is unresolved' "$P/CLAUDE.md")"
check "rename rule"          "1" "$(grep -c 'NEVER rename' "$P/CLAUDE.md")"
check "old body gone"        "0" "$(grep -c '| Task | Skill |' "$P/CLAUDE.md")"
check "no project-local path" "0" "$(grep -c '[^/]\.claude/skills/' "$P/CLAUDE.md")"
check "head preserved"       "1" "$(grep -c '^## Commands' "$P/CLAUDE.md")"
check "tail preserved"       "1" "$(grep -c '^## Tail section' "$P/CLAUDE.md")"
cp "$P/CLAUDE.md" "$ROOT/live-once.md"
bash "$RECON" "$P" >/dev/null
check "idempotent"           "" "$(diff "$ROOT/live-once.md" "$P/CLAUDE.md")"

echo "=== 3b. SDD_GITNEXUS_FULL_BLOCK=1 -> upstream body kept, paths repaired ==="
P="$(make_proj live-full yes yes yes)"
SDD_GITNEXUS_FULL_BLOCK=1 bash "$RECON" "$P" >/dev/null
check "no project-local path" "0" "$(grep -c '[^/]\.claude/skills/' "$P/CLAUDE.md")"
check "global paths"         "3" "$(grep -c '~/.claude/skills/gitnexus' "$P/CLAUDE.md")"

echo "=== 3c. unterminated block -> not compacted, tail kept ==="
P="$(make_proj live-open yes yes yes)"
grep -vF 'gitnexus:end' "$P/CLAUDE.md" > "$P/x" && mv "$P/x" "$P/CLAUDE.md"
bash "$RECON" "$P" >/dev/null
check "tail preserved"       "1" "$(grep -c '^## Tail section' "$P/CLAUDE.md")"

echo "=== 4. no block -> untouched ==="
P="$(make_proj noblock no no no)"
cp "$P/CLAUDE.md" "$ROOT/noblock-before.md"
bash "$RECON" "$P" >/dev/null
check "byte-identical"       "" "$(diff "$ROOT/noblock-before.md" "$P/CLAUDE.md")"

echo "=== 5. --check exit codes ==="
P="$(make_proj chk-none no no no)";  bash "$RECON" "$P" --check; check "no index/mcp -> 1" "1" "$?"
P="$(make_proj chk-idx no yes no)";  bash "$RECON" "$P" --check; check "index only  -> 1" "1" "$?"
P="$(make_proj chk-both no yes yes)"; bash "$RECON" "$P" --check; check "both        -> 0" "0" "$?"

echo "=== 6. --wire ==="
P="$(make_proj wire no yes no)"
bash "$RECON" "$P" --wire >/dev/null
check "mcp.json written"     "1" "$(grep -c '"gitnexus": {' "$P/.mcp.json" 2>/dev/null)"
check "enabled in settings"  "1" "$(grep -c 'enabledMcpjsonServers' "$P/.claude/settings.json")"
check "perms preserved"      "1" "$(grep -c 'Bash(npx gitnexus \*)' "$P/.claude/settings.json")"
bash "$RECON" "$P" --check;  check "now wired -> 0" "0" "$?"
cp "$P/.mcp.json" "$ROOT/wire-once.json"
bash "$RECON" "$P" --wire >/dev/null
check "wire idempotent"      "" "$(diff "$ROOT/wire-once.json" "$P/.mcp.json")"

echo "=== 7. --wire without index -> no files created ==="
P="$(make_proj wire-noindex no no no)"
bash "$RECON" "$P" --wire >/dev/null
check "no .mcp.json"         "absent" "$([ -f "$P/.mcp.json" ] && echo present || echo absent)"

echo "=== 8. malformed settings.json is not clobbered ==="
P="$(make_proj broken no yes no)"
printf '{ "permissions": { "allow": [ "Bash(x)" ' > "$P/.claude/settings.json"
cp "$P/.claude/settings.json" "$ROOT/broken-before.json"
bash "$RECON" "$P" --wire >/dev/null 2>&1
check "settings untouched"   "" "$(diff "$ROOT/broken-before.json" "$P/.claude/settings.json")"

echo "=== 9. --global pins the CLI (stubbed npm/gitnexus/brew, no network) ==="
STUB="$ROOT/stub-bin"; mkdir -p "$STUB"
printf '#!/bin/bash\necho "%s"\n' "\${FAKE_GN_VER:-1.0.0}" > "$STUB/gitnexus"
printf '#!/bin/bash\necho "npm $*" >> "%s"\n' "$ROOT/calls.log" > "$STUB/npm"
printf '#!/bin/bash\necho "brew $*" >> "%s"\n[ "$1" = list ] && exit 1\nexit 0\n' "$ROOT/calls.log" > "$STUB/brew"
chmod +x "$STUB"/*
: > "$ROOT/calls.log"
PATH="$STUB:$PATH" FAKE_GN_VER=1.0.0 SDD_GITNEXUS_VERSION=9.9.9 bash "$RECON" --global >/dev/null
check "off-version -> npm install pinned" "1" \
  "$(grep -c 'npm install -g gitnexus@9.9.9' "$ROOT/calls.log")"
if [ "$(uname -s)" = "Darwin" ]; then
  check "missing openssl@3 -> brew install" "1" "$(grep -c 'brew install openssl@3' "$ROOT/calls.log")"
fi
: > "$ROOT/calls.log"
PATH="$STUB:$PATH" FAKE_GN_VER=9.9.9 SDD_GITNEXUS_VERSION=9.9.9 bash "$RECON" --global >/dev/null
check "on-version -> no npm call" "0" "$(grep -c '^npm' "$ROOT/calls.log")"
PATH="$STUB:$PATH" bash "$RECON" --global >/dev/null; check "--global exits 0" "0" "$?"

echo "=== 10. bad usage ==="
bash "$RECON" 2>/dev/null; check "no args -> 2" "2" "$?"
bash "$RECON" "$ROOT/does-not-exist" 2>/dev/null; check "bad dir -> 2" "2" "$?"

echo ""
echo "PASS=$PASS FAIL=$FAIL"
rm -rf "$ROOT"
[ "$FAIL" -eq 0 ]
