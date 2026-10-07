#!/usr/bin/env python3
"""
UserPromptSubmit hook: save the active Jira ticket when /kiro:jira-solve is invoked.

Reads hook stdin JSON, extracts the ticket ID from the prompt, writes it to
~/.claude/state/active_jira_ticket so the push hook can pick it up later.
"""
import json
import sys
from pathlib import Path


def is_ticket_key(value: str) -> bool:
    """`ABC-123` — uppercase ASCII project key, hyphen, digits, nothing else."""
    project, sep, number = value.partition("-")
    return bool(sep and project and number
                and project.isascii() and project.isalpha() and project.isupper()
                and number.isascii() and number.isdigit())


def ticket_after_marker(prompt: str, marker: str = "jira-solve") -> str | None:
    """First ticket key appearing as the next word after `marker`."""
    search_from = 0
    while True:
        found = prompt.find(marker, search_from)
        if found == -1:
            return None
        rest = prompt[found + len(marker):]
        if rest[:1].isspace():
            words = rest.split()
            if words and is_ticket_key(words[0]):
                return words[0]
        search_from = found + len(marker)


data = json.load(sys.stdin)
ticket = ticket_after_marker(data.get("prompt", ""))
if ticket:
    state_dir = Path.home() / ".claude" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "active_jira_ticket").write_text(ticket)
