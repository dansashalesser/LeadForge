#!/usr/bin/env python3
"""Repair project .claude/settings.json files broken by trailing // comments.

Older harness installs copied templates/settings.json.template verbatim, and
that template carried a ktx notes block of `//` lines after the closing brace.
JSON permits neither, so Claude Code dropped every permission rule and hook in
the file. This moves any trailing comment block into a sidecar
`.claude/settings.notes.md` and leaves valid JSON behind.

Usage:
    repair-settings-json.py <project_dir> [<project_dir> ...]
    repair-settings-json.py --dry-run <project_dir>

Only touches files that fail to parse AND parse cleanly once the trailing
comment block is removed. Anything else is reported for manual review.
"""
import json
import sys
from pathlib import Path


def _is_comment_or_blank(line):
    """True for an empty line or one whose only content is a `//` comment."""
    stripped = line.strip()
    return not stripped or stripped.startswith("//")


def _strip_comment_marker(line):
    """Drop a leading `// ` (or bare `//`) from one comment line."""
    stripped = line.lstrip()
    if not stripped.startswith("//"):
        return line
    body = stripped[2:]
    return body.removeprefix(" ")


class RepairError(Exception):
    """Settings file is broken in a way this script must not guess at."""


def split_trailing_comments(text):
    """Return (json_part, comment_part) by peeling // lines off the end."""
    lines = text.splitlines()
    cut = len(lines)
    while cut > 0 and _is_comment_or_blank(lines[cut - 1]):
        cut -= 1
    if cut == len(lines):
        raise RepairError("no trailing comment block found")
    return "\n".join(lines[:cut]) + "\n", "\n".join(lines[cut:]).strip()


def comments_to_markdown(comment_block):
    body = "\n".join(_strip_comment_marker(line) for line in comment_block.splitlines())
    return (
        "# settings.json notes\n\n"
        "JSON allows no comments — Claude Code refuses to parse `.claude/settings.json`\n"
        "if anything follows the closing brace. Moved out of settings.json by\n"
        "`scripts/setup/repair-settings-json.py`:\n\n"
        "```\n" + body.strip() + "\n```\n"
    )


def repair(project_dir, dry_run=False):
    settings = Path(project_dir) / ".claude" / "settings.json"
    if not settings.is_file():
        return f"SKIP    {settings} — not found"

    text = settings.read_text()
    try:
        json.loads(text)
        return f"OK      {settings} — already valid"
    except json.JSONDecodeError as err:
        first_error = err

    try:
        json_part, comment_part = split_trailing_comments(text)
        json.loads(json_part)
    except (RepairError, json.JSONDecodeError):
        return (f"MANUAL  {settings} — {first_error.msg} at line {first_error.lineno}; "
                "not a trailing comment block, fix by hand")

    if dry_run:
        return f"WOULD   {settings} — move {len(comment_part.splitlines())} comment lines to sidecar"

    notes = settings.parent / "settings.notes.md"
    if not notes.exists():
        notes.write_text(comments_to_markdown(comment_part))
    settings.write_text(json_part)
    return f"FIXED   {settings} — notes moved to {notes}"


def main(argv):
    dry_run = "--dry-run" in argv
    targets = [a for a in argv if not a.startswith("--")]
    if not targets:
        print(__doc__)
        return 1
    for target in targets:
        print(repair(target, dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
