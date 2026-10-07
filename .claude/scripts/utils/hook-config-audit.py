#!/usr/bin/env python3
"""hook-config-audit.py — sweep a repo's own hooks/MCP config as attack surface.

scan-pii.sh (hooks/claude/scan-pii.sh) scans file *content* for PII/secrets.
skill-permissions-gate.sh only fires reactively when a new SKILL.md is written.
Neither re-sweeps the hooks and MCP config that already exist as the harness's
hook count grows. This closes that gap with three deterministic checks:

1. Secrets/PII in hook source and settings — shells out to the repo's own
   scan-pii.sh (same OPF engine), pointed at .claude/hooks/ and config files
   instead of user content.
2. Network-exfil patterns — curl/wget calls in hook scripts to a host not on
   the allowlist.
3. Over-broad tool grants — permission entries in settings.json/settings.local.json
   with no scoping argument (bare tool name, or an argument that is just "*").

No `re` import — banned repo-wide (see ruff.toml). All matching is done with
plain string methods.

Usage: hook-config-audit.py <repo_dir> [--json]
"""
import json
import subprocess
import sys
from pathlib import Path

# Hosts hooks are known to legitimately call. Extend this list, don't remove
# the check, when a new hook needs a new host.
ALLOWED_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "api.github.com",
    "api.anthropic.com",
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "web.archive.org",
    "localhost",
    "127.0.0.1",
}


def extract_host(after_scheme: str) -> str:
    """After 'https://' or 'http://', pull the host up to the next / ? " ' or whitespace."""
    host = after_scheme
    for cut in ("/", "?", '"', "'", " ", "\t", "\n", ")"):
        idx = host.find(cut)
        if idx != -1:
            host = host[:idx]
    return host


def find_urls(line: str):
    urls = []
    for scheme in ("https://", "http://"):
        start = 0
        while True:
            idx = line.find(scheme, start)
            if idx == -1:
                break
            urls.append(extract_host(line[idx + len(scheme):]))
            start = idx + len(scheme)
    return urls


def scan_exfil(hooks_dir: Path):
    findings = []
    if not hooks_dir.is_dir():
        return findings
    for hook_file in sorted(hooks_dir.glob("*.sh")):
        try:
            text = hook_file.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "curl" not in stripped and "wget" not in stripped:
                continue
            for host in find_urls(stripped):
                if host and host not in ALLOWED_HOSTS:
                    findings.append({
                        "file": str(hook_file.name),
                        "line": lineno,
                        "host": host,
                        "text": stripped[:160],
                    })
    return findings


# Tools whose permission syntax takes a command/path argument that can be
# scoped down (e.g. "Bash(git status)"). A bare entry for one of these (no
# parens at all) grants the tool with no scoping whatsoever. Tools not in
# this set (WebSearch, mcp__server__method, etc.) have no argument syntax to
# begin with, so a bare entry for them is the only form that exists — not a
# missed scoping opportunity, and not flagged.
PARAMETRIZED_TOOLS = {"Bash", "Edit", "Write", "Read", "MultiEdit", "WebFetch"}


def parse_permission_entry(entry: str):
    """Return (tool, arg) for a permission string. arg is None if the entry
    has no parens at all (e.g. 'Bash' or 'WebSearch').

    'Bash(git status)' -> ('Bash', 'git status')
    'Bash(*)'           -> ('Bash', '*')
    'Bash'              -> ('Bash', None)
    """
    open_paren = entry.find("(")
    if open_paren == -1 or not entry.endswith(")"):
        return entry, None
    return entry[:open_paren], entry[open_paren + 1:-1]


def scan_permissions(settings_path: Path):
    findings = []
    if not settings_path.is_file():
        return findings
    try:
        data = json.loads(settings_path.read_text())
    except (OSError, json.JSONDecodeError):
        return findings
    perms = data.get("permissions", {})
    for bucket in ("allow", "deny"):
        for entry in perms.get(bucket, []):
            if not isinstance(entry, str):
                continue
            tool, arg = parse_permission_entry(entry)
            unscoped_bare = arg is None and tool in PARAMETRIZED_TOOLS
            unscoped_wildcard = arg is not None and arg.strip() == "*"
            if unscoped_bare or unscoped_wildcard:
                findings.append({
                    "file": settings_path.name,
                    "bucket": bucket,
                    "entry": entry,
                })
    return findings


def scan_secrets(repo: Path):
    scan_pii = repo / ".claude" / "hooks" / "scan-pii.sh"
    if not scan_pii.is_file():
        return {"ran": False, "reason": "scan-pii.sh not installed"}
    # Scan hook files individually (not the whole directory in one call) so a
    # slow/large directory can't blow one shared timeout, and so a timeout on
    # one file doesn't erase results for the rest.
    hooks_dir = repo / ".claude" / "hooks"
    targets = sorted(hooks_dir.glob("*.sh")) if hooks_dir.is_dir() else []
    for name in ("settings.json", "settings.local.json"):
        p = repo / ".claude" / name
        if p.is_file():
            targets.append(p)
    mcp = repo / ".mcp.json"
    if mcp.is_file():
        targets.append(mcp)

    high_severity_total = 0
    incomplete = 0
    ran_clean = True
    details = []
    for target in targets:
        try:
            result = subprocess.run(
                ["bash", str(scan_pii), str(target)],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            # Unresolved, not clean — flag it so it can't be misreported as clean.
            ran_clean = False
            incomplete += 1
            details.append({"target": str(target), "incomplete": True, "error": str(exc)})
            continue
        if result.returncode == 1:
            ran_clean = False
            high_severity_total += 1
            details.append({"target": str(target), "high_severity": True,
                             "output": result.stdout[-2000:]})
    return {"ran": True, "clean": ran_clean, "high_severity_hits": high_severity_total,
            "incomplete": incomplete, "details": details}


def main():
    if len(sys.argv) < 2:
        print("Usage: hook-config-audit.py <repo_dir> [--json]", file=sys.stderr)
        return 2
    repo = Path(sys.argv[1]).resolve()
    as_json = "--json" in sys.argv[2:]

    secrets = scan_secrets(repo)
    exfil = scan_exfil(repo / ".claude" / "hooks")
    permissions = []
    for name in ("settings.json", "settings.local.json"):
        permissions.extend(scan_permissions(repo / ".claude" / name))

    total_findings = ((secrets.get("high_severity_hits", 0) or 0)
                       + (secrets.get("incomplete", 0) or 0)
                       + len(exfil) + len(permissions))
    out = {
        "repo": str(repo),
        "secrets": secrets,
        "exfil": exfil,
        "permissions": permissions,
        "total_findings": total_findings,
    }

    if as_json:
        print(json.dumps(out, indent=2))
    else:
        print(f"hook-config-audit: {total_findings} finding(s) — "
              f"secrets={secrets.get('high_severity_hits', 0)} exfil={len(exfil)} "
              f"permissions={len(permissions)}")
        for f in exfil:
            print(f"  [exfil] {f['file']}:{f['line']} -> {f['host']}")
        for f in permissions:
            print(f"  [permission] {f['file']} {f['bucket']}: {f['entry']}")

    return 1 if total_findings > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
