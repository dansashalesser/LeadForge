#!/bin/bash
# rtk-net-effect-runner.sh — daily rerun/reread signal for the dashboard's RTK layer.
#
# Deterministic (no LLM call). Wraps scripts/utils/rtk-net-effect.py, writes
# .claude/memory/rtk-net-effect.json the dashboard reads to annotate the RTK
# layer's savings number with a global rerun-rate signal instead of reporting
# local savings alone. Self-paces daily via state-file guard so calling it
# from the orchestrator every day is a cheap no-op between runs.
#
# Usage:
#   rtk-net-effect-runner.sh            # current repo (cwd), respecting cadence
#   rtk-net-effect-runner.sh --force    # ignore cadence guard, run now
# Env:
#   SDD_SKIP_RTK_NET_EFFECT=1           — opt out entirely
#   SDD_RTK_NET_EFFECT_DAYS=<days>      — lookback window (default 30)
set -u

[ "${SDD_SKIP_RTK_NET_EFFECT:-0}" = "1" ] && exit 0

REPO="$(pwd)"
FORCE=false
[ "${1:-}" = "--force" ] && FORCE=true

# Only meaningful inside an installed repo
[ -d "$REPO/.claude" ] || exit 0

MIN_GAP_DAYS=1
STATE_FILE="$REPO/.claude/memory/.last-rtk-net-effect-run"
TODAY="$(date +%Y-%m-%d)"

if [ "$FORCE" = false ] && [ -f "$STATE_FILE" ]; then
    last="$(cut -dT -f1 "$STATE_FILE" 2>/dev/null | head -1)"
    [ "$last" = "$TODAY" ] && exit 0
fi

DAYS="${SDD_RTK_NET_EFFECT_DAYS:-30}"
PROJECT_SLUG="$(echo "$REPO" | tr '/' '-')"
SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
HARNESS_UTILS="$SCRIPT_DIR/../utils/rtk-net-effect.py"

[ -f "$HARNESS_UTILS" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

mkdir -p "$REPO/.claude/memory"
OUT_FILE="$REPO/.claude/memory/rtk-net-effect.json"

if python3 "$HARNESS_UTILS" --days "$DAYS" --project="$PROJECT_SLUG" --json > "$OUT_FILE.tmp" 2>/dev/null; then
    mv "$OUT_FILE.tmp" "$OUT_FILE"
    echo "rtk-net-effect: wrote $OUT_FILE"
else
    rm -f "$OUT_FILE.tmp"
    echo "rtk-net-effect: no data in window (days=$DAYS) — leaving prior snapshot in place"
fi

# Record run date for cadence guard regardless of data outcome — a project
# with no transcripts yet must not retry every single day.
date -Iseconds > "$STATE_FILE"
