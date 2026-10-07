#!/bin/bash
# Logs an automated review for a freshly auto-created PR, closing the gap where
# detect_base_and_create.sh only echoed advisory "go review this" text that a
# session could ignore. Invoked once, right after PR creation, by
# detect_base_and_create.sh — never on pushes to an already-open PR (avoids
# re-reviewing on every commit).
#
# Runs headless (same pattern as code-review-learning-runner.sh): claude --print
# with bypassPermissions, backgrounded by the caller so the hook returns fast.
# Best-effort — never fails the caller.
#
# Usage: log_review.sh <pr_number>
# Writes:
#   .claude/memory/pr-reviews/pr-<n>.md           (human-readable review)
#   .claude/memory/pr-reviews/pr-<n>.review.json  (schema-controlled output —
#                                                   see gitnexus-pr-review's
#                                                   Output Contract section)

set -u

PR_NUMBER="${1:-}"
[ -z "$PR_NUMBER" ] && exit 0

command -v claude >/dev/null 2>&1 || exit 0
command -v gh >/dev/null 2>&1 || exit 0

REVIEW_DIR=".claude/memory/pr-reviews"
OUT_MD="$REVIEW_DIR/pr-$PR_NUMBER.md"
OUT_JSON="$REVIEW_DIR/pr-$PR_NUMBER.review.json"
VALIDATOR=".claude/scripts/pr/validate_review_json.py"

mkdir -p "$REVIEW_DIR"

PROMPT="Review PR #$PR_NUMBER following ~/.claude/skill-library/gitnexus-pr-review/SKILL.md (fall back to
~/.claude/skill-library/code-reviewer/SKILL.md if the GitNexus index is stale or unavailable). Write the
human-readable review to $OUT_MD using gitnexus-pr-review's Review Output Format.
Then, per gitnexus-pr-review's Output Contract section, ALSO write the
structured $OUT_JSON (review.json schema: verdict, body, comments[] with
CRITICAL/IMPORTANT/SUGGESTION/NIT-prefixed bodies, diff-line-anchored via the
PR diff's [OLD:n]/[NEW:n] annotations). Do NOT run 'gh pr review', 'gh pr
comment', or 'gh api .../reviews' — writing these two files is the entire
task, nothing gets posted from this step. When both files are written, run:
  python3 $VALIDATOR $OUT_JSON $PR_NUMBER
(the trailing PR number runs a line-anchor check — each comment's path/line
must still exist in the PR's current diff, catching anchors left stale by a
force-push or amended commit between generation and posting).
If it reports errors, fix $OUT_JSON and re-validate before finishing."

echo "$PROMPT" | SDD_HEADLESS=1 claude --print --output-format text --permission-mode bypassPermissions >/dev/null 2>&1
exit 0
