#!/bin/bash
# Deterministic cross-reference scan for skill-curator.
#
# For every skill in ~/.claude/skills/*/, finds where its name (word-boundary)
# appears in other skills' SKILL.md bodies, hooks, agents, commands, CLAUDE.md,
# kiro rules, and routine scripts — so the curator never proposes deleting/
# merging something that other skills, hooks, or agents still depend on.
#
# Single-pass design: every candidate file is read ONCE and each line tokenized
# once against the full name set, instead of one grep-over-everything PER skill
# (the previous approach — O(skills x files), confirmed to still be running
# past 120s wall-clock on ~1000 skills). That cost was paid inline by the
# skill-curator-runner before it ever invokes claude, eating into the same
# run's time/turn budget that Phase 4 (writing reports/skill-curation-report.md)
# needs — the likely reason the weekly report's main body froze at 2026-08-06
# while later runs kept exiting 0 with real duration but nothing written.
#
# Output is file:line locations only (never the full matched line text) and capped
# to MAX_REFS per skill — a common English-word skill name (e.g. "questions") can
# otherwise match hundreds of lines and produce a single multi-KB entry that buries
# the signal for every other skill in the map.
#
# Usage: bash scripts/utils/skill-dependency-scan.sh   (run from the harness repo root)
# Output: markdown lines, one per skill with >=1 referrer, alphabetical by skill
# name (matches the original glob-iteration order). Silent (no output) if none.

set -u

REPO_DIR="$(pwd)"
SKILLS_DIR="$HOME/.claude/skills"
MAX_REFS=8

[ -d "$SKILLS_DIR" ] || exit 0

TARGETS=()
for d in "$REPO_DIR/hooks" "$REPO_DIR/agents" "$REPO_DIR/commands" \
         "$REPO_DIR/kiro/settings/rules" "$REPO_DIR/scripts/routines"; do
  [ -d "$d" ] && TARGETS+=("$d")
done
[ -f "$REPO_DIR/CLAUDE.md" ] && TARGETS+=("$REPO_DIR/CLAUDE.md")

NAMES_FILE="$(mktemp)"
PAIRS_FILE="$(mktemp)"
trap 'rm -f "$NAMES_FILE" "$PAIRS_FILE"' EXIT

for skill_dir in "$SKILLS_DIR"/*/; do
  basename "${skill_dir%/}"
done > "$NAMES_FILE"

SKILL_MD_LIST=("$SKILLS_DIR"/*/SKILL.md)
TARGET_FILES=()
if [ "${#TARGETS[@]}" -gt 0 ]; then
  while IFS= read -r f; do
    TARGET_FILES+=("$f")
  done < <(find "${TARGETS[@]}" -type f 2>/dev/null)
fi

# One read per file, one token pass per line, one hash lookup per token —
# emits unsorted "name<TAB>hit" pairs; self-references (a skill's own
# SKILL.md matching its own name) are dropped inline.
awk -v skills_dir="$SKILLS_DIR" -v repo_dir="$REPO_DIR" -v names_file="$NAMES_FILE" '
  BEGIN {
    while ((getline nm < names_file) > 0) {
      if (nm != "") is_name[nm] = 1
    }
    close(names_file)
    prefix_len = length(skills_dir) + 2
  }
  {
    is_corpus = (index(FILENAME, skills_dir "/") == 1)
    owner = ""
    relbase = ""
    if (is_corpus) {
      rest = substr(FILENAME, prefix_len)
      split(rest, parts, "/")
      owner = parts[1]
    } else {
      relpath = FILENAME
      if (index(relpath, repo_dir "/") == 1) relpath = substr(relpath, length(repo_dir) + 2)
    }
    n = split($0, toks, /[^-A-Za-z0-9_]+/)
    delete seen
    for (i = 1; i <= n; i++) {
      tok = toks[i]
      if (tok == "" || !(tok in is_name)) continue
      if (tok in seen) continue
      seen[tok] = 1
      if (is_corpus) {
        if (tok == owner) continue
        print tok "\t" owner " skill:" FNR
      } else {
        print tok "\t" relpath ":" FNR
      }
    }
  }
' "${SKILL_MD_LIST[@]}" "${TARGET_FILES[@]}" 2>/dev/null | sort -u > "$PAIRS_FILE"

[ -s "$PAIRS_FILE" ] || exit 0

# PAIRS_FILE is sorted by name then hit string. Group consecutive same-name
# lines into one markdown bullet, capped at MAX_REFS with a "(+N more)" tail.
awk -F'\t' -v max_refs="$MAX_REFS" '
  function flush() {
    if (cur == "") return
    out = ""
    shown = (total < max_refs ? total : max_refs)
    for (j = 1; j <= shown; j++) out = out (j > 1 ? ", " : "") hit_list[j]
    if (total > max_refs) out = out " (+" (total - max_refs) " more)"
    print "- `" cur "` — referenced by: " out
  }
  {
    if ($1 != cur) {
      flush()
      cur = $1
      total = 0
      delete hit_list
    }
    total++
    if (total <= max_refs) hit_list[total] = $2
  }
  END { flush() }
' "$PAIRS_FILE"
