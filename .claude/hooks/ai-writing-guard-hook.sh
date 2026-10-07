#!/bin/bash
# AI-writing guard — PreToolUse (matcher: Write|Edit|MultiEdit|Bash), HARD DENY.
#
# Blocks AI-sounding buzzwords/cliches before they land in a file write/edit or
# a git commit / gh PR body. Scoped narrowly so it never touches real code:
#   - markdown files (.md/.markdown/.mdx/.txt): whole text, minus fenced/inline code
#   - hash-comment languages (.py/.sh/.yaml/...): only # comments and docstrings
#   - C-style languages (.js/.ts/.java/...): only /* */ and // comments
#   - Bash: only the -m/--message/-b/--body/-t/--title text or heredoc body of a
#     git commit / gh command — never the rest of the command line
#
# Adapted from claude-codex-settings' "humanize" plugin (github.com/fcakyon/
# claude-codex-settings). Ported because none of the harness's other hooks
# check *how text reads* — they cover security/git-safety/behavior, not style
# drift, and long sessions drift toward AI-sounding prose that a prompt
# reminder won't reliably self-police.
set -u

EVENT_FILE="$(mktemp)"
cat > "$EVENT_FILE"
trap 'rm -f "$EVENT_FILE"' EXIT

EVENT_FILE="$EVENT_FILE" python3 - <<'PY' 2>/dev/null || true
import json, os, re, sys
from collections import Counter
from pathlib import Path

try:
    e = json.load(open(os.environ["EVENT_FILE"]))
except Exception:
    sys.exit(0)

tool = e.get("tool_name", "") or ""
tinp = e.get("tool_input", {}) or {}

SWAP = {
    "leverage": "use", "utilize": "use", "plethora": "many", "myriad": "many",
    "delve": "look at", "paradigm": "model", "tapestry": "mix", "showcase": "show",
    "prose": "text", "realm": "area", "landscape": "field", "innovative": "new",
    "transformative": "major", "unprecedented": "new", "consolidate": "merge",
    "modernize": "update", "streamline": "simplify", "flexible": "adjustable",
    "establish": "set up", "enhanced": "better", "comprehensive": "full", "optimize": "improve",
    "unequivocally": "clearly", "crucially": "drop it", "remarkably": "drop it",
    "seamlessly": "drop it", "manifestation": "sign", "testament": "sign",
    "prominent": "clear", "underscoring": "showing", "cultivating": "building",
    "fostering": "building", "encompassing": "covering", "facilitating": "helping",
    "emphasizing": "showing", "embodying": "showing", "vibrant": "lively",
    "game-changing": "big", "cutting-edge": "latest",
}
PHRASES = [
    (r"ever[- ]evolving", "drop 'ever-evolving'"),
    (r"fast[- ]paced world", "drop 'fast-paced world'"),
    (r"a testament to", "say what it shows"),
    (r"it is important to note", "state the point"),
    (r"as an ai language model", "drop it"),
    (r"in conclusion", "drop it"),
    (r"in summary", "drop it"),
    (r"to sum up", "drop it"),
    (r"plays? a \w+ role in shaping", "say what it does"),
]
LIMIT = 3
OFTEN = ["crucial", "essential", "vital", "significant", "moreover", "furthermore", "additionally", "aligns", "explore"]
# Em-dash intentionally excluded: this harness's own docs/hook comments use it
# as house style (headers, box-drawn banners) - flagging it would block routine
# doc edits, not just AI-sounding writing.
MARKS = {"§": "section sign"}

SWAP_RE = re.compile(r"\b(" + "|".join(SWAP) + r")\b", re.IGNORECASE)
OFTEN_RE = re.compile(r"\b(" + "|".join(OFTEN) + r")\b", re.IGNORECASE)
HEREDOC = re.compile(r"<<-?[ \t]*[\"']?([A-Za-z_]\w*)[\"']?([^\n]*)\r?\n(.*?)\r?\n[ \t]*\1[ \t]*$", re.DOTALL | re.MULTILINE)

MD_EXT = {".md", ".markdown", ".mdx", ".txt"}
HASH_EXT = {".py", ".sh", ".bash", ".zsh", ".rb", ".yaml", ".yml", ".toml"}
C_EXT = {".js", ".ts", ".jsx", ".tsx", ".c", ".cc", ".cpp", ".h", ".hpp", ".java", ".go", ".rs", ".css", ".scss", ".swift", ".kt", ".php"}


def md_text(text):
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return re.sub(r"`[^`]*`", "", text)


def hash_comments(text):
    out = re.findall(r'^[ \t]*[rbuRBU]*"""(.*?)"""', text, flags=re.DOTALL | re.MULTILINE)
    out += re.findall(r"^[ \t]*[rbuRBU]*'''(.*?)'''", text, flags=re.DOTALL | re.MULTILINE)
    out += [m.group(1) for line in text.splitlines() if (m := re.search(r"(?:^|\s)#(.*)", line))]
    return "\n".join(out)


def c_comments(text):
    out = re.findall(r"/\*(.*?)\*/", text, flags=re.DOTALL)
    out += [m.group(1) for line in text.splitlines() if (m := re.search(r"(?:^|\s)//(.*)", line))]
    return "\n".join(out)


def checked(path, text):
    ext = Path(path).suffix.lower()
    if ext in MD_EXT:
        return md_text(text)
    if ext in HASH_EXT:
        return hash_comments(text)
    if ext in C_EXT:
        return c_comments(text)
    return ""


def bash_text(command):
    """Return commit/PR message text from a git-commit or gh command, else empty."""
    if not re.search(r"\bgit\b[^|&;\n]*\bcommit\b", command) and not re.search(r"\bgh\b", command):
        return ""
    parts = [m.group(3) for m in HEREDOC.finditer(command)]
    flag_vals = re.findall(r'(?:-m|--message|-b|--body|-t|--title)\s+"([^"]*)"', command)
    flag_vals += re.findall(r"(?:-m|--message|-b|--body|-t|--title)\s+'([^']*)'", command)
    return "\n".join(parts + flag_vals)


def violation(text):
    if m := SWAP_RE.search(text):
        word = m.group(1).lower()
        return f"AI-buzzword '{m.group(1)}' — swap for '{SWAP[word]}'"
    for pat, fix in PHRASES:
        if re.search(pat, text, re.IGNORECASE):
            return f"AI-cliche phrase matching /{pat}/ — {fix}"
    counts = Counter(w.lower() for w in OFTEN_RE.findall(text))
    for word, n in counts.items():
        if n >= LIMIT:
            return f"'{word}' used {n}× — overused AI hedge word, cut most occurrences"
    for mark, note in MARKS.items():
        if mark in text:
            return f"'{mark}' found — {note}"
    return None


text_to_check = ""
if tool in ("Write", "Edit", "MultiEdit"):
    file_path = tinp.get("file_path", tinp.get("path", ""))
    if tool == "MultiEdit":
        content = "\n".join(ed.get("new_string", "") for ed in tinp.get("edits", []) if isinstance(ed, dict))
    else:
        content = tinp.get("content", tinp.get("new_string", ""))
    text_to_check = checked(file_path, str(content))
elif tool == "Bash":
    text_to_check = bash_text(str(tinp.get("command", "")))

if text_to_check:
    reason = violation(text_to_check)
    if reason:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"AI-writing guard: {reason}. Rewrite in plain language and retry.",
            }
        }))
PY

exit 0

# REGISTRATION (settings.json) — wired in templates/settings.json.template:
# "PreToolUse": [
#   { "matcher": "Write|Edit|MultiEdit|Bash",
#     "hooks": [ { "type": "command", "command": "bash .claude/hooks/ai-writing-guard-hook.sh" } ] }
# ]
