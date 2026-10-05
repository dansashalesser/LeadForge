#!/usr/bin/env python3
"""
Jira REST API v2 client for the SDD workflow.

Reads credentials from ~/.env.jira:

    PAT (Personal Access Token) — Jira Data Center:
        JIRA_URL=https://your-jira-host.example.com
        JIRA_PAT=your-personal-access-token

    Basic auth (Jira Cloud / legacy):
        JIRA_URL=https://your-domain.atlassian.net
        JIRA_USERNAME=your.email@example.com
        JIRA_API_TOKEN=your-api-token-or-password

    If JIRA_PAT is present it takes precedence over Basic auth.

Usage:
    uv run python .claude/scripts/jira_client.py fetch PROJ-123
    uv run python .claude/scripts/jira_client.py comment PROJ-123 "Analysis complete."
    uv run python .claude/scripts/jira_client.py search "project=PROJ AND status='In Progress'"
"""

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ENV_FILE = Path.home() / ".env.jira"

FETCH_FIELDS = [
    "summary",
    "description",
    "issuetype",
    "status",
    "priority",
    "assignee",
    "reporter",
    "labels",
    "components",
    "fixVersions",
    "comment",
    "issuelinks",
    "subtasks",
    "parent",
    "customfield_10014",  # Epic Link (common)
    "customfield_10016",  # Story points (common)
    "customfield_10100",  # Acceptance criteria (common — may vary per instance)
    "customfield_10101",  # Acceptance criteria alt field
    "customfield_10200",  # Acceptance criteria alt field
    "attachment",
]


def load_credentials() -> dict[str, str]:
    if not ENV_FILE.exists():
        print(
            f"ERROR: Credential file not found at {ENV_FILE}\n"
            f"Create it with (PAT / Data Center):\n"
            f"  JIRA_URL=https://your-jira-host.example.com\n"
            f"  JIRA_PAT=your-personal-access-token\n"
            f"Or (Basic auth / Cloud):\n"
            f"  JIRA_URL=https://your-domain.atlassian.net\n"
            f"  JIRA_USERNAME=your.email@example.com\n"
            f"  JIRA_API_TOKEN=your-api-token-or-password",
            file=sys.stderr,
        )
        sys.exit(1)

    creds: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            creds[key.strip()] = value.strip()

    if "JIRA_URL" not in creds:
        print(f"ERROR: Missing JIRA_URL in {ENV_FILE}", file=sys.stderr)
        sys.exit(1)

    # Require either PAT (Bearer) or USERNAME+API_TOKEN (Basic)
    has_pat = "JIRA_PAT" in creds
    has_basic = "JIRA_USERNAME" in creds and "JIRA_API_TOKEN" in creds
    if not has_pat and not has_basic:
        print(
            f"ERROR: {ENV_FILE} must contain either JIRA_PAT (for Data Center PAT auth)\n"
            f"       or both JIRA_USERNAME and JIRA_API_TOKEN (for Basic auth).",
            file=sys.stderr,
        )
        sys.exit(1)

    creds["JIRA_URL"] = creds["JIRA_URL"].rstrip("/")
    return creds


def make_auth_header(creds: dict[str, str]) -> str:
    if "JIRA_PAT" in creds:
        return f"Bearer {creds['JIRA_PAT']}"
    raw = f"{creds['JIRA_USERNAME']}:{creds['JIRA_API_TOKEN']}"
    encoded = base64.b64encode(raw.encode()).decode()
    return f"Basic {encoded}"


def api_request(
    creds: dict[str, str],
    method: str,
    path: str,
    body: dict | None = None,
) -> dict:
    url = f"{creds['JIRA_URL']}{path}"
    auth = make_auth_header(creds)

    headers = {
        "Authorization": auth,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        print(
            f"ERROR: HTTP {e.code} {e.reason} for {method} {url}\n{body_text}",
            file=sys.stderr,
        )
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: Connection failed to {url}: {e.reason}", file=sys.stderr)
        sys.exit(1)


def cmd_fetch(creds: dict[str, str], issue_id: str) -> None:
    """Fetch full issue data and output as JSON."""
    fields = ",".join(FETCH_FIELDS)
    path = f"/rest/api/2/issue/{issue_id}?fields={urllib.parse.quote(fields)}"
    data = api_request(creds, "GET", path)

    fields_data = data.get("fields", {})

    # Extract acceptance criteria — try known custom fields, then scan description
    acceptance_criteria = ""
    for cf in ("customfield_10100", "customfield_10101", "customfield_10200"):
        val = fields_data.get(cf)
        if val and isinstance(val, str):
            acceptance_criteria = val
            break

    # If not in a custom field, try to extract from description (wiki markup)
    description = fields_data.get("description") or ""
    if not acceptance_criteria and "acceptance criteria" in description.lower():
        lines = description.splitlines()
        capturing = False
        ac_lines: list[str] = []
        for line in lines:
            if "acceptance criteria" in line.lower():
                capturing = True
                continue
            if capturing:
                # Stop at next heading
                stripped = line.strip()
                if stripped.startswith("h1.") or stripped.startswith("h2.") or stripped.startswith("h3."):
                    break
                ac_lines.append(line)
        acceptance_criteria = "\n".join(ac_lines).strip()

    # Extract comments (last 10, most recent first)
    comments_raw = fields_data.get("comment", {}).get("comments", [])
    comments = [
        {
            "author": c.get("author", {}).get("displayName", "Unknown"),
            "created": c.get("created", ""),
            "body": c.get("body", ""),
        }
        for c in comments_raw[-10:]
    ]

    # Extract linked issues
    links = []
    for link in fields_data.get("issuelinks", []):
        direction = "inward" if "inwardIssue" in link else "outward"
        linked = link.get(f"{direction}Issue", {})
        links.append(
            {
                "type": link.get("type", {}).get("name", ""),
                "direction": direction,
                "issue_key": linked.get("key", ""),
                "summary": linked.get("fields", {}).get("summary", ""),
                "status": linked.get("fields", {}).get("status", {}).get("name", ""),
            }
        )

    output = {
        "key": data.get("key", issue_id),
        "url": f"{creds['JIRA_URL']}/browse/{data.get('key', issue_id)}",
        "summary": fields_data.get("summary", ""),
        "description": description,
        "acceptance_criteria": acceptance_criteria,
        "issue_type": fields_data.get("issuetype", {}).get("name", "Task"),
        "status": fields_data.get("status", {}).get("name", ""),
        "priority": fields_data.get("priority", {}).get("name", ""),
        "assignee": (fields_data.get("assignee") or {}).get("displayName", "Unassigned"),
        "reporter": (fields_data.get("reporter") or {}).get("displayName", ""),
        "labels": fields_data.get("labels", []),
        "components": [c.get("name", "") for c in fields_data.get("components", [])],
        "comments": comments,
        "linked_issues": links,
        "subtasks": [
            {"key": s.get("key"), "summary": s.get("fields", {}).get("summary", "")}
            for s in fields_data.get("subtasks", [])
        ],
    }

    print(json.dumps(output, indent=2))


def cmd_comment(creds: dict[str, str], issue_id: str, body: str) -> None:
    """Post a comment to a Jira issue."""
    path = f"/rest/api/2/issue/{issue_id}/comment"
    response = api_request(creds, "POST", path, body={"body": body})
    print(json.dumps({"comment_id": response.get("id"), "created": response.get("created")}))


def cmd_edit_comment(creds: dict[str, str], issue_id: str, comment_id: str, body: str) -> None:
    """Edit an existing comment on a Jira issue."""
    path = f"/rest/api/2/issue/{issue_id}/comment/{comment_id}"
    response = api_request(creds, "PUT", path, body={"body": body})
    print(json.dumps({"comment_id": response.get("id"), "updated": response.get("updated")}))


def cmd_search(creds: dict[str, str], jql: str) -> None:
    """Search issues by JQL and output summary list."""
    params = urllib.parse.urlencode({
        "jql": jql,
        "fields": "summary,issuetype,status,priority,assignee",
        "maxResults": 20,
    })
    path = f"/rest/api/2/search?{params}"
    data = api_request(creds, "GET", path)

    issues = [
        {
            "key": issue.get("key"),
            "summary": issue.get("fields", {}).get("summary", ""),
            "type": issue.get("fields", {}).get("issuetype", {}).get("name", ""),
            "status": issue.get("fields", {}).get("status", {}).get("name", ""),
            "priority": issue.get("fields", {}).get("priority", {}).get("name", ""),
        }
        for issue in data.get("issues", [])
    ]
    print(json.dumps({"total": data.get("total", 0), "issues": issues}, indent=2))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(
            "Usage:\n"
            "  jira_client.py fetch <ISSUE-ID>\n"
            "  jira_client.py comment <ISSUE-ID> <body>\n"
            "  jira_client.py search <JQL>",
            file=sys.stderr,
        )
        sys.exit(1)

    creds = load_credentials()
    command = args[0]

    if command == "fetch":
        if len(args) < 2:
            print("ERROR: fetch requires an issue ID", file=sys.stderr)
            sys.exit(1)
        cmd_fetch(creds, args[1])

    elif command == "comment":
        if len(args) < 3:
            print("ERROR: comment requires an issue ID and body", file=sys.stderr)
            sys.exit(1)
        cmd_comment(creds, args[1], " ".join(args[2:]))

    elif command == "edit-comment":
        if len(args) < 4:
            print("ERROR: edit-comment requires an issue ID, comment ID, and body", file=sys.stderr)
            sys.exit(1)
        cmd_edit_comment(creds, args[1], args[2], " ".join(args[3:]))

    elif command == "search":
        if len(args) < 2:
            print("ERROR: search requires a JQL string", file=sys.stderr)
            sys.exit(1)
        cmd_search(creds, " ".join(args[1:]))

    else:
        print(f"ERROR: Unknown command '{command}'", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
