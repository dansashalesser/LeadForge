#!/bin/bash
# js-quality-gate-hook — PostToolUse soft gate (Write/Edit/MultiEdit) on JS/TS files.
#
# The sibling of ruff-quality-gate-hook.sh, for the other half of the languages this
# harness gets installed into. `ruff check` fires on every `.py` write; nothing fired
# on a `.ts`/`.tsx`/`.js`/`.jsx` write at all, so agent-written TypeScript reached the
# human unlinted in every project the harness ships to.
#
# Prefers `oxlint` (millisecond-scale, and the runner used by dmmulroy/anti-slop's
# low-evidence rule set — see docs/sources/git/README.md), falls back to `eslint`.
# Silently no-ops when neither is installed, so this is safe on machines and repos
# with no JS toolchain at all.
#
# Advisory only — surfaces findings back into context, never blocks. Exits 0 always.
set -uo pipefail

Y="\033[1;33m"; B="\033[1m"; R="\033[0m"

tailwind_design_token_check() {
  # Regex-only, no lint plugin required — gated on the repo actually using
  # Tailwind, so this never fires as noise on non-Tailwind projects. Runs
  # independently of whether oxlint/eslint are installed. Rules are the
  # shadcn-ui/lint baseline (see kiro/settings/rules/deterministic-enforcement.md
  # for the full rationale + eslint-plugin-tailwindcss upgrade path).
  local file="$1"
  local tw_config=""
  for cfg in tailwind.config.js tailwind.config.ts tailwind.config.mjs tailwind.config.cjs; do
    [ -f "$cfg" ] && tw_config="$cfg" && break
  done
  [ -z "$tw_config" ] && return 0

  local findings=""
  if grep -nE 'className=.*#[0-9a-fA-F]{3,6}\b|className=.*\brgb\(' "$file" >/dev/null 2>&1; then
    findings="${findings}  - no-raw-colors: raw hex/rgb color inside a className — use a design-token utility class instead\n"
  fi
  if grep -nE '\b[a-z-]+-\[[^]]+\]' "$file" >/dev/null 2>&1; then
    findings="${findings}  - no-arbitrary-values: Tailwind arbitrary-value syntax (e.g. w-[13px]) — prefer a token from ${tw_config}\n"
  fi
  if grep -nE 'className=\{`[^`]*\$\{' "$file" >/dev/null 2>&1; then
    findings="${findings}  - require-static-classes: className built from a template literal with interpolation — Tailwind's JIT scanner needs full static class names to see it\n"
  fi

  [ -z "$findings" ] && return 0
  echo -e "${B}${Y}⚠  tailwind-design-tokens — $(basename "$file")${R}"
  echo -e "$findings"
  echo -e "${B}Advisory only.${R} Gated on ${tw_config} existing in this repo."
}

EVENT=$(cat)

FILE_PATH=$(printf '%s' "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    if e.get('tool_name', '') not in ('Write', 'Edit', 'MultiEdit'):
        sys.exit(0)
    inp = e.get('tool_input', {}) or {}
    print(inp.get('file_path', inp.get('path', '')))
except Exception:
    pass
" 2>/dev/null || echo "")

[[ -z "$FILE_PATH" ]] && exit 0

case "$FILE_PATH" in
  *.ts|*.tsx|*.mts|*.cts|*.js|*.jsx|*.mjs|*.cjs) ;;
  *) exit 0 ;;
esac

# Type declaration files carry no runtime logic; linting them is noise.
[[ "$FILE_PATH" == *.d.ts ]] && exit 0
[[ ! -f "$FILE_PATH" ]] && exit 0

# Never lint dependencies or build output, even if an agent edits one directly.
case "$FILE_PATH" in
  */node_modules/*|*/dist/*|*/build/*|*/.next/*|*/coverage/*|*/vendor/*) exit 0 ;;
esac

tailwind_design_token_check "$FILE_PATH"

# Best-effort wall-clock guard. eslint on a large project can take seconds; oxlint
# cannot. Absent on stock macOS, in which case we just run unguarded.
GUARD=""
if command -v timeout >/dev/null 2>&1; then GUARD="timeout 20"
elif command -v gtimeout >/dev/null 2>&1; then GUARD="gtimeout 20"
fi

LINTER=""
if command -v oxlint >/dev/null 2>&1; then
  LINTER="oxlint"
  OUTPUT=$($GUARD oxlint "$FILE_PATH" 2>/dev/null || true)
elif command -v eslint >/dev/null 2>&1; then
  LINTER="eslint"
  OUTPUT=$($GUARD eslint "$FILE_PATH" 2>/dev/null || true)
else
  exit 0
fi

# Both linters print a summary line even on a clean file. Treat "no findings" as
# silence rather than dumping a "0 problems" banner into context on every write.
if [[ -z "$OUTPUT" ]] || printf '%s' "$OUTPUT" | grep -qiE 'found 0 warnings and 0 errors|0 problems'; then
  exit 0
fi

echo -e "${B}${Y}⚠  js-quality-gate ($LINTER) — $(basename "$FILE_PATH")${R}"
echo "$OUTPUT"
echo ""
echo -e "${B}Advisory only — this write already happened.${R} Fix findings above if real, or explain why they don't apply."

# anti-slop rules are opt-in per repo and are the ones worth acting on first: a
# finding named below means type evidence was thrown away, not just style drift.
if printf '%s' "$OUTPUT" | grep -qE 'no-chained-type-assertions|no-unknown-(parameters|returns|type-aliases)|no-unsafe-dictionary-type|no-known-value-widening|no-widen-then-assert|require-safety-comment-for-type-assertion|no-runtime-typeof|no-module-mocking'; then
  echo -e "${B}anti-slop rule hit.${R} These reject low-evidence types (casts, \`unknown\` in contracts, widened shapes). Recover the real type — do not silence the rule."
fi

exit 0

# REGISTRATION (settings.json — PostToolUse, matcher "Write|Edit|MultiEdit"):
# {
#   "matcher": "Write|Edit|MultiEdit",
#   "hooks": [
#     { "type": "command", "command": "bash \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/js-quality-gate-hook.sh\"" }
#   ]
# }
#
# Linters: oxlint (preferred) -> eslint (fallback) -> silent no-op if neither exists.
# Rules:   install dmmulroy/anti-slop into a repo with
#          `npx skills add dmmulroy/anti-slop --skill install-anti-slop` — the rules
#          are vendored per repo by design; this hook is only the enforcement point.
# Tailwind: tailwind_design_token_check() runs independently of oxlint/eslint,
#           gated on a tailwind.config.* file existing in the repo root.
# Tests:   bash hooks/claude/js-quality-gate-hook.test.sh
