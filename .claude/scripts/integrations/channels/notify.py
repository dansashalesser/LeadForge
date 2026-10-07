#!/usr/bin/env python3
"""
Outbound channel notifier for the SDD harness.

Posts a message to whichever of Slack / Discord / Microsoft Teams incoming
webhooks are configured. Stdlib-only — no pip install required (mirrors
scripts/integrations/jira/jira_client.py).

Reads credentials from ~/.env.channels (chmod 600, never under any git repo):

    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
    TEAMS_WEBHOOK_URL=https://....webhook.office.com/...   (or a Power Automate URL)

Any subset may be present. Platforms without a URL are skipped silently.
If the file is absent the command is a clean no-op (exit 0) — so automated
callers (the daily runner) can invoke it unconditionally.

Usage:
    python3 notify.py "your message"
    echo "your message" | python3 notify.py        # message on stdin
    python3 notify.py --only slack "slack-only message"
    python3 notify.py --title "Deploy" "shipped v1.2.3"
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV_FILE = Path.home() / ".env.channels"

# Per-platform hard caps (chars). Discord rejects >2000; keep margin.
LIMITS = {"discord": 1900, "slack": 3500, "teams": 3500}


def load_webhooks() -> dict[str, str]:
    """Return {platform: url} for every configured webhook. Empty if no file."""
    if not ENV_FILE.exists():
        return {}
    keys = {
        "SLACK_WEBHOOK_URL": "slack",
        "DISCORD_WEBHOOK_URL": "discord",
        "TEAMS_WEBHOOK_URL": "teams",
    }
    out: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key in keys and value:
            out[keys[key]] = value
    return out


def payload_for(platform: str, title: str, message: str) -> dict:
    """Build the platform-specific JSON body."""
    text = f"*{title}*\n{message}" if title else message
    text = text[: LIMITS[platform]]
    if platform == "slack":
        return {"text": text}
    if platform == "discord":
        # Discord uses **bold**, not *bold*; rebuild to keep titles readable.
        content = f"**{title}**\n{message}" if title else message
        return {"content": content[: LIMITS["discord"]]}
    # teams — simple text works for legacy O365 connectors; Power Automate
    # "When a Teams webhook request is received" flows can map .text too.
    return {"text": text}


def post(platform: str, url: str, body: dict) -> tuple[bool, str]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
            return True, f"{resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} {e.reason}: {e.read().decode(errors='replace')[:200]}"
    except urllib.error.URLError as e:
        return False, f"connection failed: {e.reason}"


def main() -> None:
    args = sys.argv[1:]
    only = None
    title = ""
    rest: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--only" and i + 1 < len(args):
            only = args[i + 1].lower()
            i += 2
        elif args[i] == "--title" and i + 1 < len(args):
            title = args[i + 1]
            i += 2
        else:
            rest.append(args[i])
            i += 1

    message = " ".join(rest).strip()
    if not message and not sys.stdin.isatty():
        message = sys.stdin.read().strip()
    if not message:
        print("ERROR: no message provided", file=sys.stderr)
        sys.exit(1)

    webhooks = load_webhooks()
    if not webhooks:
        # No config / no file → clean no-op so automated callers don't error.
        print(f"No channels configured ({ENV_FILE} absent or empty) — nothing sent.")
        sys.exit(0)

    if only:
        webhooks = {p: u for p, u in webhooks.items() if p == only}
        if not webhooks:
            print(f"ERROR: --only {only} but no {only} webhook configured", file=sys.stderr)
            sys.exit(1)

    any_fail = False
    for platform, url in webhooks.items():
        ok, detail = post(platform, url, payload_for(platform, title, message))
        if ok:
            print(f"  ✓ {platform}")
        else:
            any_fail = True
            print(f"  ✗ {platform}: {detail}", file=sys.stderr)

    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
