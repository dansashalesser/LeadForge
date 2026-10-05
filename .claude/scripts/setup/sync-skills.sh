#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# sync-skills.sh <harness_dir>
#
# Installs harness skills into the user's ~/.claude, split into two tiers:
#   ~/.claude/skills/         — LISTED in every prompt (harness-called skills + the
#                               14 domain-router "master" skills). Keep this small.
#   ~/.claude/skill-library/  — reached on demand through a master skill; NOT listed,
#                               so it costs no per-prompt context.
#
# The split is driven by scripts/setup/skill-library.txt (one skill name per line;
# '#'/blank lines ignored). A skill named there installs to skill-library/ and is
# removed from skills/ if a stale copy is there; every other skill installs to skills/.
#
# Single namer for the skill tiers: install.sh and update.sh both call this, so the
# routing lives in exactly one place. Always exits 0 for a missing manifest (installs
# everything to skills/, the pre-split behaviour) — never blocks an install/update.
# ──────────────────────────────────────────────────────────────────────────────
set -u

HARNESS_DIR="${1:-}"
if [ -z "$HARNESS_DIR" ] || [ ! -d "$HARNESS_DIR/skills" ]; then
  echo "  sync-skills: no skills/ under '$HARNESS_DIR' — nothing to do." >&2
  exit 0
fi

MANIFEST="$HARNESS_DIR/scripts/setup/skill-library.txt"
SKILLS_DST="$HOME/.claude/skills"
LIBRARY_DST="$HOME/.claude/skill-library"
mkdir -p "$SKILLS_DST" "$LIBRARY_DST"

# Membership test against the manifest. No associative arrays: macOS ships bash 3.2.
# No manifest → always false, so every skill routes to skills/ exactly as before the
# split was introduced. Exact whole-line match (-x -F); comment/blank lines never match.
is_library() {
  [ -f "$MANIFEST" ] || return 1
  grep -qxF "$1" "$MANIFEST"
}

# rm-then-copy (matches sync_dir): avoids BSD cp content-dump and GNU double-nesting.
sync_one() {
  local src="${1%/}" dst_parent="$2"
  rm -rf "$dst_parent/$(basename "$src")"
  cp -r "$src" "$dst_parent/"
}

listed=0
libraried=0
for skill_dir in "$HARNESS_DIR/skills"/*/; do
  [ -d "$skill_dir" ] || continue
  name="$(basename "${skill_dir%/}")"
  if is_library "$name"; then
    sync_one "${skill_dir%/}" "$LIBRARY_DST"
    # Evict any stale listed copy so it stops costing per-prompt context.
    rm -rf "${SKILLS_DST:?}/$name"
    libraried=$((libraried + 1))
  else
    sync_one "${skill_dir%/}" "$SKILLS_DST"
    listed=$((listed + 1))
  fi
done

echo "  Skills synced: $listed listed (~/.claude/skills/), $libraried in library (~/.claude/skill-library/)."
exit 0
