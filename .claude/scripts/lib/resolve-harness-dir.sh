# shellcheck shell=bash
# =============================================================================
# resolve-harness-dir.sh — source this to set $HARNESS_DIR to the harness root.
# =============================================================================
# Pure self-location: the harness root is derived from THIS file's own position
# on disk, never from a hardcoded path. Works regardless of:
#   - which machine / OS / user (macOS, Linux, WSL, Git Bash)
#   - where the harness was cloned (~/.claude/sdd-harness, ~/Documents/..., etc.)
#   - the caller's current working directory
#   - the harness root being reached through a symlink (e.g.
#     ~/.claude/sdd-harness -> ~/GitHub/sdd-harness)
#   - this file itself being a symlink
#
# Usage (from any script under scripts/):
#   __here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
#   . "$__here/lib/resolve-harness-dir.sh"
#   # $HARNESS_DIR is now set to the real, absolute harness root.
#
# This file lives at <root>/scripts/lib/, so the root is two levels up. If you
# ever move it, update the "/../.." below — it is the ONE place that encodes the
# helper's depth, and nothing else in the harness hardcodes a path.
# =============================================================================

__sdd_resolve_harness_dir() {
  local src="${BASH_SOURCE[0]}" dir
  # Follow a chain of symlinks to the real file (handles this file being a link).
  while [ -h "$src" ]; do
    dir="$(cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd)"
    src="$(readlink "$src")"
    case "$src" in
      /*) ;;                 # absolute link target — use as-is
      *) src="$dir/$src" ;;  # relative link target — resolve against link dir
    esac
  done
  # `cd -P` resolves any symlinked DIRECTORY components in the path too, so a
  # symlinked harness root collapses to its real physical location here.
  cd -P "$(dirname "$src")/../.." >/dev/null 2>&1 && pwd
}

HARNESS_DIR="$(__sdd_resolve_harness_dir)"
unset -f __sdd_resolve_harness_dir
export HARNESS_DIR

if [ -z "${HARNESS_DIR:-}" ] || [ ! -d "$HARNESS_DIR" ]; then
  echo "resolve-harness-dir.sh: failed to locate harness root from ${BASH_SOURCE[0]}" >&2
  return 1 2>/dev/null || exit 1
fi
