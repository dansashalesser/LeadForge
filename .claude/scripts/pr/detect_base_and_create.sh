#!/bin/bash
# Shared logic for pr-auto-create-hook.sh and pr-mention-nudge.sh.
# Detects the branch a feature branch actually forked from (no hardcoded
# main/dev/master list — works against "any base branch" per design), then
# creates a PR against it if one doesn't already exist. Idempotent: safe to
# call on every push / every PR-mention, re-running is a no-op once a PR exists.
#
# Usage: detect_base_and_create.sh
# Requires: git, gh (GitHub CLI, authenticated)
# Exit 0 always — best-effort automation must never fail the caller's tool call.

set -u

command -v gh >/dev/null 2>&1 || { echo "gh CLI not found — skipping PR automation." >&2; exit 0; }

# Opt-in gate: only create/submit PRs from a worktree explicitly enabled via
# `git config --worktree hooks.autopilot.enabled true` (requires
# `git config extensions.worktreeConfig true` once, in the main worktree).
# Ad hoc review/PR worktrees stay disabled by default — see git/post-commit.
[ "$(git config --bool hooks.autopilot.enabled 2>/dev/null)" = "true" ] || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
[ -z "$current_branch" ] || [ "$current_branch" = "HEAD" ] && exit 0

# If a gh-stack is already active for this branch (initiated by smart_commit.sh
# per the stacking-pull-requests skill), submit the whole stack instead of
# bundling everything into one PR. Covers the case of a manual `git push` that
# bypassed smart_commit.sh's own submit call.
if [ -f ".git/gh-stack" ]; then
  stack_out="$(gh stack submit --auto 2>&1)"
  if [ $? -eq 0 ]; then
    echo "[STACK-SUBMITTED] gh-stack layers submitted for $current_branch."
  else
    echo "WARN: gh stack submit failed: $stack_out — see ~/.claude/skill-library/stacking-pull-requests/SKILL.md (likely a sync conflict)." >&2
  fi
  exit 0
fi

default_branch="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"

best_ref=""
best_date=0
while IFS= read -r ref; do
  ref="${ref#origin/}"
  [ "$ref" = "$current_branch" ] && continue
  [ "$ref" = "HEAD" ] && continue
  mb="$(git merge-base "$current_branch" "refs/remotes/origin/$ref" 2>/dev/null || git merge-base "$current_branch" "$ref" 2>/dev/null)"
  [ -z "$mb" ] && continue
  mb_date="$(git show -s --format=%ct "$mb" 2>/dev/null || echo 0)"
  if [ "$mb_date" -gt "$best_date" ]; then
    best_date="$mb_date"
    best_ref="$ref"
  fi
done < <(git for-each-ref --format='%(refname:short)' refs/heads refs/remotes/origin | sort -u)

base_branch="${best_ref:-$default_branch}"
[ -z "$base_branch" ] && exit 0
[ "$base_branch" = "$current_branch" ] && exit 0

# Idempotency: don't recreate a PR that already exists for this branch.
existing="$(gh pr list --head "$current_branch" --json number --jq '.[0].number' 2>/dev/null)"
if [ -n "$existing" ]; then
  echo "[PR-AUTO-CREATED] PR #$existing already open against its base — babysitting continues."
  exit 0
fi

pr_url="$(gh pr create --base "$base_branch" --fill --draft 2>&1)"
if [ $? -ne 0 ]; then
  echo "WARN: gh pr create failed: $pr_url" >&2
  exit 0
fi

# Ask gh for the number instead of parsing it out of the printed URL. The URL is
# free text; the number is a field. Same principle as the repo-wide regex ban.
pr_number="$(gh pr list --head "$current_branch" --json number --jq '.[0].number' 2>/dev/null)"
if [ -z "$pr_number" ]; then
  echo "[PR-AUTO-CREATED] PR opened against $base_branch, but its number could not be read back: $pr_url" >&2
  exit 0
fi
echo "[PR-AUTO-CREATED] PR #$pr_number opened against $base_branch (auto-detected base)."

# Evidence section. This path runs headless on `git push`, where no before/after
# probe was run and none can be invented — so the honest thing is to record that
# fact in the body, not to leave the section out and not to fabricate one.
#
# The section is written here because pr-evidence-hook.sh cannot see this path:
# the `gh pr create` above runs inside this script, not as a Bash tool call, so
# no PreToolUse event fires. Writing the marker also means that when an agent
# later fills it in, the hook stays quiet on the follow-up edit.
#
# Deliberately a placeholder that reads as debt, not as an exemption — the PR is
# created as a draft, and this is what has to be replaced before it leaves draft.
tmp_body="$(mktemp)"
current_body="$(gh pr view "$pr_number" --json body --jq '.body' 2>/dev/null)"
{
  printf '%s' "$current_body"
  printf '\n\n## Evidence\n'
  printf 'Not captured — this PR was opened automatically on push, before any\n'
  printf 'before/after probe was run. Replace this section with a real capture\n'
  printf 'before taking the PR out of draft. See ~/.claude/skill-library/create-pr/SKILL.md,\n'
  printf '"Attach Runtime Evidence".\n'
} > "$tmp_body"

if ! gh api -X PATCH "repos/{owner}/{repo}/pulls/$pr_number" -F "body=@$tmp_body" >/dev/null 2>&1; then
  echo "WARN: could not write the Evidence placeholder into PR #$pr_number — add it by hand." >&2
fi
rm -f "$tmp_body"

echo "[PR-AUTO-CREATED] PR #$pr_number carries a placeholder '## Evidence' section."
echo "Capture the real before/after and replace it before undrafting — the before-state"
echo "is cheapest to capture now and costs a revert once the change is merged."
if [ -f "PULL_REQUEST_TEMPLATE.md" ] || [ -f ".github/PULL_REQUEST_TEMPLATE.md" ] || [ -f "docs/PULL_REQUEST_TEMPLATE.md" ]; then
  echo "[PR-AUTO-CREATED] A PULL_REQUEST_TEMPLATE.md exists — rewrite PR #$pr_number's body to follow it,"
  echo "sized to what a reviewer needs to make a decision (not the diff size), then run:"
  echo "  gh pr edit $pr_number --body \"<rewritten body>\""
fi
LOG_SCRIPT=".claude/scripts/pr/log_review.sh"
if [ -f "$LOG_SCRIPT" ]; then
  nohup bash "$LOG_SCRIPT" "$pr_number" >/dev/null 2>&1 &
  echo "[PR-AUTO-CREATED] Automated review logging to .claude/memory/pr-reviews/pr-$pr_number.md running in background."
fi
echo "Read ~/.claude/skill-library/pr-babysit/SKILL.md and follow it to watch CI/review feedback in the background."

# REGISTRATION (see pr-auto-create-hook.sh / pr-mention-nudge.sh for the hooks that call this)
