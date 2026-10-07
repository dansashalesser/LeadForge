#!/bin/bash
# =============================================================================
# check-no-hardcoded-paths.sh — regression guard for dynamic, portable paths.
# =============================================================================
# Fails (exit 1) if any committed harness SCRIPT contains a hardcoded absolute
# path literal. Every path in the harness must be self-located at runtime (see
# scripts/lib/resolve-harness-dir.sh) or computed from that self-location — so
# the harness runs unchanged on any machine, OS, or clone location.
#
# Run manually:        bash scripts/utils/check-no-hardcoded-paths.sh
# Wired into:          /kiro:harness-validate  and  .git/hooks/pre-commit
#
# Scope: *.sh, *.py, *.json and *.template under the harness root, EXCLUDING:
#   - .claude/**           synced copies of this source (would double-report)
#   - docs/**              prose + frozen historical plan/spec records
#   - skills/**            vendored third-party skills — someone else's code and
#                          recorded benchmark output (e.g. /Users/lokesh/... inside
#                          loki-mode result JSON). Not our path plumbing, and 13 of
#                          them drowned the real findings before this exclusion.
#   - scripts/lib/resolve-harness-dir.sh  (the one allowed depth marker)
#   - scripts/lib/harness-pointer.sh      (the one allowed namer of the pointer file
#                                          and its convenience symlink)
#   - scripts/lib/tool-paths.sh           (the one allowed namer of package-manager
#                                          layouts — it exists to ask uv/pipx/brew
#                                          where they install, and holds the XDG
#                                          spec defaults used only when a tool
#                                          cannot be asked)
#   - this script itself   (it names the very patterns it bans)
#
# ...plus GENERATED_SCANNED below, which re-admits the specific gitignored files
# that are generated onto each machine and must still be path-free.
#
# History: this guard originally scanned only *.sh and *.py, and excluded .claude/**
# wholesale. .claude/settings.json is JSON *and* lives under .claude/, so it was
# invisible on both counts — while holding 23 absolute hook paths baked in by a
# {{HARNESS_DIR}} substitution. The guard reported "✓ No hardcoded paths" the entire
# time. Moving the harness silently disabled every hook. Config counts as code.
# =============================================================================
set -u

__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/../lib/resolve-harness-dir.sh"
cd "$HARNESS_DIR" || exit 1

# Banned patterns — machine/user-specific absolutes and the hardcoded harness
# root used in EXECUTABLE context. Each entry: "regex<TAB>human explanation".
#
# Deliberately NOT banned (legitimate, would be false positives):
#   - bare mount prefixes (/mnt/c/, C:\) in path-CONVERSION utilities
#   - the conventional "~/.claude/sdd-harness/..." string in help/usage text
#     (it documents where the harness is meant to live; it does not execute)
#   - an OVERRIDABLE env default, i.e. ${SDD_HARNESS_HOME:-...}: the literal is
#     just a fallback that any non-standard install can override, so it is
#     portable by construction (used by the cross-repo skill-usage-tracker hook)
# The patterns below require a concrete USER segment or the executable $HOME
# assignment, which is exactly the anti-pattern that breaks on other machines.
PATTERNS=(
  '/home/[A-Za-z]	hardcoded Linux home dir w/ user (use self-location or $HOME)'
  '/Users/[A-Za-z]	hardcoded macOS home dir w/ user (use self-location or $HOME)'
  '/c/Users/[A-Za-z]	hardcoded Git Bash user path (use self-location or $HOME)'
  '/mnt/c/Users/[A-Za-z]	hardcoded WSL user path (use self-location or $HOME)'
  'C:\\\\Users\\\\[A-Za-z]	hardcoded Windows user path (use self-location or $HOME)'
  '\$HOME/\.claude/sdd-harness	hardcoded harness root (source lib/resolve-harness-dir.sh instead)'
  'cd "\$\(dirname	logical-cd self-location resolves symlinks/junctions to the wrong path — use "cd -P" and/or source lib/resolve-harness-dir.sh'
  # --- Tool-layout literals ------------------------------------------------
  # A path can be $HOME-relative and still be a guess. `~/.local/share/uv/tools`,
  # `~/.local/bin` and `~/.raindrop/bin` are DEFAULTS, not facts: uv honours
  # UV_TOOL_DIR / UV_TOOL_BIN_DIR / XDG_*, pipx honours PIPX_HOME, raindrop honours
  # RAINDROP_HOME. Naming them works on the machine it was written on and breaks on
  # a clone that set any of those. Ask the tool instead — lib/tool-paths.sh does.
  # /opt/homebrew is worse: it is Apple-Silicon-only, so hardcoding it breaks every
  # Intel Mac (and /usr/local breaks every Apple Silicon one). Ask `brew --prefix`.
  '/opt/homebrew	Apple-Silicon-only Homebrew prefix (use brew_bin_dir / `brew --prefix`)'
  '\$HOME/\.local/(bin|share/uv|pipx)	guessed tool layout (use lib/tool-paths.sh: uv_tool_bin_dir / uv_tool_dir / find_tool)'
  '\$HOME/\.raindrop	guessed raindrop home (use lib/tool-paths.sh: raindrop_bin_dir, honours RAINDROP_HOME)'
  '\$HOME/\.cargo	guessed cargo home (use lib/tool-paths.sh: cargo_bin_dir, honours CARGO_HOME)'
)

# Gitignored files that are generated per-machine and still must not contain a
# machine-specific path. `git ls-files` cannot see these, so name them explicitly.
# Only settings.json — settings.local.json is the user's own file and may legitimately
# hold absolute paths (e.g. permissions additionalDirectories).
GENERATED_SCANNED=(
  '.claude/settings.json'
)

# Collect candidate files: tracked + newly-added (not-yet-committed, not
# gitignored) sources, minus the exclusions.
FILES=()
while IFS= read -r line; do
  FILES+=("$line")
done < <(
  { git ls-files '*.sh' '*.py' '*.json' '*.template' 2>/dev/null
    git ls-files --others --exclude-standard '*.sh' '*.py' '*.json' '*.template' 2>/dev/null
  } | sort -u \
    | grep -Ev '^\.claude/' \
    | grep -Ev '^docs/' \
    | grep -Ev '^skills/' \
    | grep -Ev '^scripts/lib/resolve-harness-dir\.sh$' \
    | grep -Ev '^scripts/lib/harness-pointer\.sh$' \
    | grep -Ev '^scripts/lib/tool-paths\.sh$' \
    | grep -Ev '^scripts/utils/check-no-hardcoded-paths\.sh$'
)

# Fallback when not in a git repo (e.g. tarball): walk the filesystem instead.
if [ "${#FILES[@]}" -eq 0 ]; then
  while IFS= read -r line; do
    FILES+=("$line")
  done < <(
    find . \( -name '*.sh' -o -name '*.py' -o -name '*.json' -o -name '*.template' \) \
      -not -path './.claude/*' \
      -not -path './docs/*' \
      -not -path './skills/*' \
      -not -path './node_modules/*' \
      -not -path './scripts/lib/resolve-harness-dir.sh' \
      -not -path './scripts/lib/harness-pointer.sh' \
      -not -path './scripts/lib/tool-paths.sh' \
      -not -path './scripts/check-no-hardcoded-paths.sh' \
      -not -path './.git/*' | sed 's|^\./||'
  )
fi

for gen in "${GENERATED_SCANNED[@]}"; do
  [ -f "$gen" ] && FILES+=("$gen")
done

violations=0
for entry in "${PATTERNS[@]}"; do
  regex="${entry%%	*}"
  why="${entry#*	}"
  for f in "${FILES[@]}"; do
    [ -f "$f" ] || continue
    # Skip comment-only matches? No — a hardcoded path in a comment still rots.
    # But DO skip overridable env defaults (${RAINDROP_HOME:-$HOME/.raindrop}) —
    # the literal there is the documented fallback of a variable any install can
    # set, so it is portable by construction. Listed explicitly rather than
    # matching `${VAR:-` generally, so a genuine machine path smuggled in as some
    # unrelated variable's default is still caught.
    OVERRIDABLE='SDD_HARNESS_HOME|RAINDROP_HOME|CARGO_HOME|PIPX_HOME|PIPX_BIN_DIR|UV_TOOL_DIR|UV_TOOL_BIN_DIR|XDG_DATA_HOME|XDG_BIN_HOME'
    matches="$(grep -nE "$regex" "$f" 2>/dev/null | grep -Ev "$OVERRIDABLE")" || continue
    if [ -n "$matches" ]; then
      while IFS= read -r line; do
        echo "HARDCODED PATH: $f:$line"
        echo "   reason: $why"
        violations=$((violations + 1))
      done <<< "$matches"
    fi
  done
done

if [ "$violations" -gt 0 ]; then
  echo ""
  echo "✗ $violations hardcoded-path violation(s). Paths must be dynamic:"
  echo "  - source scripts/lib/resolve-harness-dir.sh to get \$HARNESS_DIR"
  echo "  - derive everything else from \$HARNESS_DIR or \$HOME"
  exit 1
fi

echo "✓ No hardcoded paths — all paths are self-located or computed."
