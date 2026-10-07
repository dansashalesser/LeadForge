#!/bin/bash
# PII scanner using OpenAI Privacy Filter (OPF)
# Usage:
#   scan-pii.sh --staged          # scan git-staged files
#   scan-pii.sh <path>            # scan a file or directory
#   scan-pii.sh .                 # scan current directory
#
# Exits 1 if high-severity PII (secret, account_number) is found.

set -euo pipefail

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BOLD='\033[1m'
RESET='\033[0m'

# --- Check OPF is installed ---
if ! command -v opf &>/dev/null; then
  echo -e "${RED}OPF not installed.${RESET} Run: uv tool install --python 3.13 git+https://github.com/openai/privacy-filter.git"
  exit 2
fi

# --- Resolve target files ---
TARGET="${1:-}"
FILES=()

if [[ "$TARGET" == "--staged" ]]; then
  # Scan only staged (cached) git files
  while IFS= read -r f; do
    [[ -f "$f" ]] && FILES+=("$f")
  done < <(git diff --cached --name-only 2>/dev/null || true)

  if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "No staged files to scan."
    exit 0
  fi
elif [[ -d "${TARGET:-.}" ]]; then
  # Scan text-like files in directory (skip binaries and node_modules)
  while IFS= read -r f; do
    FILES+=("$f")
  done < <(find "${TARGET:-.}" \
    -type f \
    \( -name "*.md" -o -name "*.txt" -o -name "*.json" -o -name "*.yaml" \
       -o -name "*.yml" -o -name "*.env" -o -name "*.log" -o -name "*.py" \
       -o -name "*.js" -o -name "*.ts" -o -name "*.sh" -o -name "*.toml" \) \
    ! -path "*/node_modules/*" \
    ! -path "*/.git/*" \
    ! -path "*/__pycache__/*" \
    2>/dev/null)

  if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "No scannable files found in ${TARGET:-.}"
    exit 0
  fi
elif [[ -f "$TARGET" ]]; then
  FILES=("$TARGET")
else
  echo "Usage: scan-pii.sh [--staged | <file-or-directory>]"
  exit 1
fi

echo -e "${BOLD}OPF Privacy Scan — ${#FILES[@]} file(s)${RESET}"
echo ""

TOTAL_SPANS=0
HIGH_SEVERITY=0
FINDINGS=""

for FILE in "${FILES[@]}"; do
  # Run OPF on this file, capture JSON output
  RESULT=$(opf -f "$FILE" --format json --no-print-color-coded-text 2>/dev/null || true)

  if [[ -z "$RESULT" ]]; then
    continue
  fi

  # Extract span count and category summary using python3 (stdlib only)
  read -r SPAN_COUNT CATEGORIES HIGH_COUNT < <(python3 - "$RESULT" <<'PYEOF'
import json, sys

try:
    data = json.loads(sys.argv[1])
    spans = data.get("detected_spans", [])
    summary = data.get("summary", {}).get("labels", {})
    high = sum(v for k, v in summary.items() if k in ("secret", "account_number"))
    cats = ", ".join(f"{k}:{v}" for k, v in summary.items()) if summary else ""
    print(len(spans), cats, high)
except Exception:
    print(0, "", 0)
PYEOF
)

  if [[ "$SPAN_COUNT" -gt 0 ]]; then
    TOTAL_SPANS=$((TOTAL_SPANS + SPAN_COUNT))
    HIGH_SEVERITY=$((HIGH_SEVERITY + HIGH_COUNT))
    if [[ "$HIGH_COUNT" -gt 0 ]]; then
      FINDINGS+="${RED}  ✗ ${FILE}${RESET} — ${SPAN_COUNT} span(s): ${CATEGORIES}\n"
    else
      FINDINGS+="${YELLOW}  ⚠ ${FILE}${RESET} — ${SPAN_COUNT} span(s): ${CATEGORIES}\n"
    fi
  fi
done

# --- Summary ---
if [[ "$TOTAL_SPANS" -eq 0 ]]; then
  echo -e "${GREEN}✓ No PII detected.${RESET}"
  exit 0
fi

echo -e "$FINDINGS"
echo -e "${BOLD}Summary:${RESET} ${TOTAL_SPANS} total span(s) across ${#FILES[@]} file(s)"

if [[ "$HIGH_SEVERITY" -gt 0 ]]; then
  echo -e "${RED}${BOLD}✗ ${HIGH_SEVERITY} high-severity finding(s) (secret / account_number) — commit blocked.${RESET}"
  echo "  Redact with: opf -f <file> --output-mode redacted > <file>_clean"
  exit 1
else
  echo -e "${YELLOW}⚠ Low-severity PII only (names, emails, phones). Review before sharing externally.${RESET}"
  exit 0
fi
