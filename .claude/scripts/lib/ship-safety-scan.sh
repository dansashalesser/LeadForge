#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# ship-safety-scan.sh — pre-ship gate for the SDD harness.
#
# Run by install.sh / update.sh BEFORE any harness source is copied into a
# target repo. The harness ships to every registered repo and machine, so a
# leaked secret or an over-broad permission rule would propagate silently.
# This is the step-14 "scan before you ship" gate.
#
# Two classes of finding:
#   1. Leaked secrets in shipping files   -> HARD BLOCK (exit 1)
#   2. Over-broad permission rules        -> SOFT WARN  (exit 0, or 1 with --strict)
#
# Usage:
#   ship-safety-scan.sh [HARNESS_DIR] [--strict]
#     HARNESS_DIR   root to scan. Defaults to $HARNESS_DIR env, else this file's
#                   grandparent (scripts/lib/.. -> harness root).
#     --strict      treat over-broad permission warnings as hard failures too.
#
# Exit codes: 0 = clean (warnings allowed), 1 = blocking violation, 2 = usage error.
# ──────────────────────────────────────────────────────────────────────────────
set -u

# --- Resolve scan root ---------------------------------------------------------
STRICT=0
ROOT=""
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    -*)       echo "ship-safety-scan: unknown flag: $arg" >&2; exit 2 ;;
    *)        ROOT="$arg" ;;
  esac
done

if [ -z "${ROOT}" ]; then
  ROOT="${HARNESS_DIR:-}"
fi
if [ -z "${ROOT}" ]; then
  __here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
  ROOT="$(cd -P "$__here/.." && pwd)"   # scripts/lib -> scripts -> harness root is one more up
  ROOT="$(cd -P "$ROOT/.." && pwd)"
fi
if [ ! -d "$ROOT" ]; then
  echo "ship-safety-scan: scan root not found: $ROOT" >&2
  exit 2
fi

# Directories never worth scanning (noise / VCS / generated).
PRUNE='-path */.git -o -path */node_modules -o -path */__pycache__ -o -path */.venv -o -path */.venv-tools -o -path */venv -o -path */memory/glacier -o -path */.dashboard/data'

# Collect shipping text files (skip binaries by extension heuristic + grep -I).
# bash 3.2 (macOS default) lacks `mapfile`, so read null-delimited into the array.
FILES=()
while IFS= read -r -d '' f; do
  FILES+=("$f")
done < <(find "$ROOT" \( $PRUNE \) -prune -o -type f -print0 2>/dev/null)

blocking=0
warnings=0

note_block() { blocking=$((blocking + 1)); echo "  ✗ BLOCK: $1"; }
note_warn()  { warnings=$((warnings + 1));  echo "  ⚠ WARN:  $1"; }

# ── Check 1: Leaked secrets (HARD BLOCK) ──────────────────────────────────────
# High-signal patterns only — structured key shapes + private-key headers.
# Prose-friendly: these effectively never appear in normal markdown.
SECRET_RE='-----BEGIN ([A-Z ]+)?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{50,}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}'

echo "Ship-safety scan: $ROOT"
echo "── secrets ──"
for f in "${FILES[@]}"; do
  # grep -I skips binary; -E extended; -q quiet first-hit
  if grep -IEq "$SECRET_RE" "$f" 2>/dev/null; then
    hit="$(grep -IEn "$SECRET_RE" "$f" 2>/dev/null | head -1 | cut -c1-100)"
    note_block "${f#$ROOT/}: $hit"
  fi
done

# A .env file (not .example/.template) with a populated value must never ship.
for f in "${FILES[@]}"; do
  base="$(basename "$f")"
  case "$base" in
    .env|.env.*)
      case "$base" in *.example|*.template|*.sample) continue ;; esac
      if grep -IEq '^[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]' "$f" 2>/dev/null; then
        note_block "${f#$ROOT/}: shipping a populated .env file"
      fi ;;
  esac
done
[ "$blocking" -eq 0 ] && echo "  ✓ no secrets detected"

# ── Check 2: Over-broad permissions (SOFT WARN) ───────────────────────────────
# Inspect settings templates for wildcard-all allows and missing danger denies.
echo "── permissions ──"
SETTINGS=()
while IFS= read -r -d '' f; do
  SETTINGS+=("$f")
done < <(find "$ROOT/templates" -type f -name '*settings*.json*' -print0 2>/dev/null)

# Standard danger denies a shipped template should carry.
EXPECTED_DENIES=('Bash(rm -rf*)' 'Bash(git push*)' 'Edit(.env*)' 'Edit(secrets/*)')

for f in "${SETTINGS[@]}"; do
  rel="${f#$ROOT/}"
  # Only inspect files that actually declare a permissions block.
  grep -IEq '"(permissions|allow|deny)"' "$f" 2>/dev/null || continue
  # Over-broad allow rules (wildcard-all on a mutating tool).
  if grep -IEq '"(Bash|Edit|Write)\(\*+\)"|"(Edit|Write)\(\*\*\)"' "$f" 2>/dev/null; then
    bad="$(grep -IEn '"(Bash|Edit|Write)\(\*+\)"|"(Edit|Write)\(\*\*\)"' "$f" 2>/dev/null | head -3 | tr '\n' ';')"
    note_warn "$rel: over-broad allow rule(s): $bad"
  fi
  # Empty deny list.
  if grep -IEq '"deny"[[:space:]]*:[[:space:]]*\[[[:space:]]*\]' "$f" 2>/dev/null; then
    note_warn "$rel: empty \"deny\": [] — no danger denies shipped"
  else
    # Has a deny block but may be missing standard denies.
    for d in "${EXPECTED_DENIES[@]}"; do
      if ! grep -Fq "$d" "$f" 2>/dev/null; then
        note_warn "$rel: missing recommended deny rule: $d"
      fi
    done
  fi
done
[ "${#SETTINGS[@]}" -eq 0 ] && echo "  (no settings templates found to check)"
[ "$warnings" -eq 0 ] && echo "  ✓ permissions look scoped"

# ── Verdict ───────────────────────────────────────────────────────────────────
echo "── result ──"
echo "  blocking=$blocking  warnings=$warnings  strict=$STRICT"
if [ "$blocking" -gt 0 ]; then
  echo "  ✗ SHIP BLOCKED: resolve the secret finding(s) above before propagating the harness." >&2
  exit 1
fi
if [ "$warnings" -gt 0 ] && [ "$STRICT" -eq 1 ]; then
  echo "  ✗ SHIP BLOCKED (--strict): resolve the permission warning(s) above." >&2
  exit 1
fi
if [ "$warnings" -gt 0 ]; then
  echo "  ⚠ shipping with $warnings permission warning(s) — review when convenient."
fi
echo "  ✓ ship-safety scan passed."
exit 0
