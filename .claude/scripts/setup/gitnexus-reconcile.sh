#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# gitnexus-reconcile.sh <project_dir> [--wire|--check] | gitnexus-reconcile.sh --global
#
# Keeps a project's GitNexus state self-consistent. `npx gitnexus setup` writes a
# managed block into CLAUDE.md or AGENTS.md containing MUST/NEVER rules that call
# gitnexus_* MCP tools. That block is committed; the .gitnexus/ index is
# gitignored and the MCP server lives in local config — so on a fresh clone,
# after `gitnexus clean`, or when the server was never wired, the block orders
# the agent to call tools that do not exist. Every edit and commit then
# deadlocks.
#
# Modes:
#   --check   quiet; exit 0 when index AND MCP server are both present
#   --wire    write the MCP server config (idempotent), then report
#   (none)    reconcile the block: strip it when the tools are not callable,
#             or compact it (unless SDD_GITNEXUS_FULL_BLOCK=1) and repair its
#             skill paths and bare tool names when they are
#   --global  pin the global `gitnexus` CLI to GITNEXUS_VERSION (npm -g), and on
#             macOS make sure Homebrew openssl@3 is present first — 1.6.12's
#             LadybugDB binary dlopens libssl.3.dylib from the Homebrew openssl@3
#             prefix, and every command fails without it. Pinning matters beyond the
#             crash: the managed block is version-dependent output, and an older
#             CLI rewrites it with a template that drops rules (risk: UNKNOWN,
#             partial/truncated) the committed copy carries.
#
# The pinned version lives here and nowhere else; SDD_GITNEXUS_VERSION overrides.
#
# The block is looked for in CLAUDE.md first, then AGENTS.md (a project may
# keep its conventions in AGENTS.md and have CLAUDE.md just `@`-import it).
# Only bytes between <!-- gitnexus:start --> and <!-- gitnexus:end --> are ever
# rewritten, plus the blank lines that surround the block when it is removed.
# Always exits 0 in reconcile mode — never blocks an install or update.
# ──────────────────────────────────────────────────────────────────────────────
set -u

GITNEXUS_VERSION="${SDD_GITNEXUS_VERSION:-1.6.12}"

# ── Global: the CLI itself and its native dependency ──────────────────────────
reconcile_global() {
  if [ "$(uname -s)" = "Darwin" ]; then
    if ! command -v brew >/dev/null 2>&1; then
      echo "  WARNING: Homebrew not found — GitNexus needs openssl@3 (https://brew.sh)."
    elif ! brew list --versions openssl@3 >/dev/null 2>&1; then
      echo "  Installing openssl@3 (required by GitNexus's LadybugDB binary)..."
      brew install openssl@3 >/dev/null 2>&1 || \
        echo "  WARNING: brew install openssl@3 failed — gitnexus will not load."
    fi
  fi

  if ! command -v npm >/dev/null 2>&1; then
    echo "  WARNING: npm not found — cannot pin gitnexus@$GITNEXUS_VERSION."
    return 0
  fi
  local cur
  cur="$(gitnexus --version 2>/dev/null | head -1 || true)"
  if [ "$cur" = "$GITNEXUS_VERSION" ]; then
    echo "  GitNexus already pinned at $GITNEXUS_VERSION."
    return 0
  fi
  echo "  Pinning gitnexus ${cur:-<absent>} -> $GITNEXUS_VERSION (npm install -g)..."
  if npm install -g "gitnexus@$GITNEXUS_VERSION" >/dev/null 2>&1; then
    echo "  GitNexus pinned at $GITNEXUS_VERSION."
  else
    echo "  WARNING: npm install -g gitnexus@$GITNEXUS_VERSION failed."
  fi
  return 0
}

if [ "${1:-}" = "--global" ]; then
  reconcile_global
  exit 0
fi

PROJ="${1:-}"
MODE="${2:-}"

if [ -z "$PROJ" ] || [ ! -d "$PROJ" ]; then
  echo "usage: gitnexus-reconcile.sh <project_dir> [--wire|--check] | gitnexus-reconcile.sh --global" >&2
  exit 2
fi

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
MCP_HELPER="$SCRIPT_DIR/gitnexus-mcp.py"
START_MARKER='<!-- gitnexus:start -->'

# Resolve which file actually holds the managed block. CLAUDE.md wins on a tie
# (matches `npx gitnexus setup`'s default target); AGENTS.md is the fallback
# for projects that relocated their conventions there.
if grep -qF "$START_MARKER" "$PROJ/CLAUDE.md" 2>/dev/null; then
  TARGET_MD="$PROJ/CLAUDE.md"
elif grep -qF "$START_MARKER" "$PROJ/AGENTS.md" 2>/dev/null; then
  TARGET_MD="$PROJ/AGENTS.md"
else
  TARGET_MD="$PROJ/CLAUDE.md"
fi

has_index() { [ -d "$PROJ/.gitnexus" ]; }

has_mcp() {
  if command -v python3 >/dev/null 2>&1 && [ -f "$MCP_HELPER" ]; then
    python3 "$MCP_HELPER" check "$PROJ" >/dev/null 2>&1
    return $?
  fi
  # Fallback without python3: match the server entry shape, not the Bash perms
  grep -q '"gitnexus"[[:space:]]*:[[:space:]]*{' \
    "$PROJ/.mcp.json" "$PROJ/.claude/settings.json" 2>/dev/null
}

has_block() { grep -qF "$START_MARKER" "$TARGET_MD" 2>/dev/null; }

# ── Remove the managed block, collapsing the surrounding blank lines to one ───
strip_block() {
  local tmp="$TARGET_MD.gitnexus.tmp"
  awk '
    { lines[NR] = $0 }
    index($0, "<!-- gitnexus:start -->") && !s { s = NR }
    index($0, "<!-- gitnexus:end -->")   && s && !e { e = NR }
    END {
      if (!s || !e || e < s) { for (i = 1; i <= NR; i++) print lines[i]; exit }
      b = s - 1; while (b >= 1  && lines[b] ~ /^[[:space:]]*$/) b--
      a = e + 1; while (a <= NR && lines[a] ~ /^[[:space:]]*$/) a++
      for (i = 1; i <= b; i++) print lines[i]
      if (b >= 1 && a <= NR) print ""
      for (i = a; i <= NR; i++) print lines[i]
    }
  ' "$TARGET_MD" > "$tmp" && mv "$tmp" "$TARGET_MD"
}

# ── Repair skill paths inside the block ───────────────────────────────────────
# GitNexus writes project-local `.claude/skills/gitnexus/...` paths; the harness
# installs its skills to ~/.claude/skills/. Rewrite is confined to the block and
# is idempotent (already-correct paths are parked behind a sentinel first).
fix_skill_paths() {
  local tmp="$TARGET_MD.gitnexus.tmp"
  awk '
    index($0, "<!-- gitnexus:start -->") { inblock = 1 }
    inblock {
      line = $0
      gsub(/~\/\.claude\/skills\//, "@@GNSKILLS@@", line)
      gsub(/\.claude\/skills\/gitnexus\/gitnexus-/, "@@GNSKILLS@@gitnexus-", line)
      gsub(/\.claude\/skills\/gitnexus-/, "@@GNSKILLS@@gitnexus-", line)
      gsub(/@@GNSKILLS@@/, "~/.claude/skills/", line)
      if (line != $0) changed = 1
      $0 = line
    }
    index($0, "<!-- gitnexus:end -->") { inblock = 0 }
    { print }
    END { exit (changed ? 0 : 1) }
  ' "$TARGET_MD" > "$tmp"
  local rewrote=$?
  if [ $rewrote -eq 0 ]; then
    mv "$tmp" "$TARGET_MD"
    echo "  GitNexus skill paths in $(basename "$TARGET_MD") repointed to ~/.claude/skills/"
  else
    rm -f "$tmp"
  fi
}

# ── Rewrite bare gitnexus_* tool names inside the block ───────────────────────
# GitNexus writes MUST/NEVER rules that call bare `gitnexus_*` names; those do
# not resolve as MCP tools, which are exposed prefixed as `mcp__gitnexus__*`.
# Rewrite is confined to the block and is idempotent: already-prefixed calls
# are parked behind a sentinel first, so a second pass never yields
# mcp__gitnexus__mcp__gitnexus__*. Longer names are matched before the shorter
# names they contain (e.g. gitnexus_api_impact before gitnexus_impact).
fix_tool_names() {
  local tmp="$TARGET_MD.gitnexus.tmp"
  awk '
    index($0, "<!-- gitnexus:start -->") { inblock = 1 }
    inblock {
      line = $0
      gsub(/mcp__gitnexus__/, "@@GNPREFIX@@", line)
      gsub(/gitnexus_detect_changes/, "@@GNPREFIX@@detect_changes", line)
      gsub(/gitnexus_shape_check/,    "@@GNPREFIX@@shape_check",    line)
      gsub(/gitnexus_api_impact/,     "@@GNPREFIX@@api_impact",     line)
      gsub(/gitnexus_list_repos/,     "@@GNPREFIX@@list_repos",     line)
      gsub(/gitnexus_group_list/,     "@@GNPREFIX@@group_list",     line)
      gsub(/gitnexus_group_sync/,     "@@GNPREFIX@@group_sync",     line)
      gsub(/gitnexus_route_map/,      "@@GNPREFIX@@route_map",      line)
      gsub(/gitnexus_tool_map/,       "@@GNPREFIX@@tool_map",       line)
      gsub(/gitnexus_context/,        "@@GNPREFIX@@context",        line)
      gsub(/gitnexus_impact/,         "@@GNPREFIX@@impact",         line)
      gsub(/gitnexus_rename/,         "@@GNPREFIX@@rename",         line)
      gsub(/gitnexus_cypher/,         "@@GNPREFIX@@cypher",         line)
      gsub(/gitnexus_query/,          "@@GNPREFIX@@query",          line)
      gsub(/@@GNPREFIX@@/, "mcp__gitnexus__", line)
      if (line != $0) changed = 1
      $0 = line
    }
    index($0, "<!-- gitnexus:end -->") { inblock = 0 }
    { print }
    END { exit (changed ? 0 : 1) }
  ' "$TARGET_MD" > "$tmp"
  local rewrote=$?
  if [ $rewrote -eq 0 ]; then
    mv "$tmp" "$TARGET_MD"
    echo "  GitNexus tool names in $(basename "$TARGET_MD") repointed to mcp__gitnexus__*"
  else
    rm -f "$tmp"
  fi
}

# ── Replace the block body with the harness's compact rule set ────────────────
# The upstream block is ~900 tokens loaded on every API call: each rule written
# twice (MCP + CLI form), a resources table and a skill-path table the skill
# listing already carries. This keeps every MUST/NEVER rule and states the CLI
# fallback once. The repo name is carried over from the upstream block (it is
# GitNexus's registry name, not always the directory name). Idempotent; set
# SDD_GITNEXUS_FULL_BLOCK=1 to keep the upstream block untouched.
compact_block() {
  [ "${SDD_GITNEXUS_FULL_BLOCK:-0}" = "1" ] && return 0
  # Unterminated block: splicing would swallow the rest of the file.
  grep -qF '<!-- gitnexus:end -->' "$TARGET_MD" || return 0
  local name
  name="$(awk '
    index($0, "<!-- gitnexus:start -->") { inblock = 1 }
    # "GitNexus as **x**" upstream, "Indexed as **x**" once compacted.
    inblock && index($0, "ndexed") && index($0, " as **") {
      rest = substr($0, index($0, " as **") + 6)
      print substr(rest, 1, index(rest, "**") - 1); exit
    }
    index($0, "<!-- gitnexus:end -->") { exit }
  ' "$TARGET_MD")"
  [ -n "$name" ] || name="$(basename "$PROJ")"

  local body="$TARGET_MD.gitnexus.body" tmp="$TARGET_MD.gitnexus.tmp"
  cat > "$body" <<EOF
<!-- gitnexus:start -->
# GitNexus — Code Intelligence

Indexed as **$name**. Tools below are MCP; if MCP is down, use the CLI with kebab-case verbs and flags, e.g. \`node .gitnexus/run.cjs detect-changes --scope all --repo .\`. Stale index: \`node .gitnexus/run.cjs analyze --index-only\`.

- **MUST run \`impact({target, direction: "upstream"})\` before editing any function, class or method**; report callers, processes, risk.
- **MUST run \`detect_changes({scope: "all"})\` before committing.** \`partial: true\` / \`truncated: true\` is not clean — a zero means unseen; re-run. Regression review: \`scope: "compare", base_ref: "<default branch>"\`.
- **HIGH/CRITICAL risk: warn before editing, never proceed silently.** \`riskSharedAxes\` never waives it (MCP File omits axes; Graph-RAG expands File).
- **\`risk: UNKNOWN\` is unresolved, not low.** Empty callers can mean unresolvable callers (dynamic dispatch, property access, cross-language) — confirm with text search before changing or deleting.
- Read-only questions — graph first: \`query({search_query})\` for concepts/flows, \`context({name})\` for a symbol, \`impact\` for blast radius. Text search only for empty/UNKNOWN results or literals.
- **NEVER rename with find-and-replace** — use \`rename\`.
- Security review: \`explain({target})\` lists taint flows (needs \`analyze --pdg\`).
- How-to detail: skills \`gitnexus-exploring\`, \`gitnexus-impact-analysis\`, \`gitnexus-debugging\`, \`gitnexus-refactoring\`, \`gitnexus-guide\`, \`gitnexus-cli\`.
<!-- gitnexus:end -->
EOF
  awk -v body="$body" '
    index($0, "<!-- gitnexus:start -->") && !done {
      while ((getline l < body) > 0) print l
      skip = 1; next
    }
    skip { if (index($0, "<!-- gitnexus:end -->")) { skip = 0; done = 1 }; next }
    { print }
  ' "$TARGET_MD" > "$tmp"
  rm -f "$body"
  if cmp -s "$tmp" "$TARGET_MD"; then
    rm -f "$tmp"
  else
    mv "$tmp" "$TARGET_MD"
    echo "  GitNexus block in $(basename "$TARGET_MD") compacted"
  fi
}

# ── Warn about skills the block references that are not installed ─────────────
warn_missing_skills() {
  local missing=""
  local name
  for name in $(grep -o '~/\.claude/skills/[A-Za-z0-9_-]*' "$TARGET_MD" 2>/dev/null \
                | sed 's|~/\.claude/skills/||' | sort -u); do
    [ -d "$HOME/.claude/skills/$name" ] || missing="$missing $name"
  done
  [ -n "$missing" ] && echo "  WARNING: $(basename "$TARGET_MD") references uninstalled skills:$missing"
  return 0
}

wire_mcp() {
  if ! has_index; then
    echo "  Skipping MCP wiring — no .gitnexus/ index in $PROJ."
    return 0
  fi
  if has_mcp; then
    echo "  GitNexus MCP server already configured."
    return 0
  fi
  if command -v python3 >/dev/null 2>&1 && [ -f "$MCP_HELPER" ]; then
    python3 "$MCP_HELPER" wire "$PROJ" || \
      echo "  WARNING: could not wire GitNexus MCP server — run /kiro:gitnexus-setup."
  else
    echo "  WARNING: python3 not found — add the MCP server manually:"
    echo '    .mcp.json → "mcpServers": { "gitnexus": { "command": "npx", "args": ["-y", "gitnexus", "mcp"] } }'
  fi
  return 0
}

case "$MODE" in
  --check)
    has_index && has_mcp
    exit $?
    ;;

  --wire)
    wire_mcp
    exit 0
    ;;
esac

# ── Default: reconcile CLAUDE.md against real state ───────────────────────────
has_block || exit 0

# An index with no MCP server is recoverable — repair it rather than deleting a
# block the project clearly opted into. Only an unrecoverable state is stripped.
if has_index && ! has_mcp; then
  echo "  GitNexus block present but MCP server missing — wiring it."
  wire_mcp
fi

if ! has_index; then
  strip_block
  echo "  Removed dead GitNexus block from CLAUDE.md (no .gitnexus/ index)."
elif ! has_mcp; then
  strip_block
  echo "  Removed dead GitNexus block from CLAUDE.md (MCP server could not be wired)."
else
  compact_block
  fix_skill_paths
  fix_tool_names
  warn_missing_skills
fi

exit 0
