#!/bin/bash
# PR risk-tier hook — PostToolUse(Bash).
# Fires after every Bash call; only acts on a plain (non-force) `git push`
# with an open PR for the current branch — mirrors pr-auto-create-hook.sh's
# trigger. Labels the PR green/yellow/red from .claude/steering/risk-zones.md
# (seeded by risk-zone-reseed-runner.sh) so reviewers see blast radius before
# opening the diff. Best-effort: never fails the caller's tool call.

set -uo pipefail

EVENT=$(cat)
MAP=".claude/steering/risk-zones.md"

[ -f "$MAP" ] || exit 0

CMD=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    print(e.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

[[ -z "$CMD" ]] && exit 0
echo "$CMD" | grep -qE "git push($|[^-])" || exit 0
echo "$CMD" | grep -qE -- "--force|(^|\s)-f(\s|$)" && exit 0

command -v gh >/dev/null 2>&1 || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Opt-in gate: only label PRs from a worktree explicitly enabled via
# `git config --worktree hooks.autopilot.enabled true` (requires
# `git config extensions.worktreeConfig true` once, in the main worktree).
# Ad hoc review/PR worktrees stay disabled by default — see git/post-commit.
[ "$(git config --bool hooks.autopilot.enabled 2>/dev/null)" = "true" ] || exit 0

current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
[ -z "$current_branch" ] && exit 0

pr_number="$(gh pr list --head "$current_branch" --json number --jq '.[0].number' 2>/dev/null)"
[ -z "$pr_number" ] && exit 0

base_branch="$(gh pr view "$pr_number" --json baseRefName --jq '.baseRefName' 2>/dev/null)"
[ -z "$base_branch" ] && exit 0

CHANGED_FILES="$(git diff --name-only "origin/$base_branch...$current_branch" 2>/dev/null)"
[ -z "$CHANGED_FILES" ] && exit 0

TIER="green"
SIGNALS=""
while IFS= read -r f; do
  [ -z "$f" ] && continue
  row="$(grep -F "\`$f\`" "$MAP" 2>/dev/null | head -1)"
  [ -z "$row" ] && continue
  zone="$(echo "$row" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $3); print $3}')"
  case "$zone" in
    red) TIER="red" ;;
    yellow) [ "$TIER" != "red" ] && TIER="yellow" ;;
  esac
  [ -n "$zone" ] && [ "$zone" != "green" ] && SIGNALS="${SIGNALS}${f} (${zone}); "
done <<< "$CHANGED_FILES"

LABEL="risk:${TIER}"
if ! gh pr edit "$pr_number" --add-label "$LABEL" >/dev/null 2>&1; then
  gh label create "$LABEL" --color "$([ "$TIER" = red ] && echo d73a4a || [ "$TIER" = yellow ] && echo fbca04 || echo 0e8a16)" >/dev/null 2>&1
  gh pr edit "$pr_number" --add-label "$LABEL" >/dev/null 2>&1
fi

echo "[PR-RISK-TIER] PR #$pr_number labeled ${LABEL}.${SIGNALS:+ Touches: $SIGNALS}"

exit 0

# REGISTRATION
# {
#   "matcher": "Bash",
#   "hooks": [{"type": "command", "command": "bash .claude/hooks/pr-risk-tier-hook.sh"}]
# }
# Add alongside pr-auto-create-hook.sh under PostToolUse -> matcher "Bash"
# in templates/settings.json.template.
