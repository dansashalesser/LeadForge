#!/usr/bin/env bash
# pr-evidence-hook.sh — PreToolUse(Bash) soft gate on `gh pr create`.
#
# A PR body that describes the change is the agent's own account of its work.
# `verification-before-completion` already demands evidence for claims made in
# conversation, but that evidence evaporates at the PR boundary: the reviewer
# receives a description and nothing to check it against. This hook checks that
# the body carries a literal `## Evidence` section and nudges when it does not.
#
# SOFT, BY DESIGN. It prints to stdout and exits 0 — it never blocks. Not every
# PR has a visible surface, and a hard block would force fabricated evidence
# blocks on docs-only PRs, which is worse than none. The one hook in this harness
# that actually refuses is git-destructive-guard-hook.sh; this is not that.
#
# MATCHING: the command is tokenized with shlex and the `gh pr create` verb is
# matched token-by-token, per the lesson in git-destructive-guard-hook.sh — a
# guard that matches the rendered string form of a structured value can be
# defeated by re-rendering it. The marker search inside the *body value* is a
# plain substring test, and that is correct here: `## Evidence` is a fixed
# structural token, not free text, and there is no adversary — the failure mode
# is a missed nudge, not a bypassed block. No regex is used anywhere (see
# scripts/utils/check-no-regex.py).
#
# REGISTRATION
# {
#   "matcher": "Bash",
#   "hooks": [{"type": "command", "command": "bash \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/pr-evidence-hook.sh\""}]
# }
# Add under PreToolUse in templates/settings.json.template.
#
# NOT COVERED: PRs opened by scripts/pr/detect_base_and_create.sh (the
# push-triggered auto-create path) call `gh pr create` inside the script, not via
# the Bash tool, so no PreToolUse event fires for them. Those PRs get their body
# from the script, and adding evidence to them is a separate change.

set -uo pipefail

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

VERDICT=$(python3 - "$COMMAND" <<'PYEOF'
import shlex
import sys
from pathlib import Path

RAW = sys.argv[1] if len(sys.argv) > 1 else ""

MARKER = "## Evidence"
OPERATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "{", "}"}
SHELL_WRAPPERS = {"bash", "sh", "zsh", "dash", "ksh"}
BODY_INLINE = ("--body", "-b")
BODY_FILE = ("--body-file", "-F")
# gh flags that consume the following token as their value. Without this,
# `gh --repo o/r pr create` reads `o/r` as the subcommand.
GH_FLAG_WITH_ARG = {"--repo", "-R"}


def tokenize(text):
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


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
    """Drop leading VAR=value assignments and a leading `env`."""
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


def read_body_file(name):
    """Body supplied by file. Unreadable or `-` (stdin) means we cannot judge."""
    if not name or name == "-":
        return None
    try:
        return Path(name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def classify(tokens):
    """Return a verdict for one `gh pr create`, or None if it is not one."""
    tokens = strip_env_prefix(tokens)
    if not tokens:
        return None

    argv0 = tokens[0].rsplit("/", 1)[-1]
    if argv0 in SHELL_WRAPPERS:
        for idx, tok in enumerate(tokens):
            if tok == "-c" and idx + 1 < len(tokens):
                return classify_text(tokens[idx + 1])
        return None

    if argv0 != "gh":
        return None

    positional = []
    skip_next = False
    for tok in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if tok in GH_FLAG_WITH_ARG:
            skip_next = True
            continue
        if not tok.startswith("-"):
            positional.append(tok)
    if positional[:2] != ["pr", "create"]:
        return None

    body = None
    saw_body_flag = False
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        for flag in BODY_INLINE:
            if tok == flag and i + 1 < len(tokens):
                saw_body_flag = True
                body = tokens[i + 1]
            elif tok.startswith(flag + "="):
                saw_body_flag = True
                body = tok[len(flag) + 1:]
        for flag in BODY_FILE:
            if tok == flag and i + 1 < len(tokens):
                saw_body_flag = True
                body = read_body_file(tokens[i + 1])
            elif tok.startswith(flag + "="):
                saw_body_flag = True
                body = read_body_file(tok[len(flag) + 1:])
        i += 1

    if not saw_body_flag:
        # --fill / --fill-first derive the body from commit messages; an
        # interactive `gh pr create` with no body flag opens an editor we
        # cannot inspect. Both arrive at the reviewer without an evidence
        # section, so both get the nudge.
        return "MISSING_BODY"
    if body is None:
        # Body exists but is not inspectable (file unreadable, stdin). Say
        # nothing rather than nudge on a guess.
        return None
    return "OK" if MARKER in body else "MISSING_EVIDENCE"


def classify_text(text):
    try:
        tokens = tokenize(text)
    except ValueError:
        # Unparseable (unbalanced quotes). Fall back to a literal scan: a soft
        # nudge may guess, a block may not.
        if "gh pr create" not in text:
            return None
        return "OK" if MARKER in text else "MISSING_EVIDENCE"
    for cmd in split_commands(tokens):
        verdict = classify(cmd)
        if verdict:
            return verdict
    return None


print(classify_text(RAW) or "")
PYEOF
) || VERDICT=""

[ -z "$VERDICT" ] && exit 0
[ "$VERDICT" = "OK" ] && exit 0

if [ "$VERDICT" = "MISSING_BODY" ]; then
  DETAIL="This \`gh pr create\` supplies no inspectable --body."
else
  DETAIL="The --body has no \`## Evidence\` section."
fi

cat << MSG
╔══ PR Evidence — missing proof ═════════════════════════════════════╗
║ ${DETAIL}
╚════════════════════════════════════════════════════════════════════╝

A PR description without evidence is your own account of your own work.
The reviewer cannot check it. Add an \`## Evidence\` section before opening:

  ## Evidence
  **Before:** <symptom reproduced — screenshot, video, or command + output>
  **After:**  <same probe, post-fix — screenshot, video, or command + output>

Rules:
  - Visible surface  -> before/after screenshot or video.
  - No visible surface -> before/after numbers, or the failing/passing
    output of the same command. Same probe both times, or it proves nothing.
  - If the before-state is already gone, say so explicitly. Do NOT
    reconstruct it from memory and present it as a capture.

Capture the BEFORE state while reproducing the problem, before you fix it —
that is the only moment it is cheap. After the fix it costs a revert.

See ~/.claude/skill-library/create-pr/SKILL.md ("Attach Runtime Evidence"). If this PR genuinely has
nothing to show (docs, comments, pure rename), write that one line under
\`## Evidence\` and proceed — this hook does not block.
MSG

exit 0
