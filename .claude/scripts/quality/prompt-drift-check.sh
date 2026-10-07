#!/bin/bash
# Prompt-drift check — insertion:deletion ratio on CLAUDE.md / SKILL.md files.
#
# These files only ever grow in the common case: every session that adds a rule or
# a lesson-learned appends a bullet, almost nobody goes back and deletes a bullet that
# stopped mattering. Pure accretion is a hygiene signal, not a functional bug — advisory
# only, wired into the evolve-agent audit rather than any blocking gate.
#
# Method: for each target file, sum added/deleted line counts across its full git
# history (`git log --numstat`), compute deletions/insertions. A file with enough
# history (>=MIN_COMMITS touching it) and a low delete ratio is flagged accretion-only.
#
# Usage:
#   prompt-drift-check.sh [file ...]     # check specific files
#   prompt-drift-check.sh                # check CLAUDE.md + every skills/*/SKILL.md
#
# Output: one JSON line per file to stdout, plus a summary line. Never fails the
# caller — on any error for a given file, skips it. Read-only; makes no edits.

set -u

MIN_COMMITS="${PROMPT_DRIFT_MIN_COMMITS:-5}"
ACCRETION_THRESHOLD="${PROMPT_DRIFT_ACCRETION_THRESHOLD:-0.1}"

FILES=()
if [ "$#" -gt 0 ]; then
  FILES=("$@")
else
  while IFS= read -r line; do
    [ -n "$line" ] && FILES+=("$line")
  done < <(
    { [ -f CLAUDE.md ] && echo CLAUDE.md; git ls-files 'skills/*/SKILL.md' 2>/dev/null; } \
      | sort -u
  )
fi

FLAGGED=0
CHECKED=0

for f in "${FILES[@]:-}"; do
  [ -z "$f" ] && continue

  stats="$(git log --follow --numstat --pretty=format: -- "$f" 2>/dev/null | grep -vE '^\s*$')"
  [ -z "$stats" ] && continue

  commits="$(git log --follow --oneline -- "$f" 2>/dev/null | wc -l | tr -d ' ')"
  [ "$commits" -lt "$MIN_COMMITS" ] && continue

  # git numstat uses "-" for binary files; ignore those lines.
  read -r added deleted <<< "$(echo "$stats" | awk '
    $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ { a+=$1; d+=$2 }
    END { print a+0, d+0 }
  ')"

  [ "$added" -eq 0 ] && continue

  ratio="$(awk -v a="$added" -v d="$deleted" 'BEGIN { printf "%.3f", d/a }')"
  is_accretion="$(awk -v r="$ratio" -v t="$ACCRETION_THRESHOLD" 'BEGIN { print (r < t) ? 1 : 0 }')"

  CHECKED=$((CHECKED + 1))
  verdict="clean"
  if [ "$is_accretion" = "1" ]; then
    verdict="accretion-only"
    FLAGGED=$((FLAGGED + 1))
  fi

  printf '{"file": "%s", "commits": %d, "added": %d, "deleted": %d, "delete_ratio": %s, "verdict": "%s"}\n' \
    "$f" "$commits" "$added" "$deleted" "$ratio" "$verdict"
done

printf '{"summary": true, "checked": %d, "flagged": %d}\n' "$CHECKED" "$FLAGGED"

# REGISTRATION
# Not a hook — read-only reporting script. Wired into evolve-agent's Step 1d
# (Prompt-Drift Check) as part of the `/kiro:evolve` audit. Run standalone with:
#   bash scripts/quality/prompt-drift-check.sh
