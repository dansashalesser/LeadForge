#!/bin/bash
# Single source of truth for writing ANY file inside a skill's directory —
# SKILL.md itself, a resources/examples/*.md file, eval-verdict.json, etc.
# Every routine/agent that edits a skill (harness-health, skill-augment-agent,
# skill-curator) must go through this instead of Write/Edit directly.
#
# Why this exists: update.sh's sync_dir does `rm -rf` + `cp -r` from
# skills/<name>/ (harness source) over ~/.claude/skills/<name>/ (installed,
# what Claude actually reads) every time the daily-orchestrator's launchd
# job fires (every 4h) — regardless of session activity. A write landed only
# on the installed copy of a harness-shipped skill survives until the next
# tick, then gets silently overwritten back to the stale source. This was
# the 5-run-running "reprovisioning" mystery in the 2026-09-17 curation
# report, and it hit the dashboard's Apply-Approved flow too (2026-09-18):
# 19 skills got their description fix on the installed copy only. Writing
# to source too closes that gap for good; the pre-write backup is the
# rollback path the sync overwrite would otherwise destroy with no trace.
#
# Usage: skill-write.sh <skill-name> <new-content-file> [relative-path]
#   relative-path defaults to SKILL.md; use e.g.
#   resources/examples/2026-09-18-examples.md or eval-verdict.json for
#   sidecar files inside the same skill directory.
# Run from the harness repo root (same cwd harness-health-runner.sh uses).

set -u

SKILL_NAME="${1:-}"
NEW_CONTENT="${2:-}"
RELPATH="${3:-SKILL.md}"

if [ -z "$SKILL_NAME" ] || [ -z "$NEW_CONTENT" ]; then
  echo "usage: skill-write.sh <skill-name> <new-content-file> [relative-path]" >&2
  exit 1
fi
if [ ! -f "$NEW_CONTENT" ]; then
  echo "error: new-content-file not found: $NEW_CONTENT" >&2
  exit 1
fi

SRC_DIR="skills/$SKILL_NAME"
# Installed location depends on tier: library skills (listed in the manifest) install to
# ~/.claude/skill-library/ and are NOT in the per-prompt listing; everything else is in
# ~/.claude/skills/. Writing to the wrong one leaves a stray listed copy and misses the
# copy Claude actually reads. See scripts/setup/sync-skills.sh (single namer of the split).
MANIFEST="scripts/setup/skill-library.txt"
if [ -f "$MANIFEST" ] && grep -qxF "$SKILL_NAME" "$MANIFEST"; then
  INSTALLED_DIR="$HOME/.claude/skill-library/$SKILL_NAME"
else
  INSTALLED_DIR="$HOME/.claude/skills/$SKILL_NAME"
fi
SRC="$SRC_DIR/$RELPATH"
INSTALLED="$INSTALLED_DIR/$RELPATH"
BACKUP_DIR=".claude/memory/skill-repair-backups/$SKILL_NAME"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_NAME="$TIMESTAMP--$(echo "$RELPATH" | tr '/' '_')"

mkdir -p "$BACKUP_DIR"

if [ -d "$SRC_DIR" ]; then
  # Harness-shipped skill: skills/<name>/ is the sync origin. Snapshot the
  # current file before overwrite (the rollback copy — only if it already
  # existed), then write source AND installed so the next scheduled sync
  # reinforces the write instead of erasing it.
  if [ -f "$SRC" ]; then
    cp "$SRC" "$BACKUP_DIR/$BACKUP_NAME"
  fi
  mkdir -p "$(dirname "$SRC")" "$(dirname "$INSTALLED")"
  cp "$NEW_CONTENT" "$SRC"
  cp "$NEW_CONTENT" "$INSTALLED"
  echo "written (harness-shipped): $SRC + $INSTALLED"
  [ -f "$BACKUP_DIR/$BACKUP_NAME" ] && echo "backup: $BACKUP_DIR/$BACKUP_NAME"
elif [ -d "$INSTALLED_DIR" ]; then
  # No harness source (marketplace/manually-installed skill) — nothing will
  # sync over it, so the installed copy alone is the safe write target.
  if [ -f "$INSTALLED" ]; then
    cp "$INSTALLED" "$BACKUP_DIR/$BACKUP_NAME"
  fi
  mkdir -p "$(dirname "$INSTALLED")"
  cp "$NEW_CONTENT" "$INSTALLED"
  echo "written (no harness source): $INSTALLED"
  [ -f "$BACKUP_DIR/$BACKUP_NAME" ] && echo "backup: $BACKUP_DIR/$BACKUP_NAME"
else
  echo "error: no skill directory found at $SRC_DIR or $INSTALLED_DIR" >&2
  exit 1
fi
