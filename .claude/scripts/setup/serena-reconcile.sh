#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# serena-reconcile.sh [<project_dir>] [--global|--check]
#
# Keeps Serena usable for the rules that depend on it (the Risk Gate's
# `mcp__serena__find_referencing_symbols` for Python symbols, and the mandatory
# `get_diagnostics_for_file` after `.py` edits). Two things broke those rules:
#
#   1. The documented launch was `uvx --from git+https://github.com/oraios/serena`.
#      uvx re-resolves a git source on every start; on a cold cache it rebuilds,
#      which blew Claude Code's 30 s MCP connect timeout, and with no network it
#      cannot start at all. Pinning a PyPI release makes startup a cache hit.
#   2. `.serena/project.yml` is auto-generated on first activation and its
#      language detection can drop Python entirely (caresync-vercel came up as
#      TypeScript-only), so Python symbol lookups silently fail. `.serena/` is
#      gitignored as machine-local state, so the fix cannot be committed — it has
#      to be reconciled per project on every install/update.
#
# Modes:
#   --global  ensure the user-scope `serena` MCP server launches the pinned
#             release with `--open-web-dashboard False` (every agent spawn starts
#             its own Serena; without the flag each opens a browser tab).
#             Registers it if absent, migrates the git+ form or a missing flag.
#             Writes go through `claude mcp`, never by editing ~/.claude.json.
#   --check   quiet; exit 0 when the global server is pinned AND the project's
#             project.yml lists every language the repo tracks
#   (none)    reconcile <project_dir>/.serena/project.yml: create it with the
#             right languages when missing, or append missing ones. Never removes
#             a language, never touches any other key.
#
# The pinned version lives here and nowhere else; SDD_SERENA_VERSION overrides.
# Always exits 0 outside --check — never blocks an install or update.
# ──────────────────────────────────────────────────────────────────────────────
set -u

SERENA_VERSION="${SDD_SERENA_VERSION:-1.7.0}"
SERENA_FROM="serena-agent==$SERENA_VERSION"

ARG1="${1:-}"
ARG2="${2:-}"
if [ "$ARG1" = "--global" ]; then
  MODE="--global"; PROJ=""
else
  PROJ="$ARG1"; MODE="$ARG2"
  if [ -z "$PROJ" ] || [ ! -d "$PROJ" ]; then
    echo "usage: serena-reconcile.sh <project_dir> [--check] | serena-reconcile.sh --global" >&2
    exit 2
  fi
fi

# ── Global: the user-scope MCP registration ───────────────────────────────────
# Prints the registered serena args as one line, "ABSENT", or "UNREADABLE".
current_global_args() {
  python3 - "$HOME/.claude.json" <<'PY' 2>/dev/null || echo "UNREADABLE"
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except FileNotFoundError:
    print("ABSENT"); sys.exit(0)
srv = (d.get("mcpServers") or {}).get("serena")
print(" ".join(srv.get("args") or []) if srv else "ABSENT")
PY
}

desired_global_args() {
  echo "--from $SERENA_FROM serena start-mcp-server --context claude-code --open-web-dashboard False"
}

global_ok() {
  [ "$(current_global_args)" = "$(desired_global_args)" ]
}

reconcile_global() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "  WARNING: python3 not found — cannot check the Serena MCP registration."
    return 0
  fi
  local cur
  cur="$(current_global_args)"
  if [ "$cur" = "$(desired_global_args)" ]; then
    echo "  Serena MCP server already pinned to $SERENA_FROM."
    return 0
  fi
  if [ "$cur" = "UNREADABLE" ]; then
    echo "  WARNING: ~/.claude.json is not valid JSON — leaving the Serena registration alone."
    return 0
  fi
  if ! command -v uvx >/dev/null 2>&1; then
    echo "  WARNING: uvx not found — Serena needs uv. Install uv, then re-run update.sh."
    return 0
  fi
  if ! command -v claude >/dev/null 2>&1; then
    echo "  WARNING: claude CLI not found — register Serena manually:"
    echo "    claude mcp add serena --scope user -- uvx --from $SERENA_FROM serena start-mcp-server --context claude-code --open-web-dashboard False"
    return 0
  fi
  if [ "$cur" != "ABSENT" ]; then
    claude mcp remove serena --scope user >/dev/null 2>&1 || true
  fi
  if claude mcp add serena --scope user -- uvx --from "$SERENA_FROM" serena start-mcp-server --context claude-code --open-web-dashboard False >/dev/null 2>&1; then
    if [ "$cur" = "ABSENT" ]; then
      echo "  Serena MCP server registered (user scope, $SERENA_FROM)."
    else
      echo "  Serena MCP server repinned to $SERENA_FROM (was: $cur)."
    fi
    echo "  Restart Claude Code sessions (or /mcp → reconnect) to pick it up."
  else
    echo "  WARNING: 'claude mcp add serena' failed — register it manually:"
    echo "    claude mcp add serena --scope user -- uvx --from $SERENA_FROM serena start-mcp-server --context claude-code --open-web-dashboard False"
  fi
  return 0
}

# ── Per project: .serena/project.yml languages ────────────────────────────────
# Languages the repo actually tracks, in Serena's language-server ids.
needed_languages() {
  git -C "$PROJ" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
  local files
  files="$(git -C "$PROJ" ls-files 2>/dev/null)"
  echo "$files" | grep -qE '\.py$'                         && echo python
  echo "$files" | grep -qE '\.(ts|tsx|js|jsx|mjs|cjs)$'   && echo typescript
  return 0
}

# Runs the yml helper. Usage: yml_langs <check|fix> <lang>...
# check: exit 0 when every lang is listed; fix: append the missing ones.
# Prints the languages it added (fix) or that are missing (check).
yml_langs() {
  python3 - "$PROJ/.serena/project.yml" "$@" <<'PY'
import sys
path, mode, want = sys.argv[1], sys.argv[2], sys.argv[3:]
lines = open(path).read().split("\n")

def top_key(line):
    """(key, rest) for an unindented `key: rest` line, else None."""
    if line[:1].isspace() or ":" not in line:
        return None
    key, rest = line.split(":", 1)
    return key, rest.strip()

def block_item(line):
    """Value of a `- value` list item, else None."""
    s = line.lstrip()
    if not s.startswith("-") or not s[1:2].isspace():
        return None
    return s[1:].strip() or None

# Current Serena writes `language_servers:`; older releases wrote `languages:`.
# Accept `key:` (block list follows) or `key: [a, b]` (inline); nothing else.
idx = key = rest = None
for i, l in enumerate(lines):
    kv = top_key(l)
    if kv and kv[0] in ("language_servers", "languages") and (
            kv[1] == "" or (kv[1].startswith("[") and kv[1].endswith("]"))):
        idx, (key, rest) = i, kv
        break
if idx is None:
    if any((kv := top_key(l)) and kv[0] == "language" and kv[1] for l in lines):
        print("SCALAR"); sys.exit(3)          # pre-list format: leave to the user
    print("NOKEY"); sys.exit(3)
if rest:                                      # inline form: key: [a, b]
    have = [x.strip().strip("'\"") for x in rest[1:-1].split(",") if x.strip()]
    missing = [w for w in want if w not in have]
    if mode == "fix" and missing:
        lines[idx] = f"{key}: [{', '.join(have + missing)}]"
else:                                         # block form: key:\n- a\n- b
    j = idx + 1
    have = []
    while j < len(lines) and block_item(lines[j]) is not None:
        have.append(block_item(lines[j]).strip("'\""))
        j += 1
    missing = [w for w in want if w not in have]
    if mode == "fix" and missing:
        first = lines[idx + 1] if j > idx + 1 else ""
        indent = first[:len(first) - len(first.lstrip())]
        lines[j:j] = [f"{indent}- {w}" for w in missing]
if mode == "fix" and missing:
    open(path, "w").write("\n".join(lines))
print(" ".join(missing))
sys.exit(0 if not missing or mode == "fix" else 1)
PY
}

project_ok() {
  local langs
  langs="$(needed_languages | tr '\n' ' ')"
  [ -z "${langs// }" ] && return 0
  [ -f "$PROJ/.serena/project.yml" ] || return 1
  command -v python3 >/dev/null 2>&1 || return 1
  # shellcheck disable=SC2086
  yml_langs check $langs >/dev/null 2>&1
}

reconcile_project() {
  local langs yml="$PROJ/.serena/project.yml"
  langs="$(needed_languages | tr '\n' ' ')"
  [ -z "${langs// }" ] && return 0            # nothing Serena can index here

  if [ ! -f "$yml" ]; then
    if ! command -v uvx >/dev/null 2>&1; then
      echo "  WARNING: uvx not found — cannot create .serena/project.yml ($langs)."
      return 0
    fi
    local args=() l
    for l in $langs; do args+=(--language "$l"); done
    if uvx --from "$SERENA_FROM" serena project create "$PROJ" "${args[@]}" >/dev/null 2>&1 && [ -f "$yml" ]; then
      echo "  Serena project config created (languages: ${langs% })."
    else
      echo "  WARNING: 'serena project create' failed — Serena will auto-detect languages on first use and may miss some."
      return 0
    fi
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "  WARNING: python3 not found — cannot check Serena languages in $yml."
    return 0
  fi
  local out rc
  # shellcheck disable=SC2086
  out="$(yml_langs fix $langs)"; rc=$?
  if [ $rc -eq 3 ]; then
    echo "  WARNING: $yml has no language_servers list ($out) — add: ${langs% }"
  elif [ -n "$out" ]; then
    echo "  Serena languages in .serena/project.yml: added $out."
  fi

  # A project-scoped registration overrides the pinned user-scope one.
  if grep -qs 'git+https://github.com/oraios/serena' "$PROJ/.mcp.json"; then
    echo "  WARNING: $PROJ/.mcp.json launches Serena from git — it overrides the pinned user-scope server. Remove that entry."
  fi
  return 0
}

case "$MODE" in
  --global)
    reconcile_global
    exit 0
    ;;
  --check)
    global_ok && project_ok
    exit $?
    ;;
esac

reconcile_project
exit 0
