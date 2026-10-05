#!/usr/bin/env python3
"""Self-heal: strip ANTHROPIC_BASE_URL from settings.json when it points at a
dead local headroom proxy, so Claude Code falls back to api.anthropic.com
instead of failing every request with ConnectionRefused.

Why this exists: headroom-setup.sh wires routing only after confirming the
proxy is healthy (see § 4 there), but nothing un-wires it if the proxy later
dies (crash, machine restart racing launchd, a bad headroom upgrade). Once
that happens, ANTHROPIC_BASE_URL sits in ~/.claude/settings.json pointing at
a proxy that refuses every connection, and headroom's own SessionStart hook
("headroom init hook ensure") re-asserts that routing on every single session
start, so hand-editing the file back out does not stick.

Called from session-start-hook.sh on every session start, so it must be fast
(bounded ~1.5s) and never raise — a hook failure here must not block the
session it is trying to protect. Idempotent: a healthy or unrouted proxy is a
silent no-op.
"""
import json
import socket
import sys
from urllib.parse import urlparse

TIMEOUT_S = 1.5


def main(settings_path):
    try:
        with open(settings_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return 0

    env = data.get("env")
    if not isinstance(env, dict):
        return 0

    base_url = env.get("ANTHROPIC_BASE_URL")
    if not base_url:
        return 0

    host = urlparse(base_url).hostname
    if host not in ("127.0.0.1", "localhost"):
        return 0  # not routed through a local proxy — nothing for this to do

    port = urlparse(base_url).port or 80
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT_S):
            return 0  # something is listening — leave routing alone
    except OSError:
        pass  # connection refused / timed out — proxy is dead, fall through

    del env["ANTHROPIC_BASE_URL"]
    try:
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    except OSError:
        return 0

    print(
        f"[HEADROOM-SELFHEAL] {base_url} is not accepting connections — "
        f"removed ANTHROPIC_BASE_URL from {settings_path} for this session. "
        f"Claude Code now talks to api.anthropic.com directly. Routing resumes "
        f"automatically once the headroom proxy is healthy again."
    )
    return 0


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        sys.exit(0)
    try:
        sys.exit(main(path))
    except Exception:
        sys.exit(0)
