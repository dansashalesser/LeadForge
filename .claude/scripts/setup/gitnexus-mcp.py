#!/usr/bin/env python3
"""Check or wire the GitNexus MCP server for a single project.

Modes:
  check <project_dir>   exit 0 if a gitnexus MCP server is configured for the
                        project (project .mcp.json, project settings.json, or
                        the user-scope ~/.claude.json entry for this path)
  wire  <project_dir>   write the server into <project>/.mcp.json and enable it
                        in <project>/.claude/settings.json

Never rewrites a settings file it cannot parse — a malformed file is reported
and left alone so no permission or hook config is silently lost.
"""

import json
import os
import sys

SERVER_NAME = "gitnexus"
SERVER_ENTRY = {"command": "npx", "args": ["-y", "gitnexus", "mcp"]}


class UnparseableConfig(Exception):
    """Config file exists but is not valid JSON, even after comment stripping."""


def load_json(path):
    """Return parsed JSON, {} if the file is absent, or raise UnparseableConfig."""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Claude Code tolerates JSON5-style // comments in settings.json
    try:
        return json.loads(_strip_line_comments(raw))
    except json.JSONDecodeError as exc:
        raise UnparseableConfig(path) from exc


def _strip_line_comments(text):
    """Remove `//` line comments from JSONC, leaving `//` inside strings intact.

    Blanket removal corrupts any settings file containing a URL — `"http://x"`
    becomes `"http:` and a valid config is reported as unparseable.
    """
    out = []
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
        elif ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif ch == "/" and text[i + 1:i + 2] == "/":
            newline = text.find("\n", i)
            if newline == -1:
                break
            i = newline
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def dump_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def is_wired(project):
    """True if any config scope already provides the gitnexus MCP server."""
    mcp_json = load_json(os.path.join(project, ".mcp.json"))
    if SERVER_NAME in mcp_json.get("mcpServers", {}):
        return True

    settings = load_json(os.path.join(project, ".claude", "settings.json"))
    if SERVER_NAME in settings.get("mcpServers", {}):
        return True

    user_config = load_json(os.path.join(os.path.expanduser("~"), ".claude.json"))
    project_entry = user_config.get("projects", {}).get(os.path.realpath(project), {})
    return SERVER_NAME in project_entry.get("mcpServers", {})


def wire(project):
    """Add the server to .mcp.json and auto-enable it in .claude/settings.json."""
    mcp_path = os.path.join(project, ".mcp.json")
    mcp_json = load_json(mcp_path)
    servers = mcp_json.setdefault("mcpServers", {})
    if servers.get(SERVER_NAME) != SERVER_ENTRY:
        servers[SERVER_NAME] = SERVER_ENTRY
        dump_json(mcp_path, mcp_json)
        print("  GitNexus MCP server written to .mcp.json")

    settings_path = os.path.join(project, ".claude", "settings.json")
    if not os.path.isfile(settings_path):
        return
    settings = load_json(settings_path)
    if settings.get("enableAllProjectMcpServers") is True:
        return
    enabled = settings.setdefault("enabledMcpjsonServers", [])
    if SERVER_NAME not in enabled:
        enabled.append(SERVER_NAME)
        dump_json(settings_path, settings)
        print("  GitNexus enabled in .claude/settings.json")


def main(argv):
    if len(argv) != 3 or argv[1] not in ("check", "wire"):
        print("usage: gitnexus-mcp.py check|wire <project_dir>", file=sys.stderr)
        return 2

    mode, project = argv[1], argv[2]
    try:
        if mode == "check":
            return 0 if is_wired(project) else 1
        wire(project)
        return 0
    except UnparseableConfig as exc:
        print(f"  MANUAL: {exc.args[0]} is not valid JSON — left untouched", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"  MANUAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
