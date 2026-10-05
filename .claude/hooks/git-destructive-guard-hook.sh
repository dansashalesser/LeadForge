#!/bin/bash
# Hard block on destructive git/gh operations, independent of settings.json's
# declarative allow/deny list — that list has been observed to NOT reliably block
# `git push --force` in some sessions even with an explicit deny entry present.
# This hook inspects the Bash command and exits 2 (hard block, tool call is
# prevented) on any match. Soft nudges (protected-path-hook.sh) only warn;
# this hook is the one place in the harness that actually refuses to run.
#
# Blocks:
#   - any force-push variant: --force, --force-with-lease, --force-if-includes,
#     -f, and short-option bundles containing f (e.g. -fu)
#   - remote branch deletion via push: --delete/-d, or `git push origin :branch`
#   - mirror push (can delete/overwrite arbitrary remote refs)
#   - local force branch delete: git branch -D / --delete --force
#   - repo deletion: gh repo delete
#   - git rebase (rewrites shared history same as force-push)
#
# MATCHING: normalize before comparing. The command is tokenized with shlex
# (a real shell lexer), split on operators, and compared token-by-token against
# exact flag names. It does NOT substring/regex-match the rendered command text.
#
# Why this matters — the previous implementation regex-stripped quoted segments
# and then grepped the remaining raw text. Every one of these defeated it:
#     F=--force; git push $F
#     bash -c 'git push --force'
#     git push --fo""rce
#     cd sub && git push --force
# A guard that matches the *rendered string form* of a structured value can
# always be defeated by re-rendering the same value a different way. Parse to
# the structure, then compare. (Same class of bug as blocking the literal string
# "169.254.169.254" while `curl http://2852039166/` reaches the same address —
# Google ADK long-horizon-harness `exfil_guard.py`, 2026-08.)
#
# FAIL-CLOSED: if the command cannot be parsed, or a destructive-capable verb
# carries an unresolved shell expansion ($VAR, $(...), backticks) that could
# expand to a flag, the hook blocks and asks for the value to be inlined. This
# is deliberate over-blocking on a small set of high-stakes verbs.
#
# NOTE ON VERDICT CONTEXT: this hook only ever emits allow or hard-block; it has
# no "ask the human" verdict, which is correct — the harness's routine runners
# (scripts/orchestration/daily-orchestrator.sh, scripts/routines/*) run headless,
# where a prompt-the-human verdict is unanswerable and silently becomes a hang or
# an implicit allow. See skills/agent-permissions-design/SKILL.md ("Verdict
# computation and context-dependence").

EVENT=$(cat)

# FAIL-CLOSED applies to the hook's own failures too, not only to the command's.
# If python3 is missing or the event is not the JSON shape expected, the parse
# exits non-zero. An empty COMMAND from a *failed* parse is not the same as an
# event with no command, so it must not fall through to `exit 0` — that turned
# a broken interpreter into a silent allow. Narrowed to events that mention git
# or gh at all (the same rule check_text applies to unbalanced quotes), so a
# broken python3 does not take down every Bash call. Literal glob, not regex.
# (Perplexity, "How we engineer safer agents", 2026-09-29: "A safeguard the
# agent can decline to invoke, or reconfigure, is not a safeguard.")
fail_closed() {
  case "$EVENT" in
    *git*|*"gh "*) ;;
    *) exit 0 ;;
  esac
  echo "BLOCKED: git-destructive-guard-hook.sh could not verify this command" >&2
  echo "Reason: $1" >&2
  echo "Fix the guard's environment (python3 on PATH, well-formed event) rather than working around it." >&2
  exit 2
}

COMMAND=$(printf '%s' "$EVENT" | python3 -c "
import json, sys
e = json.load(sys.stdin)
command = e.get('tool_input', {}).get('command', '')
if not isinstance(command, str):
    raise SystemExit('tool_input.command is not a string')
print(command)
" 2>/dev/null) || fail_closed "could not parse the hook event (python3 missing or malformed JSON)"

[ -z "$COMMAND" ] && exit 0

REASON=$(python3 - "$COMMAND" <<'PYEOF'
import shlex
import sys

RAW = sys.argv[1] if len(sys.argv) > 1 else ""

OPERATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "{", "}"}
SHELL_WRAPPERS = {"bash", "sh", "zsh", "dash", "ksh"}
# git global options that consume the following token as their value
GIT_GLOBAL_WITH_ARG = {"-c", "-C", "--exec-path", "--git-dir", "--work-tree",
                       "--namespace", "--config-env"}
FORCE_FLAGS = {"--force", "-f", "--force-with-lease", "--force-if-includes"}
# chr(96) is a backtick — written this way because a literal backtick inside the
# $( ... ) command substitution that wraps this heredoc is parsed by bash first.
EXPANSION_MARKERS = ("$", chr(96))


def tokenize(text):
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def split_commands(tokens):
    """Split a token stream on shell operators into individual commands."""
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
        # an assignment is NAME=..., where NAME has no shell metachars
        eq = tok.find("=")
        if eq > 0 and tok[:eq].replace("_", "").isalnum() and not tok[:eq][0].isdigit():
            i += 1
            continue
        break
    return tokens[i:]


def has_unresolved_expansion(tokens):
    return any(any(m in tok for m in EXPANSION_MARKERS) for tok in tokens)


def git_subcommand(tokens):
    """Return (subcommand, args, alias_injected) skipping git global options."""
    i = 1
    alias_injected = False
    while i < len(tokens):
        tok = tokens[i]
        if tok in GIT_GLOBAL_WITH_ARG:
            value = tokens[i + 1] if i + 1 < len(tokens) else ""
            # `git -c alias.p='push --force' p` hides the verb behind an alias
            if tok == "-c" and value.startswith("alias."):
                alias_injected = True
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok, tokens[i + 1:], alias_injected
    return None, [], alias_injected


def is_force_flag(tok):
    if tok in FORCE_FLAGS:
        return True
    if tok.startswith("--force-with-lease=") or tok.startswith("--force-if-includes="):
        return True
    # short-option bundle such as -fu / -uf
    if tok.startswith("-") and not tok.startswith("--") and len(tok) > 1:
        return "f" in tok[1:]
    return False


def is_delete_flag(tok):
    if tok in ("--delete", "-d"):
        return True
    if tok.startswith("-") and not tok.startswith("--") and len(tok) > 1:
        return "d" in tok[1:]
    return False


def check_command(tokens):
    """Return a block reason string, or None if this command is allowed."""
    tokens = strip_env_prefix(tokens)
    if not tokens:
        return None

    argv0 = tokens[0].rsplit("/", 1)[-1]

    # recurse into `bash -c '<inner>'`
    if argv0 in SHELL_WRAPPERS:
        for idx, tok in enumerate(tokens):
            if tok == "-c" and idx + 1 < len(tokens):
                return check_text(tokens[idx + 1])
        return None

    if argv0 == "git":
        sub, args, alias_injected = git_subcommand(tokens)
        if alias_injected:
            return ("git -c alias.* injects a hidden subcommand — the real verb "
                    "cannot be verified; run the command without the alias")
        if sub is None:
            return None

        if sub == "rebase":
            return ("git rebase rewrites shared history — add a follow-up commit "
                    "or a fresh branch instead")

        if sub == "push":
            if has_unresolved_expansion(args):
                return ("git push carries an unresolved shell expansion that could "
                        "expand to --force; inline the literal value instead")
            for tok in args:
                if is_force_flag(tok):
                    return "force-push variant detected"
                if is_delete_flag(tok) or tok == "--mirror":
                    return "remote branch deletion or mirror-push detected"
                # empty source refspec, e.g. `git push origin :branch`
                if tok.startswith(":") and len(tok) > 1:
                    return "empty-refspec remote branch deletion detected"
            return None

        if sub == "branch":
            if has_unresolved_expansion(args):
                return ("git branch carries an unresolved shell expansion that could "
                        "expand to -D; inline the literal value instead")
            has_force = any(tok == "--force" or (
                tok.startswith("-") and not tok.startswith("--") and "f" in tok[1:]
            ) for tok in args)
            for tok in args:
                if tok.startswith("-") and not tok.startswith("--") and "D" in tok[1:]:
                    return "force local branch delete detected"
                if tok == "--delete" and has_force:
                    return "force local branch delete detected"
            return None

        return None

    if argv0 == "gh":
        rest = [t for t in tokens[1:] if not t.startswith("-")]
        if len(rest) >= 2 and rest[0] == "repo" and rest[1] == "delete":
            return "repo deletion via gh CLI detected"
        if rest[:1] == ["repo"] and has_unresolved_expansion(tokens[1:]):
            return ("gh repo carries an unresolved shell expansion that could expand "
                    "to `delete`; inline the literal subcommand instead")
        return None

    return None


def check_text(text):
    try:
        tokens = tokenize(text)
    except ValueError:
        # unparseable (unbalanced quotes) — fail closed only if it mentions a
        # destructive-capable binary at all
        lowered = text.lower()
        if "git " in lowered or "gh " in lowered:
            return ("command could not be parsed safely (unbalanced quotes) and "
                    "mentions git/gh — rewrite it so it parses")
        return None
    for cmd in split_commands(tokens):
        reason = check_command(cmd)
        if reason:
            return reason
    return None


reason = check_text(RAW)
print(reason if reason else "")
PYEOF
) || fail_closed "the command analyzer crashed before reaching a verdict"

if [ -n "$REASON" ]; then
  echo "BLOCKED: destructive git/gh operation refused by git-destructive-guard-hook.sh" >&2
  echo "Reason: $REASON" >&2
  echo "Command: $COMMAND" >&2
  echo "If this is genuinely needed, ask the user to run it manually in their own shell." >&2
  exit 2
fi

exit 0
