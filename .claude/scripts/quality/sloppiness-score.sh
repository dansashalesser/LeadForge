#!/bin/bash
# Sloppiness score — Verbosity + Erosion metrics, no LLM judge.
#
# Verbosity: ratio of duplicated non-blank/non-comment lines to total lines
# (clone-detection proxy — exact-line dedup, not AST-aware). Erosion: loc-weighted
# fraction of lines living in "high branch-density" files (proxy for cyclomatic
# mass in complex functions — file-level, not function-level, since this is
# dependency-free bash, not a real AST tool).
#
# Published baselines (earendil.com "Measuring code sloppiness"):
#   human      verbosity ~0.15  erosion ~0.31
#   ai-agent   verbosity ~0.33  erosion ~0.68
#
# Usage:
#   sloppiness-score.sh [file ...]     # score specific files
#   sloppiness-score.sh                # score `git diff --name-only HEAD`
#
# Output: one JSON line to stdout. Never fails the caller — on any error,
# prints {"error": "..."} and exits 0. Warn-only by design; no hard gate.

set -u

EXT_RE='\.(py|js|jsx|ts|tsx|go|rs|java|rb|sh|bash|c|cc|cpp|h|hpp)$'
BRANCH_RE='\b(if|elif|else if|for|while|case|catch|except)\b|&&|\|\|'
HIGH_DENSITY_THRESHOLD="${SLOPPINESS_DENSITY_THRESHOLD:-0.12}"

FILES=()
if [ "$#" -gt 0 ]; then
  FILES=("$@")
else
  while IFS= read -r line; do
    [ -n "$line" ] && FILES+=("$line")
  done < <(git diff --name-only HEAD 2>/dev/null)
fi

TOTAL_LOC=0
TOTAL_DUP=0
HIGH_DENSITY_LOC=0
SCANNED=0

for f in "${FILES[@]:-}"; do
  [ -z "$f" ] && continue
  [ -f "$f" ] || continue
  echo "$f" | grep -qE "$EXT_RE" || continue

  # Strip blank lines and full-line comments (#, //), normalize whitespace.
  norm="$(grep -vE '^\s*$|^\s*(#|//)' "$f" 2>/dev/null | sed 's/^[ \t]*//;s/[ \t]*$//')"
  [ -z "$norm" ] && continue

  loc="$(echo "$norm" | wc -l | tr -d ' ')"
  [ "$loc" -eq 0 ] && continue

  dup="$(echo "$norm" | sort | uniq -d | while IFS= read -r line; do
    echo "$norm" | grep -Fxc -- "$line"
  done | awk '{s+=$1} END {print s+0}')"

  branch_count="$(grep -cE "$BRANCH_RE" <<< "$norm" 2>/dev/null)"
  [ -z "$branch_count" ] && branch_count=0
  density="$(awk -v b="$branch_count" -v l="$loc" 'BEGIN { printf "%.4f", (l > 0) ? b/l : 0 }')"

  TOTAL_LOC=$((TOTAL_LOC + loc))
  TOTAL_DUP=$((TOTAL_DUP + dup))
  SCANNED=$((SCANNED + 1))

  is_high="$(awk -v d="$density" -v t="$HIGH_DENSITY_THRESHOLD" 'BEGIN { print (d >= t) ? 1 : 0 }')"
  [ "$is_high" = "1" ] && HIGH_DENSITY_LOC=$((HIGH_DENSITY_LOC + loc))
done

if [ "$SCANNED" -eq 0 ] || [ "$TOTAL_LOC" -eq 0 ]; then
  echo '{"scanned": 0, "verbosity": null, "erosion": null, "verdict": "no-scoreable-files"}'
  exit 0
fi

VERBOSITY="$(awk -v d="$TOTAL_DUP" -v l="$TOTAL_LOC" 'BEGIN { printf "%.3f", d/l }')"
EROSION="$(awk -v h="$HIGH_DENSITY_LOC" -v l="$TOTAL_LOC" 'BEGIN { printf "%.3f", h/l }')"

VERDICT="$(awk -v v="$VERBOSITY" -v e="$EROSION" 'BEGIN {
  if (v >= 0.33 || e >= 0.68) { print "high-slop"; exit }
  if (v >= 0.15 || e >= 0.31) { print "elevated"; exit }
  print "clean"
}')"

printf '{"scanned": %d, "loc": %d, "verbosity": %s, "erosion": %s, "verdict": "%s", "baseline_human": {"verbosity": 0.15, "erosion": 0.31}, "baseline_ai_agent": {"verbosity": 0.33, "erosion": 0.68}}\n' \
  "$SCANNED" "$TOTAL_LOC" "$VERBOSITY" "$EROSION" "$VERDICT"
