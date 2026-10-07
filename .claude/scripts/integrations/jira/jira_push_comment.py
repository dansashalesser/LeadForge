#!/usr/bin/env python3
"""
Post a Jira comment after a git push, when a jira-solve session was active.

Called by the PostToolUse Bash hook whenever `git push` is detected.
Reads the active ticket from ~/.claude/state/active_jira_ticket.
Composes a comment from git log + any nearby docs file, posts it, then cleans up.

Usage (invoked automatically by Claude Code hook):
    python3 jira_push_comment.py <project_root>
"""

import json
import os
import subprocess
import sys
from pathlib import Path

STATE_FILE = Path.home() / ".claude" / "state" / "active_jira_ticket"
JIRA_CLIENT = Path(__file__).parent / "jira_client.py"


def run(cmd: list[str], cwd: str | None = None) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, cwd=cwd
        )
        return result.stdout.strip()
    except Exception:
        return ""


def find_docs_file(project_root: str, ticket: str) -> str | None:
    """Look for a recently written docs file mentioning this ticket."""
    docs_dir = Path(project_root) / "docs"
    if not docs_dir.exists():
        return None
    # Search docs/**/*.md for the ticket ID or files modified recently
    for md in sorted(docs_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        content = md.read_text(errors="ignore")
        if ticket in content or md.stat().st_mtime > (Path(project_root).stat().st_mtime - 86400):
            return content[:3000]  # first 3000 chars is enough context
    return None


def markdown_section(text: str, heading: str) -> str:
    """The `heading` block, heading line included, up to the next `## ` or EOF."""
    start = text.find(heading)
    if start == -1:
        return ""
    nxt = text.find("\n## ", start + len(heading))
    return text[start:] if nxt == -1 else text[start:nxt]


def strip_table_cells(text: str) -> str:
    """Drop `|…|` spans so markdown tables do not land in the Jira comment.

    Only pairs of pipes on the same line are removed — a lone trailing pipe must
    not swallow the rest of the document.
    """
    out = []
    i = 0
    while True:
        start = text.find("|", i)
        if start == -1:
            break
        end = text.find("|", start + 1)
        if end == -1:
            break
        if "\n" in text[start:end]:
            out.append(text[i:start + 1])
            i = start + 1
            continue
        out.append(text[i:start])
        i = end + 1
    out.append(text[i:])
    return "".join(out)


def extract_metrics(text: str) -> list[str]:
    """Pull `92%`, `12.5%+` and `34 tests` out of a result section, in order."""
    found: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if not text[i].isdigit():
            i += 1
            continue
        j = i
        while j < n and text[j].isdigit():
            j += 1
        if text[j:j + 1] == "." and text[j + 1:j + 2].isdigit():
            j += 1
            while j < n and text[j].isdigit():
                j += 1
        if text[j:j + 1] == "%":
            j += 1
            if text[j:j + 1] == "+":
                j += 1
            found.append(text[i:j])
        elif text[j:j + 6] == " tests":
            j += 6
            found.append(text[i:j])
        i = j
    return found


def is_ticket_key(value: str) -> bool:
    """`ABC-123` — uppercase ASCII project key, hyphen, digits, nothing else."""
    project, sep, number = value.partition("-")
    return bool(sep and project and number
                and project.isascii() and project.isalpha() and project.isupper()
                and number.isascii() and number.isdigit())


def build_comment(ticket: str, project_root: str) -> str:
    # Git context
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project_root)
    log = run(
        ["git", "log", "origin/main..HEAD", "--no-merges", "--oneline"],
        cwd=project_root,
    )
    stat = run(
        ["git", "diff", "--stat", "origin/main..HEAD"],
        cwd=project_root,
    )

    # Count commits
    commit_count = len([l for l in log.splitlines() if l.strip()])

    # Key files changed
    changed_files_raw = run(
        ["git", "diff", "--name-only", "origin/main..HEAD"],
        cwd=project_root,
    )
    changed_files = [f for f in changed_files_raw.splitlines() if f.strip()]

    # Docs context
    docs_content = find_docs_file(project_root, ticket) or ""

    # New test files
    new_tests = [f for f in changed_files if f.startswith("tests/") and f.endswith(".py")]
    new_docs = [f for f in changed_files if f.endswith(".md")]

    # Build the comment
    lines = [
        f"h3. ✅ Code pushed — {ticket}",
        "",
        f"*Branch:* {branch}",
        f"*Commits:* {commit_count}",
        "",
    ]

    if new_tests:
        lines += [
            "h4. What was done",
            "",
        ]
        for f in new_tests:
            lines.append(f"* Added {{code}}{f}{{code}}")
        lines.append("")

    if docs_content:
        # Pull the "What we did" section from docs if available
        what_section = markdown_section(docs_content, "## The Approach")
        result_section = markdown_section(docs_content, "## Result")

        if what_section:
            approach_text = strip_table_cells(what_section)[:600].strip()
            lines += [
                "h4. Approach",
                "",
                approach_text,
                "",
            ]

        if result_section:
            # Extract key numbers from the result table
            nums = extract_metrics(result_section)
            if nums:
                lines += [
                    "h4. Result",
                    "",
                    "Key metrics: " + " | ".join(nums[:4]),
                    "",
                ]
    else:
        # Fallback: summarise from git log
        if log:
            lines += [
                "h4. Commits",
                "",
            ]
            for entry in log.splitlines()[:8]:
                lines.append(f"* {entry}")
            lines.append("")

    if changed_files:
        key_files = [f for f in changed_files if not f.endswith(".pyc")][:10]
        lines += [
            "h4. Files changed",
            "",
        ]
        for f in key_files:
            lines.append(f"* {{code}}{f}{{code}}")
        lines.append("")

    lines += [
        "----",
        "_Posted automatically by Claude Code after git push._",
    ]

    return "\n".join(lines)


def main() -> None:
    if not STATE_FILE.exists():
        sys.exit(0)  # No active Jira session — nothing to do

    ticket = STATE_FILE.read_text().strip()
    if not is_ticket_key(ticket):
        STATE_FILE.unlink(missing_ok=True)
        sys.exit(0)

    project_root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

    comment = build_comment(ticket, project_root)

    # Post via jira_client.py
    result = subprocess.run(
        [sys.executable, str(JIRA_CLIENT), "comment", ticket, comment],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode == 0:
        STATE_FILE.unlink(missing_ok=True)
        print(f"Posted Jira comment to {ticket}")
    else:
        print(f"Failed to post comment: {result.stderr}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
