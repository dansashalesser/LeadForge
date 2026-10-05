# shellcheck shell=bash
# =============================================================================
# project-gitignore.sh — keep harness-generated files out of a project's repo.
# =============================================================================
# Everything the harness writes into a project is regenerable output, not
# source: .claude/ and specs/ are rebuilt by install.sh / update.sh, CLAUDE.md
# is templated per project, AGENTS.md is written by `lean-ctx setup`, and
# ERRORS.md is a per-machine failure log. None of it belongs in the project's
# history, so the entries below are appended to the project's .gitignore.
#
# Both install.sh (first-time) and update.sh (every sync) call this, so a
# project installed before an entry was added picks it up on the next update.
#
# Usage:
#   . "$__here/scripts/lib/project-gitignore.sh"
#   ensure_gitignore "$PROJECT_DIR"
# =============================================================================

# The full local-only set. Add here and every installed project inherits it.
SDD_GITIGNORE_ENTRIES=(".claude/" "specs/" "CLAUDE.md" "AGENTS.md" "ERRORS.md")

# Append any missing entries to <project_dir>/.gitignore, idempotently.
# Always returns 0 — callers run under `set -e`, and "nothing to add" is the
# normal case on every re-run, not a failure.
ensure_gitignore() {
  local project_dir="$1"
  local gitignore="$project_dir/.gitignore"
  local entry added=false
  touch "$gitignore"
  for entry in "${SDD_GITIGNORE_ENTRIES[@]}"; do
    # Match the exact line to avoid false positives (e.g. ".claude/" vs ".claudeignore").
    if ! grep -qxF "$entry" "$gitignore" 2>/dev/null; then
      if [ "$added" = false ]; then
        # Separate from prior content with a blank line + header, once.
        [ -s "$gitignore" ] && printf '\n' >> "$gitignore"
        echo "# SDD harness — local-only, never committed" >> "$gitignore"
        added=true
      fi
      echo "$entry" >> "$gitignore"
    fi
  done
  if [ "$added" = true ]; then
    echo "  Added harness entries to .gitignore (${SDD_GITIGNORE_ENTRIES[*]})."
  fi
  return 0
}
