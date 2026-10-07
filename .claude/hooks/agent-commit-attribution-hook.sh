#!/bin/bash
# agent-commit-attribution-hook.sh — PreToolUse(Bash)
# Advisory. Fires before a `git commit` that supplies a message inline and warns
# when that message carries no Co-Authored-By trailer.
#
# WHY THIS EXISTS (it is not a style nag — it repairs a measurement):
#   skills/keep-rate/SKILL.md selects agent-authored commits with
#     git log --all --grep="Co-Authored-By: Claude"
#   and the keep-rate widget in scripts/utils/dashboard.py blames against the
#   same set. An agent commit that ships without the trailer silently drops out
#   of the denominator, so the metric reads HIGH — the failure is invisible and
#   biased in the flattering direction. As of 2026-08 only 10 of the last 30
#   commits in this repo carried the trailer, so the split of this history into
#   agent-authored vs human-authored is already unrecoverable.
#
# WHY PreToolUse AND NOT .git/hooks/commit-msg:
#   A git hook sees a commit, not an author. It cannot tell an agent commit from
#   a human one, so it can only nag on every commit or none. A PreToolUse Bash
#   hook can: if Claude issued the command, it is an agent commit by definition.
#   Enforce identity at the chokepoint that knows who acted. (The framing is
#   lifted from onecli's gateway, which rewrites commit payloads in-flight
#   because GitHub App tokens have "no natural author identity" —
#   github.com/onecli/onecli, apps/gateway/.../github_commit_trailer.rs.)
#
# STRENGTH: soft. Prints to stdout and exits 0 — never blocks a commit. This
# matches address-check-hook.sh, the harness's existing precedent for "did a
# CLAUDE.md instruction survive compaction", which is deliberately a passive
# signal rather than a gate.
#
# EXCLUSIONS (ported from onecli, which skips merge endpoints for the same
# reason): commits whose message is generated or inherited rather than authored
# get no warning — --amend --no-edit, --squash, --fixup, -C/--reuse-message,
# -c/--reedit-message, and any `git commit` with no inline message (an editor
# session, whose content this hook cannot see).

set -u

EVENT=$(cat)

COMMAND=$(printf '%s' "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    print(e.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

[ -z "$COMMAND" ] && exit 0

case "$COMMAND" in
  *commit*) ;;
  *) exit 0 ;;
esac

VERDICT=$(python3 - "$COMMAND" <<'PYEOF'
import shlex
import sys

RAW = sys.argv[1] if len(sys.argv) > 1 else ""

OPERATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "{", "}"}
GIT_GLOBAL_WITH_ARG = {"-c", "-C", "--exec-path", "--git-dir", "--work-tree",
                       "--namespace", "--config-env"}
# flags that mean "the message is generated or inherited, not authored here"
SKIP_FLAGS = {"--squash", "--fixup", "--no-edit", "-C", "--reuse-message",
              "-c", "--reedit-message"}
MESSAGE_FLAGS = {"-m", "--message"}
TRAILER_KEY = "co-authored-by:"


def split_commands(tokens):
    out, cur = [], []
    for tok in tokens:
        if tok in OPERATORS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def strip_env_prefix(tokens):
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "env":
            i += 1
            continue
        eq = tok.find("=")
        if eq > 0 and tok[:eq].replace("_", "").isalnum() and not tok[:eq][0].isdigit():
            i += 1
            continue
        break
    return tokens[i:]


def git_subcommand(tokens):
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in GIT_GLOBAL_WITH_ARG:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok, tokens[i + 1:]
    return None, []


def collect_message(args):
    """Return (message_text, found_inline_message)."""
    parts = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in MESSAGE_FLAGS:
            if i + 1 < len(args):
                parts.append(args[i + 1])
            i += 2
            continue
        if tok.startswith("--message="):
            parts.append(tok.split("=", 1)[1])
            i += 1
            continue
        # short-option forms: -mmsg, and bundles such as -am / -amMSG
        if tok.startswith("-") and not tok.startswith("--") and "m" in tok[1:]:
            rest = tok[1:].split("m", 1)[1]
            if rest:
                parts.append(rest)
                i += 1
            else:
                if i + 1 < len(args):
                    parts.append(args[i + 1])
                i += 2
            continue
        i += 1
    return "\n".join(parts), bool(parts)


def check(tokens):
    tokens = strip_env_prefix(tokens)
    if not tokens:
        return None
    if tokens[0].rsplit("/", 1)[-1] != "git":
        return None
    sub, args = git_subcommand(tokens)
    if sub != "commit":
        return None
    if any(a in SKIP_FLAGS or a.startswith("--fixup=") or a.startswith("--squash=")
           for a in args):
        return None
    message, found = collect_message(args)
    if not found:
        # editor-driven commit — message not visible to this hook
        return None
    if TRAILER_KEY in message.lower():
        return None
    return "missing"


try:
    lex = shlex.shlex(RAW, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    tokens = list(lex)
except ValueError:
    tokens = []

verdict = ""
for cmd in split_commands(tokens):
    if check(cmd):
        verdict = "missing"
        break
print(verdict)
PYEOF
) || VERDICT=""

[ "$VERDICT" = "missing" ] || exit 0

cat << 'EOF'
[attribution] This commit message has no Co-Authored-By trailer.

  keep-rate and its dashboard widget both select agent commits with
  `git log --grep="Co-Authored-By: Claude"`. A commit without the trailer
  is counted as human-authored, which inflates the reported keep rate.

  Append to the commit message body:

    Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

  (Advisory only — the commit was not blocked. Skip this if the change is
  genuinely hand-authored by the user.)
EOF

exit 0

# REGISTRATION — add to .claude/settings.json (already present in
# templates/settings.json.template):
#
#   "PreToolUse": [
#     {
#       "matcher": "Bash",
#       "hooks": [
#         {
#           "type": "command",
#           "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/agent-commit-attribution-hook.sh"
#         }
#       ]
#     }
#   ]
