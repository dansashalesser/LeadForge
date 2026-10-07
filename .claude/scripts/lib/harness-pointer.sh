# shellcheck shell=bash
# =============================================================================
# harness-pointer.sh — write and read THE single stored pointer to the harness.
# =============================================================================
# Two kinds of code need to find the harness root, and only one of them can
# self-locate:
#
#   in-tree scripts   — live under the harness, derive $HARNESS_DIR from their own
#                       position on disk via scripts/lib/resolve-harness-dir.sh.
#                       Nothing stored, nothing to go stale.
#   cross-repo hooks  — run with cwd set to an *installed project*, so self-location
#                       resolves to that project, not the harness. They need a pointer.
#
# $HOME/.sdd-harness-root is that pointer, and it is the only file on the machine
# that records where the harness lives. A plain file rather than a symlink because
# Windows and Git Bash require elevated privileges to create symlinks.
#
# $HOME/.claude/sdd-harness is kept as a convenience symlink for humans and for the
# documented `~/.claude/sdd-harness/install.sh` invocations. It is DERIVED from the
# pointer, refreshed alongside it, and never read as an independent source.
#
# Usage:
#   . "$__here/scripts/lib/harness-pointer.sh"
#   write_harness_pointer "$HARNESS_DIR"     # install.sh / update.sh only
#   root="$(read_harness_pointer)"           # hooks; empty if unset or stale
# =============================================================================

POINTER_FILE="${SDD_POINTER_FILE:-$HOME/.sdd-harness-root}"

# Refresh the convenience symlink to match the pointer. Best-effort: a filesystem
# that cannot make symlinks is not a failure, the pointer file still works.
sync_harness_symlink() {
  local target="$1" link="$HOME/.claude/sdd-harness"
  [ -d "$target" ] || return 0
  mkdir -p "$HOME/.claude" 2>/dev/null || return 0

  # Already correct — nothing to do.
  [ "$(readlink "$link" 2>/dev/null)" = "$target" ] && return 0

  # Refuse to clobber a real directory: that means someone cloned the harness to
  # ~/.claude/sdd-harness directly, which is a supported layout.
  if [ -e "$link" ] && [ ! -h "$link" ]; then
    return 0
  fi

  rm -f "$link" 2>/dev/null
  ln -s "$target" "$link" 2>/dev/null || return 0
}

# Install the harness-repo-only pre-commit guard (hooks/git/pre-commit), which runs
# check-no-hardcoded-paths.sh and blocks a commit that would bake a machine-specific
# path into harness source. It is deliberately NOT propagated to downstream projects —
# a user's own project may legitimately reference absolute paths.
#
# This used to be a manual `cp` documented in the hook's own header, against a path
# (git-hooks/) that does not exist. Nobody ran it, so the guard was never installed
# anywhere while both the hook header and the guard header advertised it as wired up.
install_harness_pre_commit() {
  local root="$1" src="$1/hooks/git/pre-commit" dst="$1/.git/hooks/pre-commit"
  [ -f "$src" ] || return 0
  [ -d "$root/.git/hooks" ] || return 0

  # Do not clobber someone else's pre-commit. README documents appending a scan-pii
  # line to this file, and other tooling may own it too. Only write when the slot is
  # empty or already holds this guard.
  if [ -f "$dst" ] && ! grep -q 'check-no-hardcoded-paths' "$dst" 2>/dev/null; then
    echo "  NOTE: $dst exists and is not the harness guard — left alone." >&2
    echo "        To enable it, add: bash scripts/utils/check-no-hardcoded-paths.sh" >&2
    return 0
  fi

  cp "$src" "$dst" 2>/dev/null || return 0
  chmod +x "$dst" 2>/dev/null || true
}

write_harness_pointer() {
  local target="$1"
  [ -n "$target" ] || return 1
  printf '%s\n' "$target" > "$POINTER_FILE"
  sync_harness_symlink "$target"
}

# Echo the harness root, or nothing if the pointer is missing or points somewhere
# that no longer exists. Callers decide how loudly to complain — see
# hooks/claude/stop-hook.sh for the warn-then-continue pattern. Returns 1 when the
# pointer is stale so callers can distinguish "never installed" from "moved".
read_harness_pointer() {
  local root
  root="$(cat "$POINTER_FILE" 2>/dev/null || true)"
  [ -n "$root" ] || return 1
  if [ ! -d "$root" ]; then
    return 1
  fi
  printf '%s\n' "$root"
}
