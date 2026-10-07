#!/usr/bin/env python3
"""SDD Harness Dashboard — starts a local server and opens the dashboard in the browser.

Quickstart (run from anywhere — harness root is resolved from the script path):
    python3 ~/.claude/sdd-harness/scripts/utils/dashboard.py

Or from inside the harness repo:
    cd ~/.claude/sdd-harness
    python3 scripts/utils/dashboard.py

Multi-repo mode (default):
    The dashboard reads ~/.claude/sdd-harness/projects.txt — one absolute repo path per line.
    Every repo listed there gets its own Trust Battery, GitNexus, Workshop, Hooks, and Memory
    tabs in the sidebar.  To add a repo, append its absolute path to projects.txt:

        echo /path/to/your/repo >> ~/.claude/sdd-harness/projects.txt

Single-repo override:
    python3 scripts/utils/dashboard.py --repo /path/to/repo

Options:
    --repo PATH     Show only the given repo (overrides projects.txt)
    --no-open       Start the server but don't launch a browser tab
    --static        Write .dashboard/index.html without starting a server (CI/offline use)
    --port PORT     HTTP port for the local server (default: auto-selected free port)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import secrets
import subprocess
import sys
import threading
from datetime import date, datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen, Request as UrlRequest
from urllib.error import URLError

# ── Constants ─────────────────────────────────────────────────────────────────

def _find_harness_root() -> Path:
    """Locate the harness root regardless of install path or script depth.

    Walks up from __file__ looking for the directory that owns both
    projects.txt and install.sh — the only ancestor that satisfies both is
    the harness root.  Works whether run from the canonical source
    (scripts/utils/) or the installed copy (.claude/scripts/utils/).
    """
    for p in Path(__file__).resolve().parents:
        if (p / "projects.txt").is_file() and (p / "install.sh").is_file():
            return p
    raise RuntimeError(
        f"Cannot locate harness root from {__file__}. "
        "Expected a parent directory containing both projects.txt and install.sh."
    )

HARNESS_DIR    = _find_harness_root()
PROJECTS_FILE  = HARNESS_DIR / "projects.txt"
DASHBOARD_DIR  = HARNESS_DIR / ".dashboard"
OUTPUT_FILE    = DASHBOARD_DIR / "index.html"
ORCH_LOG       = HARNESS_DIR / "logs" / "orchestrator.log"
COMPANION_PORT   = 4569
WORKSHOP_PORT    = 5899
HEADROOM_PORT    = 8787
GN_PORT          = 4747
# `gitnexus serve` ships no web UI — it is the API backend for the hosted app.
GN_WEB_UI        = "https://gitnexus.vercel.app"
GN_PROBE_URL     = f"http://localhost:{GN_PORT}/api/repos"
HEADROOM_SAVINGS = Path.home() / ".headroom" / "proxy_savings.json"

SECTION_DEFS = [
    ("session_health",    "⚡", "Session Health"),
    ("herder",            "🐑", "Herder"),
    ("gitnexus",          "🕸", "GitNexus"),
    ("workshop",          "🔬", "Workshop"),
    ("budget_efficiency", "💰", "Budget & Efficiency"),
    ("automation",        "🤖", "Automation"),
    ("knowledge_base",    "🧠", "Knowledge Base"),
]

# Herder backend. Imported lazily-tolerantly: the dashboard must still render on a
# machine without Herdr installed, showing an install hint instead of a spawn form.
try:
    import herder
    HERDER_IMPORT_ERROR = ""
except Exception as _herder_exc:      # noqa: BLE001 - dashboard must never fail to render
    herder = None
    HERDER_IMPORT_ERROR = str(_herder_exc)

# Per-process CSRF token for the herder endpoints. The dashboard binds 127.0.0.1,
# but "local" is not "safe": any page in any open browser tab can POST to
# localhost. That is tolerable for the fixed-prompt endpoints below and NOT
# tolerable for an endpoint that starts an agent with a caller-supplied prompt.
# The token is minted per process and embedded in the served HTML, so only a page
# actually served by this process can call the herder API.
HERDER_TOKEN = secrets.token_urlsafe(32)

PRICING_HISTORY = DASHBOARD_DIR / "models-pricing-history.json"
PRICING_MAX_AGE = 14 * 86400   # 14-day refresh cadence
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CLAUDE_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
LIVE_CONTEXT_MAX_AGE = 15 * 60   # state older than this is a dead/closed session, not "live"

_MODEL_LABEL = {
    "claude-opus-4-8":           ("Opus 4.8",   "#cba6f7"),
    "claude-opus-4-7":           ("Opus 4.7",   "#cba6f7"),
    "claude-opus-4-6":           ("Opus 4.6",   "#cba6f7"),
    "claude-opus-4-5":           ("Opus 4.5",   "#cba6f7"),
    "claude-sonnet-4-6":         ("Sonnet 4.6", "#89b4fa"),
    "claude-sonnet-4-5":         ("Sonnet 4.5", "#89b4fa"),
    "claude-haiku-4-5-20251001": ("Haiku 4.5",  "#a6e3a1"),
    "claude-haiku-4-5":          ("Haiku 4.5",  "#a6e3a1"),
}

# Providers included in the "what if" cross-provider switcher (ordered for display)
FEATURED_PROVIDERS = [
    "anthropic", "openai", "google", "google-vertex",
    "mistral", "deepseek", "xai", "cohere",
    "amazon-bedrock", "azure", "perplexity", "groq",
]
PROVIDER_DISPLAY = {
    "anthropic":      "Anthropic",
    "openai":         "OpenAI",
    "google":         "Google",
    "google-vertex":  "Google Vertex",
    "mistral":        "Mistral",
    "deepseek":       "DeepSeek",
    "xai":            "xAI (Grok)",
    "cohere":         "Cohere",
    "amazon-bedrock": "Amazon Bedrock",
    "azure":          "Azure",
    "perplexity":     "Perplexity",
    "groq":           "Groq",
}

NOW = datetime.now(timezone.utc)

HOOK_DESCRIPTIONS = {
    "impeccable-detect":      "Scores in-session behavior (0–5) after each tool use; writes session-quality observations",
    "revert-detect":          "Detects git reverts; logs them as trust-drain events in observations",
    "session-start":          "Runs on session start: checks for missed maintenance, loads context",
    "stop":                   "Post-session wrap-up: keep-rate check, memory housekeeping",
    "pre-tool-use-gitnexus":  "Ensures GitNexus graph is indexed before code-analysis tool calls",
    "memory-discipline":      "Gates memory writes: enforces quality/length rules before saving",
    "hook-added-notify":      "Notifies when a new hook file is added to the repo",
    "gbrain-agent-spawn":     "Intercepts agent spawns to inject memory-first lookup pattern",
    "gbrain-memory-write":    "Validates memory writes against GBrain quality patterns",
    "gbrain-external":        "Routes external fetches through GBrain compiled-truth check",
    "compaction-discipline":  "Injects boundary-timing principles before context compaction",
    "scan-pii":               "Scans outgoing data for PII before tool calls",
}

# ── Utilities ─────────────────────────────────────────────────────────────────

def rel_time(ts_str):
    if not ts_str:
        return "unknown"
    try:
        ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        secs = int((NOW - ts).total_seconds())
        if secs < 0:
            return "just now"
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        days = secs // 86400
        return f"{days}d ago"
    except Exception:
        return str(ts_str)

def run_cmd(cmd, cwd=None, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.stdout.strip()
    except Exception:
        return ""

def h(text):
    return html.escape(str(text))

def badge(text, style="default"):
    colours = {
        "ok":      ("#a6e3a1", "rgba(166,227,161,0.15)"),
        "warn":    ("#f9e2af", "rgba(249,226,175,0.15)"),
        "missed":  ("#f38ba8", "rgba(243,139,168,0.15)"),
        "info":    ("#89b4fa", "rgba(137,180,250,0.15)"),
        "active":  ("#a6e3a1", "rgba(166,227,161,0.15)"),
        "inactive":("#6c7086", "rgba(108,112,134,0.15)"),
        "default": ("#a6adc8", "rgba(166,173,200,0.1)"),
    }
    color, bg = colours.get(style, colours["default"])
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:20px;'
            f'font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:0.6px;'
            f'color:{color};background:{bg};border:1px solid {color}55">{h(text)}</span>')

def empty_state(msg):
    return (f'<div style="padding:48px;text-align:center;color:var(--overlay0);'
            f'font-size:13px">{h(msg)}</div>')

def _inner(section_html: str) -> str:
    """Strip outer section-inner wrapper and section-title h2 from a render_* result."""
    s = section_html.strip()
    prefix = '<div class="section-inner">'
    if s.startswith(prefix):
        s = s[len(prefix):]
        idx = s.rfind('</div>')
        if idx != -1:
            s = s[:idx]
    s = _strip_leading_section_title(s)
    return s.strip()

def _strip_leading_section_title(s: str) -> str:
    """Drop a leading `<h2 class="section-title">…</h2>`, if the string opens with one."""
    open_tag, close_tag = '<h2 class="section-title">', '</h2>'
    body = s.lstrip()
    if not body.startswith(open_tag):
        return s
    end = body.find(close_tag, len(open_tag))
    if end == -1:
        return s
    return body[end + len(close_tag):].lstrip()

def _combined_section(title: str, prefix: str, subs: list) -> str:
    """Combine multiple render_* outputs into a single tabbed section.
    subs: list of (name, icon, label, html) tuples.
    """
    names = [s[0] for s in subs]
    names_js = "['" + "','".join(names) + "']"
    tabs = ''
    panes = ''
    for i, (name, icon, label, html) in enumerate(subs):
        active = ' active' if i == 0 else ''
        tabs += (f'<button id="{prefix}-tab-{name}" class="sub-tab{active}" '
                 f'onclick="switchTab(\'{prefix}\',\'{name}\',{names_js})">'
                 f'{icon} {h(label)}</button>')
        display = 'block' if i == 0 else 'none'
        panes += f'<div id="{prefix}-pane-{name}" style="display:{display}">{_inner(html)}</div>'
    return (f'<div class="section-inner">'
            f'<h2 class="section-title">{h(title)}</h2>'
            f'<div class="sub-tab-bar">{tabs}</div>'
            f'{panes}'
            f'</div>')

def section_desc(text, *, icon="ℹ", color="var(--blue)"):
    return (f'<div style="background:rgba(49,50,68,0.5);border-left:3px solid {color}44;'
            f'border-radius:0 6px 6px 0;padding:8px 12px;margin-bottom:16px;'
            f'font-size:11px;color:var(--subtext0);line-height:1.6">'
            f'<span style="color:{color};margin-right:6px">{icon}</span>{text}</div>')

# ── Minimal Markdown → HTML ────────────────────────────────────────────────────

def _replace_spans(text, opener, closer, render, forbid=None):
    """Rewrite every `opener…closer` span through `render(inner)`, left to right.

    A span whose body is empty, unterminated, or contains `forbid` is left as
    literal text and scanning resumes one character past the opener.
    """
    out = []
    i = 0
    while i < len(text):
        start = text.find(opener, i)
        if start == -1:
            break
        inner_start = start + len(opener)
        end = text.find(closer, inner_start)
        if end == -1:
            break
        inner = text[inner_start:end]
        if not inner or (forbid is not None and forbid in inner):
            out.append(text[i:start + 1])
            i = start + 1
            continue
        out.append(text[i:start])
        out.append(render(inner))
        i = end + len(closer)
    out.append(text[i:])
    return "".join(out)

def _render_md_links(text):
    """Rewrite `[label](url)` into an anchor. Anything malformed stays literal."""
    out = []
    i = 0
    while i < len(text):
        start = text.find("[", i)
        if start == -1:
            break
        label_end = text.find("]", start + 1)
        if label_end == -1:
            break
        label = text[start + 1:label_end]
        url_end = text.find(")", label_end + 2)
        url = text[label_end + 2:url_end] if url_end != -1 else ""
        if not label or not url or text[label_end + 1:label_end + 2] != "(":
            out.append(text[i:start + 1])
            i = start + 1
            continue
        out.append(text[i:start])
        out.append(f'<a href="{url}" style="color:var(--mauve)">{label}</a>')
        i = url_end + 1
    out.append(text[i:])
    return "".join(out)

def inline_md(text):
    text = _replace_spans(
        text, "`", "`",
        lambda inner: (f'<code style="background:var(--surface0);padding:1px 5px;'
                       f'border-radius:3px;font-family:monospace;font-size:11px">'
                       f'{h(inner)}</code>'),
        forbid="`",
    )
    # Bold before italic: the two-star span has to be consumed first, or the
    # single-star pass would claim its opening delimiter.
    text = _replace_spans(text, "**", "**",
                          lambda inner: f'<strong style="color:var(--text)">{inner}</strong>',
                          forbid="*")
    text = _replace_spans(text, "*", "*", lambda inner: f'<em>{inner}</em>', forbid="*")
    return _render_md_links(text)

def _is_table_rule(line: str) -> bool:
    """True for a markdown table separator row: `|---|:--:|` — pipes, dashes,
    colons and spaces only, with at least one character between the outer pipes."""
    return (len(line) >= 3
            and line.startswith("|")
            and line.endswith("|")
            and set(line) <= set("|-: "))

def mini_md(text):
    lines = text.split("\n")
    out = []
    in_ul = False
    in_table = False
    table_header_done = False

    def flush_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    def flush_table():
        nonlocal in_table, table_header_done
        if in_table:
            out.append("</tbody></table>")
            in_table = False
            table_header_done = False

    td_s = "padding:5px 10px;border:1px solid var(--surface0);font-size:12px"
    th_s = td_s + ";font-weight:600;color:var(--mauve);background:var(--surface0)"

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#### "):
            flush_ul(); flush_table()
            out.append(f'<h4 style="color:var(--text);margin:12px 0 4px">{inline_md(stripped[5:])}</h4>')
        elif stripped.startswith("### "):
            flush_ul(); flush_table()
            out.append(f'<h3 style="color:var(--mauve);margin:16px 0 6px;font-size:14px">{inline_md(stripped[4:])}</h3>')
        elif stripped.startswith("## "):
            flush_ul(); flush_table()
            out.append(f'<h2 style="color:var(--text);margin:20px 0 8px;font-size:16px;'
                       f'border-bottom:1px solid var(--surface0);padding-bottom:6px">'
                       f'{inline_md(stripped[3:])}</h2>')
        elif stripped.startswith("# "):
            flush_ul(); flush_table()
            out.append(f'<h1 style="color:var(--text);margin:0 0 12px;font-size:18px">'
                       f'{inline_md(stripped[2:])}</h1>')
        elif _is_table_rule(stripped):
            # Table separator row — mark header as done
            table_header_done = True
        elif stripped.startswith("|") and stripped.endswith("|"):
            flush_ul()
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            if not in_table:
                out.append('<table style="width:100%;border-collapse:collapse;margin:8px 0">'
                           '<tbody>')
                in_table = True
                out.append("<tr>" + "".join(
                    f"<th style='{th_s}'>{inline_md(c)}</th>" for c in cells
                ) + "</tr>")
            else:
                out.append("<tr>" + "".join(
                    f"<td style='{td_s}'>{inline_md(c)}</td>" for c in cells
                ) + "</tr>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            flush_table()
            if not in_ul:
                out.append('<ul style="margin:4px 0;padding-left:20px;color:var(--subtext1)">')
                in_ul = True
            out.append(f'<li style="margin:2px 0;font-size:12px">{inline_md(stripped[2:])}</li>')
        elif stripped in ("---", "***", "___"):
            flush_ul(); flush_table()
            out.append('<hr style="border:none;border-top:1px solid var(--surface0);margin:12px 0">')
        elif stripped == "":
            flush_ul(); flush_table()
            out.append('<div style="height:6px"></div>')
        else:
            flush_ul(); flush_table()
            out.append(f'<p style="margin:3px 0;color:var(--subtext1);font-size:12px">'
                       f'{inline_md(stripped)}</p>')

    flush_ul()
    flush_table()
    return "\n".join(out)

# ── Data Parsers ──────────────────────────────────────────────────────────────

def discover_repos():
    if not PROJECTS_FILE.exists():
        return []
    repos = []
    for line in PROJECTS_FILE.read_text().splitlines():
        p = Path(line.strip())
        if line.strip() and p.exists():
            repos.append(p)
    return repos

def parse_trust_scores(repo):
    f = repo / ".claude" / "memory" / "trust-score.jsonl"
    if not f.exists():
        return []
    records = []
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    records.sort(key=lambda r: r.get("ts", ""))
    return records[-30:]

def parse_observations(repo):
    """Split `- YYYY-MM-DD [tag]: text` lines by structure, not by pattern match.

    Prose is parsed only for *display*. Every number the dashboard computes comes
    from metrics.jsonl instead — see parse_metrics.
    """
    f = repo / ".claude" / "memory" / "observations.md"
    if not f.exists():
        return {}
    result = {}
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line.startswith("- ") or "[" not in line or "]: " not in line:
            continue
        date_str, _, remainder = line[2:].partition(" [")
        try:
            date.fromisoformat(date_str)
        except ValueError:
            continue
        tags_raw, _, text = remainder.partition("]: ")
        if not text:
            continue
        for tag in (t.strip() for t in tags_raw.split(",")):
            result.setdefault(tag, []).append((date_str, text))
    return result

def parse_metrics(repo):
    """Read .claude/memory/metrics.jsonl — the machine channel for measurements.

    One JSON object per line, written by scripts/session/record_metric.py. A
    malformed line raises rather than being skipped: a metrics file that is
    quietly half-read produces a plausible-looking wrong number, which is the
    exact failure this file exists to prevent.
    """
    f = repo / ".claude" / "memory" / "metrics.jsonl"
    if not f.exists():
        return {}
    result = {}
    for lineno, line in enumerate(f.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{f}:{lineno} is not valid JSON: {e}") from e
        result.setdefault(rec["metric"], []).append(rec)
    for records in result.values():
        records.sort(key=lambda r: r["date"])
    return result

def list_hooks(repo):
    hooks_dir = repo / ".claude" / "hooks"
    if not hooks_dir.exists():
        return []
    return sorted(f.name for f in hooks_dir.glob("*.sh"))

def read_last_routine_run(repo):
    f = repo / ".claude" / "memory" / ".last-routine-run"
    if not f.exists():
        return None
    try:
        ts_str = f.read_text().strip()
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def gitnexus_stats(repo):
    gn_dir  = repo / ".gitnexus"
    meta_f  = gn_dir / "meta.json"
    result  = {"available": gn_dir.exists()}
    if not gn_dir.exists():
        return result
    try:
        mtime = gn_dir.stat().st_mtime
        age   = NOW.timestamp() - mtime
        dt    = datetime.fromtimestamp(mtime, tz=timezone.utc)
        result["indexed_ago"] = rel_time(dt.isoformat())
        result["stale"]       = age > 86400
        result["very_stale"]  = age > 259200
    except Exception:
        pass
    if meta_f.exists():
        try:
            meta = json.loads(meta_f.read_text())
            s = meta.get("stats", {})
            result["symbols"]   = s.get("nodes", 0)
            result["clusters"]  = s.get("communities", 0)
            result["processes"] = s.get("processes", 0)
            result["files"]     = s.get("files", 0)
            # Use indexedAt from meta for accuracy
            if "indexedAt" in meta:
                result["indexed_ago"] = rel_time(meta["indexedAt"])
                age2 = (NOW - datetime.fromisoformat(
                    meta["indexedAt"].replace("Z", "+00:00"))).total_seconds()
                result["stale"]      = age2 > 86400
                result["very_stale"] = age2 > 259200
        except Exception:
            pass
    return result

def _tool_bin_dirs():
    """Directories a globally-installed CLI may live in, discovered not guessed.

    The Python twin of scripts/lib/tool-paths.sh. This used to list the two
    Homebrew bin directories literally — one is Apple-Silicon-only, the other
    Intel-only, so the list was right on one Mac and wrong on the other, and wrong
    on any machine with a custom prefix. Ask each package manager where it actually
    installs instead; every remaining path is $HOME- or env-derived.
    """
    import subprocess

    dirs = []
    for argv in (["uv", "tool", "dir", "--bin"], ["brew", "--prefix"]):
        try:
            out = subprocess.run(
                argv, capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode != 0:
            continue
        value = out.stdout.strip()
        if not value:
            continue
        # `brew --prefix` reports the prefix; binaries live under its bin/.
        dirs.append(os.path.join(value, "bin") if argv[0] == "brew" else value)

    home = os.path.expanduser("~")
    dirs.append(os.path.join(os.environ.get("RAINDROP_HOME", os.path.join(home, ".raindrop")), "bin"))
    dirs.append(os.path.join(os.environ.get("CARGO_HOME", os.path.join(home, ".cargo")), "bin"))
    dirs.append(os.environ.get("XDG_BIN_HOME", os.path.join(home, ".local", "bin")))

    seen, unique = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def find_global_tool(name):
    """Absolute path to a globally-installed CLI, or None.

    shutil.which only searches $PATH, and the PATH a hook or IDE hands us is not
    the PATH of an interactive login shell — an installed, running tool reads as
    absent. Fall back to the discovered install directories.
    """
    import shutil

    hit = shutil.which(name)
    if hit:
        return hit
    for d in _tool_bin_dirs():
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def workshop_stats(repo):
    import subprocess
    installed = find_global_tool("raindrop") is not None
    try:
        result = subprocess.run(
            ["grep", "-rq", "raindrop.begin", str(repo),
             "--include=*.py", "--include=*.ts"],
            capture_output=True, timeout=5
        )
        instrumented = result.returncode == 0
    except Exception:
        instrumented = False
    return {"installed": installed, "instrumented": instrumented}

def parse_orchestrator_log():
    """Parse ~/.claude/sdd-harness/logs/orchestrator.log.

    Two line shapes:
      '<ts> <repo> exit=<N> duration=<N>s'                  (daily-maintenance)
      '<ts> <repo> <runner-name> exit=<N> duration=<N>s'    (macro-eval, skill-curator, harness-health)
    Plus harness-level drift lines '<ts> harness: drift review exit=<N>' (no duration).
    """
    if not ORCH_LOG.exists():
        return []
    runs = []
    for line in ORCH_LOG.read_text().splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue

        # Trailing shape is always `exit=<N> duration=<N>s`, or `exit=<N>` for drift.
        duration = _log_int(fields[-1], "duration=", "s")
        exit_code = _log_int(fields[-2] if duration is not None else fields[-1], "exit=")
        if exit_code is None:
            continue

        ts = fields[0]
        middle = fields[1:-2] if duration is not None else fields[1:-1]

        if middle[:3] == ["harness:", "drift", "review"] and duration is None:
            runs.append({
                "ts": ts, "path": str(HARNESS_DIR), "runner": "drift-review",
                "exit": exit_code, "duration": 0,
            })
            continue

        if duration is None or not middle:
            continue
        if len(middle) >= 2:
            runner, path = middle[-1], " ".join(middle[:-1])
        else:
            runner, path = "daily-maintenance", middle[0]
        runs.append({
            "ts": ts, "path": path, "runner": runner,
            "exit": exit_code, "duration": duration,
        })
    return runs


def _log_int(field: str, prefix: str, suffix: str = "") -> int | None:
    """Read `<prefix><digits><suffix>` from one whitespace-delimited log field."""
    if not field.startswith(prefix) or not field.endswith(suffix):
        return None
    digits = field[len(prefix):len(field) - len(suffix) if suffix else None]
    return int(digits) if digits.isdigit() else None


# ── Scheduled Tasks ───────────────────────────────────────────────────────────

# Source of truth for the Scheduled Tasks dashboard tab. To add a routine here:
# the orchestrator already logs it under `runner_log_token`; just append an entry
# and the dashboard picks it up.
def _scheduled_task_registry(repo_dir=None):
    """repo_dir: base directory for scope="per-repo" entries — the routine's own
    state/artifact files always live under the repo it actually ran in. Defaults
    to HARNESS_DIR for backward-compat callers that want the harness's own view.
    scope="harness" entries always resolve against HARNESS_DIR regardless, since
    those routines only ever run against the harness repo itself.
    """
    base = Path(repo_dir) if repo_dir else HARNESS_DIR
    return [
        {
            "key":               "daily-maintenance",
            "name":              "Daily Maintenance",
            "runner_log_token":  "daily-maintenance",
            "state_file":        base / ".claude" / "memory" / ".last-routine-run",
            "artifact_glob":     str(base / ".claude" / "memory" / "daily" / "*-brief.md"),
            "artifact_label":    ".claude/memory/daily/<date>-brief.md",
            "schedule_human":    "Daily at 18:00 (local)",
            "interval_seconds":  86400,
            "scope":             "per-repo",
            "what_it_does":      "Judge previous day, reflect drains→memory, housekeep, trust-score, write morning brief",
        },
        {
            "key":               "macro-eval",
            "name":              "Macro-Eval Sweep",
            "runner_log_token":  "macro-eval",
            "state_file":        base / ".claude" / "memory" / ".last-macro-eval-run",
            "artifact_glob":     str(base / ".claude" / "reports" / "macro-evals" / "*.md"),
            "artifact_label":    ".claude/reports/macro-evals/<date>.md",
            "schedule_human":    "Twice weekly (MIN_GAP_DAYS=3)",
            "interval_seconds":  3 * 86400,
            "scope":             "per-repo",
            "what_it_does":      "Cluster Raindrop trace failures from last 4 days, impact-rank, post annotations",
        },
        {
            "key":               "skill-curator",
            "name":              "Weekly Skill-Curator",
            "runner_log_token":  "skill-curator",
            "state_file":        HARNESS_DIR / ".claude" / "memory" / ".last-skill-curator-run",
            "artifact_glob":     str(HARNESS_DIR / "reports" / "skill-curation-report.md"),
            "artifact_label":    "reports/skill-curation-report.md",
            "schedule_human":    "Weekly (MIN_GAP_DAYS=7)",
            "interval_seconds":  7 * 86400,
            "scope":             "harness",
            "what_it_does":      "Score all skills (SkillOS rubric), flag low-quality/duplicates, audit description budgets",
        },
        {
            "key":               "harness-health",
            "name":              "Bi-Weekly Harness Health",
            "runner_log_token":  "harness-health",
            "state_file":        HARNESS_DIR / ".claude" / "memory" / ".last-harness-health-run",
            "artifact_glob":     str(HARNESS_DIR / "docs" / "claudemd-review-report.md"),
            "artifact_label":    "docs/claudemd-review-report.md",
            "schedule_human":    "Bi-weekly (MIN_GAP_DAYS=13)",
            "interval_seconds":  13 * 86400,
            "scope":             "harness",
            "what_it_does":      "Review every repo's CLAUDE.md for drift; iteratively repair low-quality skills",
        },
        {
            "key":               "drift-review",
            "name":              "Wednesday Drift Review",
            "runner_log_token":  "drift-review",
            "state_file":        HARNESS_DIR / ".last-drift-review",
            "artifact_glob":     str(HARNESS_DIR / "docs" / "drift-review-report.md"),
            "artifact_label":    "docs/drift-review-report.md",
            "schedule_human":    "Every Wednesday",
            "interval_seconds":  7 * 86400,
            "scope":             "harness",
            "what_it_does":      "Sweep the harness for structural drift; auto-fix what it can",
        },
        {
            "key":               "security-report",
            "name":              "Daily Security Scan",
            "runner_log_token":  "security-report",
            "state_file":        None,  # per-repo state; dashboard shows last log entry
            "artifact_glob":     str(base / ".claude" / "reports" / "security" / "*.md"),
            "artifact_label":    ".claude/reports/security/<date>-security-report.md",
            "schedule_human":    "Daily (MIN_GAP_DAYS=1)",
            "interval_seconds":  86400,
            "scope":             "per-repo",
            "what_it_does":      "Static security scan of recent git changes; flags OWASP patterns, secrets, injection sinks",
        },
        {
            "key":               "startup-payload",
            "name":              "Startup Payload Audit",
            "runner_log_token":  "startup-payload",
            "state_file":        base / ".claude" / "memory" / ".last-startup-payload-audit",
            "artifact_glob":     str(base / ".claude" / "reports" / "context" / "startup-payload.json"),
            "artifact_label":    ".claude/reports/context/startup-payload.json",
            "schedule_human":    "Daily (deterministic, no LLM)",
            "interval_seconds":  86400,
            "scope":             "per-repo",
            "what_it_does":      "Measures fixed per-session token tax (CLAUDE.md + @imports + rules + auto-MEMORY.md); flags over-budget, stale files, ghost refs",
        },
        {
            "key":               "tool-failure-review",
            "name":              "Tool-Failure Review",
            "runner_log_token":  "tool-failure-review",
            "state_file":        base / ".claude" / "memory" / ".last-tool-failure-review",
            "artifact_glob":     str(base / ".claude" / "memory" / "tool-failures.jsonl"),
            "artifact_label":    ".claude/memory/tool-failures.jsonl (input ledger — promotes findings into meta/patterns.md, no dedicated report)",
            "schedule_human":    "~Twice weekly (MIN_GAP_DAYS=3)",
            "interval_seconds":  3 * 86400,
            "scope":             "per-repo",
            "what_it_does":      "Promotes recurring Bash/MCP tool failures from the ledger into memory patterns",
        },
        {
            "key":               "code-review-learning",
            "name":              "Code-Review Learning Sweep",
            "runner_log_token":  "code-review-learning",
            "state_file":        base / ".claude" / "memory" / ".last-code-review-learning-run",
            "artifact_glob":     str(base / "docs" / "code-review-learning-report.md"),
            "artifact_label":    "docs/code-review-learning-report.md",
            "schedule_human":    "Weekly (MIN_GAP_DAYS=7)",
            "interval_seconds":  7 * 86400,
            "scope":             "per-repo",
            "what_it_does":      "Compares pr-babysit reviews against real human review activity on merged PRs; promotes low-risk findings",
        },
        {
            "key":               "daily-briefing",
            "name":              "Daily Briefing Synthesis",
            "runner_log_token":  "daily-briefing",
            "state_file":        base / ".claude" / "memory" / ".last-daily-briefing-run",
            "artifact_glob":     str(base / ".claude" / "reports" / "daily-briefings" / "*.md"),
            "artifact_label":    ".claude/reports/daily-briefings/<date>.md",
            "schedule_human":    "Daily (MIN_GAP_DAYS=1)",
            "interval_seconds":  86400,
            "scope":             "per-repo",
            "what_it_does":      "Synthesizes prioritized status digest from projects.md/people.md + connected live sources; no-ops until ledger is bootstrapped",
        },
    ]


def _read_state_ts(path):
    """Read a state file. Content is either ISO timestamp, YYYY-MM-DD, or ISO-week (drift)."""
    try:
        raw = Path(path).read_text().strip()
    except Exception:
        return None
    if not raw:
        return None
    # Drift state uses ISO week '2026-W22' — convert to that week's Wednesday
    year_part, sep, week_part = raw.partition("-W")
    if sep and len(year_part) == 4 and len(week_part) == 2 \
            and year_part.isdigit() and week_part.isdigit():
        try:
            dt = datetime.fromisocalendar(int(year_part), int(week_part), 3)  # 3 = Wednesday
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        # Fall back to state file's mtime for badly-formatted contents
        try:
            return datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc)
        except Exception:
            return None


def _newest_artifact(glob_pattern):
    from glob import glob
    matches = glob(glob_pattern)
    if not matches:
        return None
    try:
        return max(matches, key=lambda p: os.path.getmtime(p))
    except Exception:
        return None


_SUMMARY_HEADINGS = ("summary", "tldr", "tl;dr", "overview", "findings")

def _is_summary_heading(line: str) -> bool:
    """True for `## Summary`, `### TL;DR`, `## Findings` and friends (any depth ≥ 2)."""
    stripped = line.lstrip()
    depth = len(stripped) - len(stripped.lstrip("#"))
    if depth < 2:
        return False
    rest = stripped[depth:]
    if rest[:1] not in (" ", "\t"):
        return False
    first_word = rest.split(maxsplit=1)[0].lower() if rest.split() else ""
    return first_word.rstrip(":") in _SUMMARY_HEADINGS

def _escape_script_close(payload: str) -> str:
    """Neutralise `</script>` in any casing so embedded JSON cannot close the tag.

    Security-relevant: a repo name or observation containing `</script>` would
    otherwise break out of the surrounding <script> block.
    """
    needle = "</script>"
    haystack = payload.lower()
    out = []
    i = 0
    while True:
        found = haystack.find(needle, i)
        if found == -1:
            break
        out.append(payload[i:found])
        out.append("<\\/" + payload[found + 2:found + len(needle)])
        i = found + len(needle)
    out.append(payload[i:])
    return "".join(out)

def _split_sweep_sections(text):
    """Yield (date, body) for each `## Sweep — YYYY-MM-DD` block of a learning report.

    Any `## Sweep — ` line ends the previous body; only one carrying a valid date
    opens a new section.
    """
    marker = "## Sweep — "
    lines = text.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if ln.startswith(marker)]
    for n, i in enumerate(starts):
        day = lines[i][len(marker):].strip()
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        yield day, "".join(lines[i + 1:end])

def _is_numbered_bullet(line: str) -> bool:
    """`- #12` or `- 7` — a bullet naming a numbered item."""
    if not line.startswith("-"):
        return False
    rest = line[1:].lstrip()
    if rest.startswith("#"):
        rest = rest[1:]
    return rest[:1].isdigit()

def _extract_headline(md_text, max_chars=600):
    """Pull a human-readable summary out of a routine's report markdown.

    Priority: explicit '## Summary'/'## TL;DR' section, then first non-heading
    paragraph in the document.
    """
    if not md_text:
        return ""
    lines = md_text.splitlines()

    def collect_block(start_idx):
        block = []
        for line in lines[start_idx:]:
            s = line.strip()
            if s.startswith("#"):
                break
            if not s and block:
                break
            if s:
                block.append(s)
        return " ".join(block).strip()

    for i, line in enumerate(lines):
        if _is_summary_heading(line):
            text = collect_block(i + 1)
            if text:
                return text[:max_chars] + ("…" if len(text) > max_chars else "")

    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("---"):
            text = collect_block(i)
            if text:
                return text[:max_chars] + ("…" if len(text) > max_chars else "")
    return ""


def _diff_against_snapshot(key, artifact_path):
    """Compare artifact against the previous-run snapshot.

    Snapshot lives at .dashboard/memory-snapshots/<key>.last.md. Returns
    (added, removed, snapshot_exists). After computing the diff, refresh the
    snapshot ONLY if the artifact is newer than the snapshot — that way the
    diff captures what the most recent run changed (not what subsequent
    dashboard renders see).
    """
    snap_dir = DASHBOARD_DIR / "memory-snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap = snap_dir / f"{key}.last.md"
    try:
        cur_text = Path(artifact_path).read_text(errors="replace")
        cur_mtime = os.path.getmtime(artifact_path)
    except Exception:
        return (0, 0, False)

    if not snap.exists():
        # First time we've seen this artifact — seed snapshot, no diff to show
        try:
            snap.write_text(cur_text)
            os.utime(snap, (cur_mtime, cur_mtime))
        except Exception:
            pass
        return (0, 0, False)

    try:
        prev_text  = snap.read_text(errors="replace")
        snap_mtime = os.path.getmtime(snap)
    except Exception:
        return (0, 0, False)

    import difflib
    added = removed = 0
    for ln in difflib.unified_diff(prev_text.splitlines(), cur_text.splitlines(), lineterm=""):
        if ln.startswith("+++") or ln.startswith("---") or ln.startswith("@@"):
            continue
        if ln.startswith("+"):
            added += 1
        elif ln.startswith("-"):
            removed += 1

    # Refresh snapshot only when artifact is newer than snapshot (a fresh run happened)
    if cur_mtime > snap_mtime + 1:  # 1s tolerance for FS timestamp noise
        try:
            snap.write_text(cur_text)
            os.utime(snap, (cur_mtime, cur_mtime))
        except Exception:
            pass

    return (added, removed, True)


def _detect_os_scheduler():
    """Return scheduler status: {installed, kind, next_fire_iso, last_stdout_ts, last_exit}."""
    result = {
        "installed":       False,
        "kind":            "—",
        "next_fire_iso":   None,
        "last_stdout_ts":  None,
        "last_exit":       None,
        "install_hint":    "",
    }
    plat = sys.platform
    if plat == "darwin":
        result["kind"] = "launchd (com.sdd.daily-orchestrator)"
        result["install_hint"] = "bash scripts/setup-mac-orchestrator.sh"
        out = run_cmd(["launchctl", "list", "com.sdd.daily-orchestrator"])
        if out:
            result["installed"] = True
            for line in out.splitlines():
                # launchctl prints `"LastExitStatus" = 0;`
                key, sep, value = line.partition("=")
                if sep and key.strip() == '"LastExitStatus"':
                    digits = value.strip().rstrip(";").strip()
                    if digits.isdigit():
                        result["last_exit"] = int(digits)
    elif plat.startswith("linux"):
        # WSL has schtasks.exe; pure Linux uses cron
        out = run_cmd(["which", "schtasks.exe"])
        if out:
            result["kind"] = 'schtasks ("SDD Daily Orchestrator")'
            result["install_hint"] = "bash scripts/setup-global-orchestrator.sh"
            probe = run_cmd(["schtasks.exe", "/Query", "/TN", "SDD Daily Orchestrator"])
            result["installed"] = bool(probe)
        else:
            result["kind"] = "crontab"
            result["install_hint"] = "bash scripts/setup-linux-orchestrator.sh"
            ct = run_cmd(["crontab", "-l"])
            result["installed"] = "sdd-daily-orchestrator" in ct or "daily-orchestrator.sh" in ct

    # Compute next 18:00 local fire time (the orchestrator's hardcoded schedule)
    local_now = datetime.now()
    nxt = local_now.replace(hour=18, minute=0, second=0, microsecond=0)
    if nxt <= local_now:
        nxt = nxt + timedelta(days=1)
    result["next_fire_iso"] = nxt.isoformat()

    # Surface most recent stdout activity from the launchd-managed log
    stdout_log = HARNESS_DIR / "logs" / "orchestrator.stdout.log"
    if stdout_log.exists():
        try:
            dt = datetime.fromtimestamp(stdout_log.stat().st_mtime, tz=timezone.utc)
            result["last_stdout_ts"] = dt.isoformat()
        except Exception:
            pass
    return result


def parse_scheduled_tasks(repo_dir=None):
    runs = parse_orchestrator_log()
    # Group by (runner, repo path) — not just runner — so a per-repo-scoped
    # routine only ever surfaces runs from the repo actually being viewed,
    # instead of whichever registered repo happened to run most recently.
    runs_by_runner_repo = {}
    for r in runs:
        runs_by_runner_repo.setdefault((r["runner"], r["path"]), []).append(r)
    for v in runs_by_runner_repo.values():
        v.sort(key=lambda x: x["ts"], reverse=True)

    target_repo = str(Path(repo_dir)) if repo_dir else str(HARNESS_DIR)

    out = []
    for spec in _scheduled_task_registry(repo_dir):
        # scope="harness" routines (skill-curator, harness-health, drift-review)
        # only ever run against the harness repo, regardless of which repo's
        # dashboard is currently being rendered.
        scope_repo = target_repo if spec["scope"] == "per-repo" else str(HARNESS_DIR)
        last_ts = _read_state_ts(spec["state_file"])
        recent  = runs_by_runner_repo.get((spec["runner_log_token"], scope_repo), [])
        # Prefer most recent entry that actually did work (duration > 0); fall back to any.
        # Guard-hit 0s entries from other repos can otherwise mask real runs.
        last_run_record = next((r for r in recent if r["duration"] > 0), recent[0] if recent else None)
        if last_run_record and not last_ts:
            try:
                last_ts = datetime.fromisoformat(
                    last_run_record["ts"].replace("Z", "+00:00"))
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
            except Exception:
                last_ts = None

        # Health classification: overdue beyond interval × 1.25 is missed
        miss_status  = "ok"
        overdue_secs = 0
        if last_ts:
            elapsed      = (NOW - last_ts).total_seconds()
            overdue_secs = max(0.0, elapsed - spec["interval_seconds"])
            if overdue_secs > spec["interval_seconds"] * 0.25:
                miss_status = "missed"
            elif overdue_secs > 0:
                miss_status = "warn"
        else:
            # Never run AND no log entries → warn (but skip warn if very recently installed)
            miss_status = "warn"

        artifact_path = _newest_artifact(spec["artifact_glob"])
        artifact_info = None
        diff_added = diff_removed = 0
        headline = ""
        if artifact_path:
            try:
                st = os.stat(artifact_path)
                artifact_info = {
                    "path":     artifact_path,
                    "size_kb":  st.st_size / 1024.0,
                    "mtime":    datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                }
            except Exception:
                pass
            diff_added, diff_removed, _ = _diff_against_snapshot(spec["key"], artifact_path)
            try:
                headline = _extract_headline(Path(artifact_path).read_text(errors="replace"))
            except Exception:
                headline = ""

        # Recent-runs strip — one entry per calendar day (prefer non-0s run for that day).
        # Without dedup the same 18:xx sweep produces 4 identical dots (one per repo).
        history = []
        seen_days: set = set()
        for rec in recent:
            day = rec["ts"][:10]
            if day in seen_days:
                continue
            seen_days.add(day)
            history.append({
                "ts":       rec["ts"],
                "repo":     Path(rec["path"]).name if rec["path"] else "—",
                "exit":     rec["exit"],
                "duration": rec["duration"],
            })
            if len(history) >= 5:
                break

        out.append({
            "key":             spec["key"],
            "name":            spec["name"],
            "scope":           spec["scope"],
            "schedule_human":  spec["schedule_human"],
            "what_it_does":    spec["what_it_does"],
            "last_run_ts_iso": last_ts.isoformat() if last_ts else None,
            "last_run_rel":    rel_time(last_ts.isoformat()) if last_ts else "never",
            "last_exit":       last_run_record["exit"] if last_run_record else None,
            "last_duration":   last_run_record["duration"] if last_run_record else None,
            "miss_status":     miss_status,
            "overdue_secs":    overdue_secs,
            "artifact":        artifact_info,
            "artifact_label":  spec["artifact_label"],
            "diff_added":      diff_added,
            "diff_removed":    diff_removed,
            "headline":        headline,
            "history":         history,
        })

    # Order: missed/warn first, then by health then alphabetical
    severity = {"missed": 0, "warn": 1, "ok": 2}
    out.sort(key=lambda r: (severity.get(r["miss_status"], 3), r["name"]))
    return out

def parse_session_history(repo):
    """Read .claude/memory/.session-history — list of ISO timestamps, one per session end."""
    f = repo / ".claude" / "memory" / ".session-history"
    if not f.exists():
        return []
    sessions = []
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            dt = datetime.fromisoformat(line.replace("Z", "+00:00"))
            sessions.append(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
        except Exception:
            continue
    return sorted(sessions)

def read_skill_report():
    report = HARNESS_DIR / "reports" / "skill-curation-report.md"
    if not report.exists():
        return None, None
    mtime = datetime.fromtimestamp(report.stat().st_mtime, tz=timezone.utc)
    return report.read_text(), rel_time(mtime.isoformat())

SKILL_PROPOSAL_PATH = HARNESS_DIR / ".claude" / "memory" / ".skill-curator-proposal.md"

def read_skill_proposal():
    if not SKILL_PROPOSAL_PATH.exists():
        return None, None
    mtime = datetime.fromtimestamp(SKILL_PROPOSAL_PATH.stat().st_mtime, tz=timezone.utc)
    return SKILL_PROPOSAL_PATH.read_text(), rel_time(mtime.isoformat())

def git_log_memory(repo):
    out = run_cmd(
        ["git", "log", "--format=%H|%cr|%s", "-30", "--", ".claude/memory/"],
        cwd=str(repo)
    )
    entries = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            entries.append({"hash": parts[0], "rel": parts[1], "subject": parts[2]})
    return entries or scan_memory_files(repo)

def git_log_harness_memory():
    claude_dir = Path.home() / ".claude"
    if not (claude_dir / ".git").exists():
        return []
    out = run_cmd(
        ["git", "log", "--format=%H|%cr|%s", "-30"],
        cwd=str(claude_dir)
    )
    entries = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            entries.append({"hash": parts[0], "rel": parts[1], "subject": parts[2]})
    return entries

def scan_memory_files(repo):
    """Fallback when .claude/memory is gitignored: return files sorted by mtime."""
    mem_dir = repo / ".claude" / "memory"
    if not mem_dir.exists():
        return []
    cutoff = NOW.timestamp() - 45 * 86400
    tmp = []
    for f in mem_dir.iterdir():
        if f.is_file() and not f.name.startswith("."):
            mtime = f.stat().st_mtime
            if mtime > cutoff:
                dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
                tmp.append((mtime, {
                    "hash": "", "rel": rel_time(dt.isoformat()),
                    "subject": f"[updated] {f.name}",
                }))
    tmp.sort(key=lambda pair: pair[0], reverse=True)
    return [e for _, e in tmp[:30]]

def read_memory_file_cards(repo):
    """Read .claude/memory/*.md and diff against yesterday's dated snapshot."""
    import difflib
    mem_dir  = repo / ".claude" / "memory"
    snap_dir = DASHBOARD_DIR / "memory-snapshots" / repo.name
    if not mem_dir.exists():
        return []

    today_str     = NOW.strftime("%Y-%m-%d")
    yesterday_str = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
    today_dir     = snap_dir / today_str
    yesterday_dir = snap_dir / yesterday_str
    today_dir.mkdir(parents=True, exist_ok=True)

    priority   = ["hot-memory.md", "observations.md", "entities.md", "patterns.md"]
    candidates = list(mem_dir.glob("*.md"))
    meta_patterns = mem_dir / "meta" / "patterns.md"
    if meta_patterns.is_file():
        candidates.append(meta_patterns)
    all_files = sorted(candidates, key=lambda f: (
        priority.index(f.name) if f.name in priority else len(priority), f.name
    ))
    cards = []
    for f in all_files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            mtime   = f.stat().st_mtime
            dt      = datetime.fromtimestamp(mtime, tz=timezone.utc)

            # Always seed today's snapshot on first dashboard load of the day
            today_snap = today_dir / f.name
            if not today_snap.exists():
                today_snap.write_text(content, encoding="utf-8")

            diff_lines   = None
            diff_summary = None
            yesterday_snap = yesterday_dir / f.name
            if yesterday_snap.exists():
                old = yesterday_snap.read_text(encoding="utf-8", errors="replace")
                if old != content:
                    raw = list(difflib.unified_diff(
                        old.splitlines(), content.splitlines(),
                        fromfile="yesterday", tofile="today", lineterm=""
                    ))
                    added   = sum(1 for l in raw if l.startswith("+") and not l.startswith("+++"))
                    removed = sum(1 for l in raw if l.startswith("-") and not l.startswith("---"))
                    diff_lines   = raw
                    diff_summary = f"+{added} added / −{removed} removed since yesterday"
                else:
                    diff_summary = "unchanged since yesterday"
            else:
                diff_summary = "no yesterday snapshot — diff will appear tomorrow"

            cards.append({
                "name":         f.name,
                "rel":          rel_time(dt.isoformat()),
                "lines":        len(content.splitlines()),
                "content":      content[:6000],
                "truncated":    len(content) > 6000,
                "diff_lines":   diff_lines,
                "diff_summary": diff_summary,
            })
        except Exception:
            pass
    return cards

# ── SVG Helpers ───────────────────────────────────────────────────────────────

def arc_gauge_svg(score, color):
    arc_len = math.pi * 100
    pct     = max(0.0, min(100.0, float(score))) / 100.0
    offset  = arc_len * (1.0 - pct)
    return f"""<svg viewBox="0 0 300 170" width="260" height="148" style="display:block;margin:0 auto">
  <defs>
    <filter id="arc-glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="3" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <path d="M 50 150 A 100 100 0 0 1 250 150"
        fill="none" stroke="var(--surface0)" stroke-width="14" stroke-linecap="round"/>
  <path d="M 50 150 A 100 100 0 0 1 250 150"
        fill="none" stroke="{color}" stroke-width="14" stroke-linecap="round"
        stroke-dasharray="{arc_len:.2f}" stroke-dashoffset="{offset:.2f}"
        filter="url(#arc-glow)"/>
  <circle cx="50"  cy="150" r="7" fill="{color}" opacity="0.5"/>
  <circle cx="250" cy="150" r="7" fill="{color}" opacity="0.5"/>
  <text x="150" y="128" text-anchor="middle" fill="{color}"
        font-size="40" font-weight="800"
        font-family="ui-monospace,Consolas,'Courier New',monospace">{score:.0f}%</text>
  <text x="150" y="148" text-anchor="middle" fill="var(--overlay0)"
        font-size="10" font-family="system-ui,sans-serif" letter-spacing="2">TRUST SCORE</text>
</svg>"""

def bar_chart_svg(records, width=420, height=64):
    if not records:
        return f'<svg width="{width}" height="{height}"></svg>'
    n     = len(records)
    gap   = 2
    bar_w = max(4, (width - gap * (n - 1)) // n)
    bars  = []
    for i, r in enumerate(records):
        delta = r.get("delta_applied", 0)
        color = "var(--green)" if delta >= 0 else "var(--red)"
        bh    = max(3, min(height - 4, int(abs(delta) * 8)))
        x     = i * (bar_w + gap)
        y     = height - bh - 2
        bars.append(f'<rect x="{x}" y="{y}" width="{bar_w}" height="{bh}" '
                    f'rx="2" fill="{color}" opacity="0.8"/>')
    return (f'<svg viewBox="0 0 {n*(bar_w+gap)} {height}" width="{width}" '
            f'height="{height}" preserveAspectRatio="none">'
            + "".join(bars) + "</svg>")

# ── Section Renderers ─────────────────────────────────────────────────────────

def render_trust_battery(rd):
    scores = rd["trust_scores"]
    obs    = rd["observations"]
    if not scores:
        return empty_state("No trust score data yet. Run daily-maintenance to start tracking.")

    cur    = scores[-1]
    score  = cur.get("score", 0)
    delta  = cur.get("delta_applied", 0)
    ts     = cur.get("ts", "")
    color  = ("#a6e3a1" if score >= 70 else
              "#f9e2af" if score >= 40 else "#f38ba8")
    if delta > 0:
        delta_str   = f"▲ +{delta:.1f}"
        delta_color = "#a6e3a1"
    elif delta < 0:
        delta_str   = f"▼ {delta:.1f}"
        delta_color = "#f38ba8"
    else:
        delta_str   = "▬ 0.0"
        delta_color = "var(--overlay0)"

    # Judge sampling state (pass^k). `samples`/`spread`/`inconclusive` are written
    # by scripts/session/trust_score.py. Records predating 2026-09-03 have none of
    # these keys — that is "unsampled", which is a different fact from "sampled and
    # agreed", so it renders as nothing rather than as a reassuring badge.
    samples      = cur.get("samples") or []
    spread       = cur.get("spread")
    inconclusive = bool(cur.get("inconclusive"))
    sample_html  = ""
    if inconclusive:
        sample_html = (
            f'<div style="margin-top:8px;text-align:center">'
            f'<span style="display:inline-block;background:rgba(249,226,175,0.10);'
            f'border:1px solid #f9e2af66;border-radius:20px;padding:4px 12px;'
            f'font-size:12px;font-weight:600;color:#f9e2af" '
            f'title="The judge was run {len(samples)}× and the verdicts spread by '
            f'{spread:.1f} points. No delta was applied — a reading that depends on '
            f'which draw you look at is not a reading.">'
            f'⚠ judge split &nbsp;{h(", ".join(f"{s:+.1f}" for s in samples))}'
            f'</span></div>'
        )
    elif len(samples) > 1:
        sample_html = (
            f'<div style="margin-top:8px;text-align:center">'
            f'<span style="display:inline-block;color:var(--overlay0);font-size:11px" '
            f'title="Median of {len(samples)} independent judge runs; spread '
            f'{spread:.1f} points.">'
            f'median of {len(samples)} runs · spread {spread:.1f}'
            f'</span></div>'
        )

    judge_entries = obs.get("judge", [])
    judge_html    = ""
    if judge_entries:
        last_judge = judge_entries[-1][1]
        truncated  = h(last_judge[:300]) + ("…" if len(last_judge) > 300 else "")
        judge_html = (f'<div style="margin-top:14px"><div class="label">Latest judge verdict</div>'
                      f'<div style="background:var(--surface0);border-radius:6px;padding:10px 12px;'
                      f'margin-top:6px;font-size:12px;color:var(--subtext1);line-height:1.6">'
                      f'{truncated}</div></div>')

    seven_day = sum(r.get("delta_applied", 0) for r in scores[-7:])
    gauge      = arc_gauge_svg(score, color)
    chart      = bar_chart_svg(scores)

    desc = section_desc(
        "Cumulative score adjusted daily by the <strong style='color:var(--text)'>session-judge</strong> "
        "agent (runs inside daily-maintenance). Starts at 20% and moves by <strong>±4.5% max/day</strong>. "
        "<strong style='color:var(--yellow)'>Score only moves on specific evidence</strong> — "
        "it does NOT react to general chat. Charges: spec gate passes, self-caught bugs "
        "<code>[debug]</code>, reuse cited in <code>[impl]</code>, rule cited in <code>[decision]</code>. "
        "Drains: <code>[memory-gap]</code> re-explanations (−2 each), silent failures, gate bypasses, churn. "
        "Delta=0 means the judge found no scorable evidence in the last 24h observations window — "
        "normal for maintenance-only days or when sessions don't produce these specific tags. "
        "The judge runs <strong>3× per day</strong> and the <strong>median</strong> is applied; "
        "when the three verdicts spread by more than 2.0 points the day is marked "
        "<strong style='color:var(--yellow)'>judge split</strong> and <strong>no delta is "
        "applied</strong> — that is the score declining to commit a number it can't stand "
        "behind, not an error to fix."
    )
    return f"""<div class="section-inner">
  <h2 class="section-title">Trust Battery</h2>
  {desc}
  <div style="display:flex;gap:32px;align-items:flex-start;flex-wrap:wrap">
    <div style="min-width:260px">
      {gauge}
      <div style="text-align:center;margin-top:6px">
        <span style="display:inline-block;background:rgba(166,227,161,0.08);
                     border:1px solid {color}44;border-radius:20px;
                     padding:4px 14px;font-size:13px;font-weight:600;
                     color:{delta_color}">{h(delta_str)} today</span>
      </div>
      {sample_html}
      <div style="text-align:center;margin-top:5px;color:var(--overlay0);font-size:11px">
        {h(rel_time(ts))}
      </div>
    </div>
    <div style="flex:1;min-width:240px">
      <div class="label" style="margin-bottom:6px">30-day delta trend</div>
      {chart}
      <div style="display:flex;gap:10px;margin-top:12px">
        <div class="stat-card">
          <div class="stat-val" style="color:{color}">{score:.0f}%</div>
          <div class="stat-lbl">current</div>
        </div>
        <div class="stat-card">
          <div class="stat-val" style="color:var(--mauve)">{seven_day:+.1f}</div>
          <div class="stat-lbl">7-day delta</div>
        </div>
        <div class="stat-card">
          <div class="stat-val" style="color:var(--blue)">{len(scores)}</div>
          <div class="stat-lbl">days tracked</div>
        </div>
      </div>
      {judge_html}
    </div>
  </div>
</div>"""

def render_gitnexus(rd, companion=False):
    gn        = rd["gitnexus"]
    repo_path = rd["path"]
    repo_name = h(Path(repo_path).name)

    if not gn.get("available"):
        return f"""<div class="section-inner">
  <h2 class="section-title">GitNexus — {repo_name}</h2>
  <div style="padding:40px;text-align:center">
    <div style="font-size:32px;margin-bottom:10px">🕸</div>
    <div style="color:var(--subtext0);margin-bottom:12px">GitNexus not indexed for <strong>{repo_name}</strong></div>
    <div style="display:inline-block;background:var(--surface0);border-radius:6px;
                padding:8px 14px;font-family:monospace;font-size:12px;color:var(--text)">
      /kiro:gitnexus-setup
    </div>
    <div style="color:var(--overlay0);font-size:11px;margin-top:8px">
      Run this command to index the repo and enable graph intelligence
    </div>
  </div>
</div>"""

    stale_color = ("#f38ba8" if gn.get("very_stale") else
                   "#f9e2af" if gn.get("stale")      else "#a6e3a1")

    def _fmt(val):
        if val is None:
            return "—"
        if val >= 1000:
            return f"{val/1000:.1f}k"
        return str(val)

    stats_html = f"""<div style="display:grid;grid-template-columns:repeat(4,1fr);
                          gap:1px;background:var(--surface0);border-radius:0;
                          overflow:hidden;flex-shrink:0;border-bottom:1px solid var(--surface0)">
    <div style="background:var(--base);padding:8px 10px;text-align:center">
      <div style="color:var(--mauve);font-size:16px;font-weight:700;line-height:1.2">{_fmt(gn.get("symbols"))}</div>
      <div class="label">symbols</div></div>
    <div style="background:var(--base);padding:8px 10px;text-align:center">
      <div style="color:var(--teal);font-size:16px;font-weight:700;line-height:1.2">{_fmt(gn.get("clusters"))}</div>
      <div class="label">clusters</div></div>
    <div style="background:var(--base);padding:8px 10px;text-align:center">
      <div style="color:var(--blue);font-size:16px;font-weight:700;line-height:1.2">{_fmt(gn.get("processes"))}</div>
      <div class="label">flows</div></div>
    <div style="background:var(--base);padding:8px 10px;text-align:center">
      <div style="color:{stale_color};font-size:12px;font-weight:600;line-height:1.2">
        {h(gn.get("indexed_ago","unknown"))}</div>
      <div class="label">indexed</div></div>
  </div>"""

    # `gitnexus serve` is an API-only backend — it serves /api/* and 404s on /.
    # The web UI is the hosted app, which talks to http://localhost:4747 by
    # default and auto-selects a repo from its own ?repo= query param.
    from urllib.parse import quote as _quote
    iframe_src = f"{GN_WEB_UI}/?repo={_quote(Path(repo_path).name)}"

    rp_js = html.escape(json.dumps(repo_path))  # &quot; inside onclick attr

    serve_btn = ""
    if companion:
        serve_btn = f"""<button id="gn-start-btn"
           style="display:none;background:var(--mauve);color:var(--crust);border:none;
                  border-radius:6px;padding:9px 18px;font-size:12px;font-weight:600;
                  cursor:pointer;letter-spacing:.3px"
           onclick="startGitnexus({rp_js})">
        ▶ Start gitnexus serve
      </button>"""

    iframe_height = "calc(100vh - 110px)"

    iframe_html = f"""<div style="position:relative;overflow:hidden;background:var(--crust);
                          flex:1;min-height:0">
    <iframe id="gn-frame" data-src="{html.escape(iframe_src)}"
            allow="local-network-access"
            style="width:100%;height:{iframe_height};border:none;display:none">
    </iframe>
    <div id="gn-fallback" style="display:flex;height:{iframe_height};
              background:var(--mantle);flex-direction:column;
              align-items:center;justify-content:center;gap:12px">
      <div style="font-size:32px">🕸</div>
      <div id="gn-status" style="color:var(--subtext0);font-size:13px">Checking gitnexus…</div>
      {serve_btn}
      <div id="gn-copy-btn"
           style="display:none;background:var(--surface0);border-radius:6px;padding:8px 16px;
                  font-family:monospace;font-size:12px;color:var(--text);cursor:pointer"
           onclick="navigator.clipboard.writeText('gitnexus serve');
                    this.textContent='✓ Copied!'">
        gitnexus serve  📋
      </div>
      <div id="gn-copy-hint" style="display:none;color:var(--overlay0);font-size:11px">
        Click to copy, then run in your terminal
      </div>
      <button id="gn-retry-btn"
           style="display:none;background:transparent;color:var(--subtext0);border:1px solid var(--surface0);
                  border-radius:6px;padding:6px 14px;font-size:11px;cursor:pointer;margin-top:4px"
           onclick="_gnProbing=false;probeGitnexus();this.style.display='none';
                    document.getElementById('gn-status').textContent='Checking gitnexus…'">
        ↺ Retry
      </button>
    </div>
  </div>
"""

    # Full-bleed layout: thin stats header + iframe fills the rest of the viewport
    return f"""<div style="display:flex;flex-direction:column;height:100vh;overflow:hidden">
  {stats_html.replace('margin-bottom:14px', 'margin-bottom:0;border-radius:0;flex-shrink:0')}
  {iframe_html}
</div>"""

def render_workshop(rd, companion=False):
    ws        = rd.get("workshop", {})
    repo_path = rd["path"]
    repo_name = h(Path(repo_path).name)

    if not ws.get("installed"):
        return f"""<div class="section-inner">
  <h2 class="section-title">Workshop — {repo_name}</h2>
  <div style="padding:40px;text-align:center">
    <div style="font-size:32px;margin-bottom:10px">🔬</div>
    <div style="color:var(--subtext0);margin-bottom:12px">Raindrop Workshop not installed</div>
    <div style="display:inline-block;background:var(--surface0);border-radius:6px;
                padding:8px 14px;font-family:monospace;font-size:12px;color:var(--text)">
      curl -fsSL https://raindrop.sh/install | bash
    </div>
    <div style="color:var(--overlay0);font-size:11px;margin-top:8px">
      Run this to install Raindrop Workshop locally
    </div>
  </div>
</div>"""

    sdk_badge = badge("instrumented", "ok") if ws.get("instrumented") else badge("not instrumented", "warn")

    eval_btn_html = ""
    if companion:
        rp_js = html.escape(json.dumps(repo_path))
        rn_js = html.escape(json.dumps(Path(repo_path).name))
        if ws.get("instrumented"):
            eval_btn_html = f"""<button id="ws-eval-btn"
               style="background:var(--mauve);color:var(--crust);border:none;
                      border-radius:6px;padding:9px 18px;font-size:12px;font-weight:600;
                      cursor:pointer;letter-spacing:.3px;white-space:nowrap;flex-shrink:0"
               onclick="runEvalLoop({rp_js},{rn_js})">
            ⚗ Run Eval Loop
          </button>"""
        else:
            eval_btn_html = """<div style="color:var(--overlay0);font-size:11px;
                                           white-space:nowrap;flex-shrink:0">
              run /raindrop-instrument-agent to enable evals
            </div>"""

    event_name = Path(repo_path).name
    en_attr    = html.escape(event_name)

    open_ws_btn = refresh_btn = ""
    if companion:
        open_ws_btn = """<a href="http://localhost:5899" target="_blank"
           style="background:var(--surface0);color:var(--text);text-decoration:none;
                  border-radius:6px;padding:9px 14px;font-size:12px;font-weight:600;
                  white-space:nowrap;flex-shrink:0">
            ↗ Full UI
          </a>"""
        refresh_btn = """<button
           style="background:transparent;color:var(--subtext0);border:1px solid var(--surface0);
                  border-radius:6px;padding:9px 14px;font-size:12px;cursor:pointer;
                  white-space:nowrap;flex-shrink:0"
           onclick="loadWorkshopRuns()">↺</button>"""

    header_html = f"""<div style="display:flex;align-items:center;background:var(--base);
                           border-bottom:1px solid var(--surface0);padding:8px 12px;
                           flex-shrink:0;gap:8px">
    <div style="flex:1;display:grid;grid-template-columns:repeat(3,1fr);gap:1px;
                background:var(--surface0);border-radius:6px;overflow:hidden">
      <div style="background:var(--base);padding:8px 10px;text-align:center">
        <div style="color:var(--teal);font-size:14px;font-weight:700;line-height:1.2">🔬</div>
        <div class="label">Workshop</div></div>
      <div style="background:var(--base);padding:8px 10px;text-align:center">
        <div style="font-size:12px;font-weight:600;color:var(--text);line-height:1.2">{repo_name}</div>
        <div class="label">repo</div></div>
      <div style="background:var(--base);padding:8px 10px;text-align:center">
        <div style="padding:2px 0">{sdk_badge}</div>
        <div class="label">SDK</div></div>
    </div>
    {eval_btn_html}
    {open_ws_btn}
    {refresh_btn}
  </div>"""

    eval_note = (
        "The <strong style='color:var(--text)'>Run Eval Loop</strong> button launches a Claude session "
        "that replays recent Workshop runs against a self-healing test harness — useful for catching "
        "regressions after code changes. Requires the repo to be instrumented first."
        if ws.get("instrumented") else
        "The <strong style='color:var(--text)'>SDK badge</strong> above shows <em>not instrumented</em> — "
        "run <code>/instrument-agent</code> in that repo's Claude session to wire up tracing, then "
        "restart the app. The <strong>Run Eval Loop</strong> button will appear once instrumented."
    )
    desc = section_desc(
        "<strong style='color:var(--text)'>Raindrop Workshop</strong> captures AI agent traces for "
        f"<code>{h(event_name)}</code> — each run records the input sent to the agent, the LLM and tool "
        "calls it made, and its final output. Runs appear here automatically whenever the app is invoked "
        "with Workshop running. Filter is scoped to this repo's <code>event_name</code> so switching "
        "repos shows only that repo's traces. " + eval_note,
        icon="🔬", color="var(--teal)"
    )

    runs_panel = f"""<div id="ws-runs-panel" data-event-name="{en_attr}"
      style="flex:1;overflow-y:auto;min-height:0;background:var(--mantle)">
      <div style="color:var(--subtext0);padding:40px;text-align:center">Loading…</div>
    </div>"""

    return f"""<div style="display:flex;flex-direction:column;height:100vh;overflow:hidden">
  {header_html}
  <div style="padding:12px 16px 0;flex-shrink:0">{desc}</div>
  {runs_panel}
</div>"""

def render_hooks_history(rd):
    hooks = rd["hooks"]
    obs   = rd["observations"]
    if not hooks:
        return empty_state("No hooks found in .claude/hooks/")

    all_obs = sorted(
        [(d, tag, t) for tag, entries in obs.items() for d, t in entries],
        key=lambda x: x[0], reverse=True
    )

    event_map = {
        "session-start":        "SessionStart",
        "stop":                 "Stop",
        "memory-discipline":    "PreToolUse",
        "impeccable-detect":    "PostToolUse",
        "revert-detect":        "PostToolUse",
        "hook-added-notify":    "PostToolUse",
        "gbrain-agent-spawn":   "PreToolUse",
        "gbrain-memory-write":  "PreToolUse",
        "gbrain-external":      "PreToolUse",
        "compaction-discipline":   "PreCompact",
        "scan-pii":                "PostToolUse",
        "pre-tool-use-gitnexus":   "PreToolUse",
        "doc-parse-nudge":         "UserPromptSubmit",
        "frontend-security-nudge": "UserPromptSubmit",
    }

    desc = section_desc(
        "Hooks are shell scripts that Claude Code calls automatically at lifecycle events. "
        "They run outside Claude's permission system. "
        "<strong style='color:var(--text)'>Click any hook</strong> to see its recorded activity in observations. "
        "Hooks without observations either haven't fired yet or don't write to observations.md."
    )

    cards = ""
    for hook_file in hooks:
        stem  = hook_file.replace(".sh", "")
        event = next((v for k, v in event_map.items() if k in stem), "Unknown")
        descr = next((v for k, v in HOOK_DESCRIPTIONS.items() if k in stem), "")

        # Collect all observations that plausibly mention this hook
        key_parts = [p for p in stem.replace("-hook", "").split("-") if len(p) > 3]
        matching = [
            (d, tag, t) for d, tag, t in all_obs
            if any(p in t.lower() or p in tag.lower() for p in key_parts)
        ]
        last_activity = matching[0] if matching else None

        # Last activity chip
        if last_activity:
            la_date, la_tag, la_text = last_activity
            la_html = (f'<span style="color:var(--subtext1);font-size:11px">'
                       f'{h(la_date)}</span>'
                       f'<span style="color:var(--overlay0);font-size:11px;margin-left:6px">'
                       f'{h(la_text[:80])}{"…" if len(la_text)>80 else ""}</span>')
        else:
            la_html = '<span style="color:var(--overlay0);font-size:11px;font-style:italic">no recorded activity</span>'

        # Activity detail: list all matching observations
        if matching:
            detail_items = "".join(
                f'<div style="padding:4px 0;border-bottom:1px solid rgba(49,50,68,0.4);'
                f'font-size:11px;color:var(--subtext0)">'
                f'<span style="color:var(--overlay0);margin-right:8px">{h(d)}</span>'
                f'<span style="color:var(--mauve);margin-right:6px;font-size:9px">[{h(tag)}]</span>'
                f'{h(t)}</div>'
                for d, tag, t in matching[:10]
            )
            detail_html = (
                f'<div style="padding:10px 12px;background:rgba(0,0,0,0.15);'
                f'border-top:1px solid var(--surface0)">'
                f'<div style="font-size:9px;text-transform:uppercase;letter-spacing:1px;'
                f'color:var(--overlay0);margin-bottom:6px">Recorded activity (newest first)</div>'
                f'{detail_items}</div>'
            )
            container = "details"
            header_tag = "summary"
            header_extra = "cursor:pointer;"
        else:
            detail_html = ""
            container = "div"
            header_tag = "div"
            header_extra = ""

        event_color = {
            "SessionStart": "#a6e3a1", "Stop": "#f38ba8",
            "PreToolUse": "#89b4fa", "PostToolUse": "#94e2d5", "PreCompact": "#cba6f7",
            "UserPromptSubmit": "#fab387",
        }.get(event, "#a6adc8")

        cards += (
            f'<{container} style="border:1px solid var(--surface0);border-radius:7px;'
            f'overflow:hidden;margin-bottom:6px">'
            f'<{header_tag} style="display:grid;grid-template-columns:180px 100px 1fr auto;'
            f'gap:12px;align-items:center;padding:8px 12px;background:var(--base);{header_extra}">'
            f'<code style="color:var(--mauve);font-size:10px;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap">{h(hook_file)}</code>'
            f'<span style="color:{event_color};font-size:10px;font-weight:600">{h(event)}</span>'
            f'<span style="font-size:11px;color:var(--subtext0)">'
            f'{h(descr) if descr else la_html}</span>'
            f'{badge("Active","active")}'
            f'</{header_tag}>'
            f'{detail_html}'
            f'</{container}>'
        )

    return f"""<div class="section-inner">
  <h2 class="section-title">Hooks History</h2>
  {desc}
  {cards}
</div>"""

# ── Model Cost: data collection ───────────────────────────────────────────────

def load_or_refresh_pricing_history():
    """Return list of pricing snapshots, refreshing from models.dev if stale."""
    snapshots = []
    if PRICING_HISTORY.exists():
        try:
            snapshots = json.loads(PRICING_HISTORY.read_text()).get("snapshots", [])
        except Exception:
            pass

    needs_refresh = True
    if snapshots:
        try:
            latest_ts = datetime.fromisoformat(snapshots[-1]["fetched_at"].replace("Z", "+00:00"))
            needs_refresh = (NOW - latest_ts).total_seconds() > PRICING_MAX_AGE
        except Exception:
            pass

    if needs_refresh:
        try:
            req = UrlRequest(
                "https://models.dev/api.json",
                headers={"User-Agent": "sdd-harness-dashboard/1.0"},
            )
            with urlopen(req, timeout=10) as r:
                raw = json.loads(r.read())

            fresh_models = {}
            for provider_id, provider in raw.items():
                if not isinstance(provider, dict) or "models" not in provider:
                    continue
                for model_id, model in provider["models"].items():
                    cost = model.get("cost")
                    if cost:
                        key = f"{provider_id}/{model_id}"
                        fresh_models[key] = {
                            "input":       float(cost.get("input",       0)),
                            "output":      float(cost.get("output",      0)),
                            "cache_read":  float(cost.get("cache_read",  0)),
                            "cache_write": float(cost.get("cache_write", 0)),
                        }

            ts_now = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
            if snapshots and snapshots[-1].get("models") == fresh_models:
                snapshots[-1]["fetched_at"] = ts_now
            else:
                snapshots.append({"fetched_at": ts_now, "models": fresh_models})

            PRICING_HISTORY.parent.mkdir(exist_ok=True)
            PRICING_HISTORY.write_text(json.dumps({"snapshots": snapshots}, indent=2))
        except Exception:
            pass

    return snapshots


def get_pricing_at(snapshots, date_str):
    """Return the pricing dict from the snapshot closest to (but not after) date_str."""
    if not snapshots:
        return {}
    try:
        session_dt = datetime.fromisoformat(date_str + "T00:00:00+00:00")
    except Exception:
        return snapshots[-1].get("models", {})

    best = None
    for snap in snapshots:
        try:
            snap_dt = datetime.fromisoformat(snap["fetched_at"].replace("Z", "+00:00"))
            if snap_dt <= session_dt:
                best = snap
        except Exception:
            continue

    return (best or snapshots[0]).get("models", {})


USAGE_FIELDS = (
    ("input",        "input_tokens"),
    ("output",       "output_tokens"),
    ("cache_read",   "cache_read_input_tokens"),
    ("cache_create", "cache_creation_input_tokens"),
)


def _parse_session_file(path, project_name):
    # One API response is written to the transcript as one JSONL line PER CONTENT
    # BLOCK, and every one of those lines repeats the SAME message.usage object.
    # Summing line-by-line double-counts multi-block responses — measured at ~83%
    # inflation across this machine's transcripts. Collapse on the response id
    # first, then sum. Keep the max per field rather than the first seen: a
    # streamed response's later blocks can carry the final (larger) output count.
    by_request = {}
    model = None
    first_ts = None
    collapsed = 0

    for lineno, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines()
    ):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message", {})
        if not isinstance(msg, dict):
            continue
        if model is None and msg.get("model"):
            model = msg["model"]
        if first_ts is None:
            first_ts = obj.get("timestamp")
        usage = msg.get("usage", {})
        if not isinstance(usage, dict):
            continue

        # Fall back to file:lineno so an id-less line is never merged into another.
        key = obj.get("requestId") or msg.get("id") or f"{path}:{lineno}"
        prev = by_request.get(key)
        if prev is None:
            by_request[key] = {
                field: usage.get(raw, 0) or 0 for field, raw in USAGE_FIELDS
            }
        else:
            collapsed += 1
            for field, raw in USAGE_FIELDS:
                prev[field] = max(prev[field], usage.get(raw, 0) or 0)

    input_t        = sum(u["input"]        for u in by_request.values())
    output_t       = sum(u["output"]       for u in by_request.values())
    cache_read_t   = sum(u["cache_read"]   for u in by_request.values())
    cache_create_t = sum(u["cache_create"] for u in by_request.values())

    if model is None or (input_t == 0 and output_t == 0 and cache_read_t == 0):
        return None

    date_str = "unknown"
    if first_ts:
        try:
            dt = datetime.fromisoformat(str(first_ts).replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    return {
        "date":         date_str,
        "project":      project_name,
        "model":        model,
        "input":        input_t,
        "output":       output_t,
        "cache_read":   cache_read_t,
        "cache_create": cache_create_t,
        # Format-drift canary: if this is 0 across every session, the transcript
        # no longer repeats usage per content block (or requestId went away) and
        # the dedup above has silently become a no-op. See the usage-dedup test.
        "collapsed":    collapsed,
    }


def gather_usage_data():
    """Scan ~/.claude/projects for session JSONL files and extract token usage."""
    sessions = []
    if not CLAUDE_PROJECTS.exists():
        return sessions

    for project_dir in sorted(CLAUDE_PROJECTS.iterdir()):
        if not project_dir.is_dir():
            continue
        raw = project_dir.name
        for prefix in ("-Users-dansasha-Documents-", "-Users-dansasha-Desktop-",
                       "-Users-dansasha-"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        else:
            raw = ""
        project_name = raw.replace("-", " ").strip() or "(global)"

        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            try:
                session = _parse_session_file(jsonl_file, project_name)
                if session:
                    sessions.append(session)
            except Exception:
                continue

    return sorted(sessions, key=lambda s: s["date"], reverse=True)


def compute_session_cost(session, pricing):
    """Return USD cost for a session using the given pricing snapshot (per-million-token rates)."""
    key = f"anthropic/{session['model']}"
    p = pricing.get(key)
    if not p:
        return None
    return (
        session["input"]        * p["input"]       / 1_000_000 +
        session["output"]       * p["output"]      / 1_000_000 +
        session["cache_read"]   * p["cache_read"]  / 1_000_000 +
        session["cache_create"] * p["cache_write"] / 1_000_000
    )


# ── Model Cost: render ────────────────────────────────────────────────────────

def render_model_cost(sessions, pricing_snapshots):
    if not sessions:
        return empty_state(
            "No session data found. Sessions accumulate in ~/.claude/projects/ "
            "as you use Claude Code."
        )

    latest_pricing  = pricing_snapshots[-1]["models"] if pricing_snapshots else {}
    latest_snap_ts  = pricing_snapshots[-1]["fetched_at"][:10] if pricing_snapshots else "—"
    n_snapshots     = len(pricing_snapshots)

    # Annotate sessions with historical cost and change flag
    priced = []
    for s in sessions:
        if s["date"] == "unknown":
            continue
        hist_pricing = get_pricing_at(pricing_snapshots, s["date"])
        cost         = compute_session_cost(s, hist_pricing)
        latest_cost  = compute_session_cost(s, latest_pricing)
        price_changed = (
            cost is not None and latest_cost is not None
            and abs(cost - latest_cost) > 1e-9
        )
        cache_cost = None
        if hist_pricing:
            p = hist_pricing.get(f"anthropic/{s['model']}")
            if p:
                cache_cost = (
                    s["cache_read"]   * p["cache_read"]  / 1_000_000 +
                    s["cache_create"] * p["cache_write"] / 1_000_000
                )
        priced.append({**s, "cost": cost, "price_changed": price_changed, "cache_cost": cache_cost})

    total_cost = sum(p["cost"] for p in priced if p["cost"] is not None)
    cutoff_30d = (NOW - timedelta(days=30)).strftime("%Y-%m-%d")
    cost_30d   = sum(
        p["cost"] for p in priced
        if p["cost"] is not None and p["date"] >= cutoff_30d
    )
    total_cache_cost = sum(p["cache_cost"] for p in priced if p["cache_cost"] is not None)
    cache_pct = (total_cache_cost / total_cost * 100) if total_cost > 0 else 0.0

    # ── Stats row ──────────────────────────────────────────────────────────────
    summary = f"""<div style="display:grid;grid-template-columns:repeat(4,1fr);
                       gap:12px;margin-bottom:20px">
  <div class="stat-card">
    <div class="stat-val" style="color:#a6e3a1">${total_cost:.2f}</div>
    <div class="stat-lbl">total cost (all time)</div></div>
  <div class="stat-card">
    <div class="stat-val" style="color:#89b4fa">${cost_30d:.2f}</div>
    <div class="stat-lbl">cost last 30 days</div></div>
  <div class="stat-card">
    <div class="stat-val" style="color:#f9e2af">{len(sessions)}</div>
    <div class="stat-lbl">sessions tracked</div></div>
  <div class="stat-card">
    <div class="stat-val" style="color:{'#f38ba8' if cache_pct >= 70 else '#cba6f7'}">{cache_pct:.0f}%</div>
    <div class="stat-lbl">cost from cache (r+w)</div></div>
</div>"""

    # ── Project filter ──────────────────────────────────────────────────────────
    projects  = sorted(set(s["project"] for s in sessions))
    proj_opts = '<option value="">All projects</option>' + "".join(
        f'<option value="{h(p)}">{h(p)}</option>' for p in projects
    )
    proj_filter = f"""<div style="margin-bottom:16px;display:flex;align-items:center;gap:12px">
  <label style="font-size:12px;color:var(--subtext0)">Project:</label>
  <select id="mc-proj-filter" onchange="mcFilter(this.value)"
    style="background:var(--surface1);color:var(--text);border:1px solid var(--surface2);
           border-radius:6px;padding:4px 10px;font-size:12px">
    {proj_opts}
  </select>
</div>"""

    # ── Cost chart (last 90 days) ───────────────────────────────────────────────
    cutoff_90d = (NOW - timedelta(days=90)).strftime("%Y-%m-%d")
    daily: dict[str, float] = {}
    for p in priced:
        if p["cost"] is None or p["date"] < cutoff_90d:
            continue
        daily[p["date"]] = daily.get(p["date"], 0.0) + p["cost"]

    chart = ""
    if daily:
        max_cost = max(daily.values()) or 1
        bars = ""
        for date, cost in sorted(daily.items()):
            bh = max(2, int(cost / max_cost * 44))
            bars += (
                f'<div title="{h(date)}: ${cost:.4f}" style="flex:1;display:flex;'
                f'flex-direction:column;align-items:center;justify-content:flex-end;'
                f'gap:2px;min-width:3px">'
                f'<div style="background:#89b4fa;height:{bh}px;width:100%;'
                f'border-radius:2px 2px 0 0;opacity:0.8"></div></div>'
            )
        chart = (
            f'<div class="label" style="margin-bottom:6px">Daily cost — last 90 days</div>'
            f'<div style="display:flex;align-items:flex-end;gap:1px;height:64px;'
            f'margin-bottom:20px">{bars}</div>'
        )

    # ── Sessions table ──────────────────────────────────────────────────────────
    rows = ""
    for p in priced[:200]:
        label, color = _MODEL_LABEL.get(p["model"], (p["model"], "#a6adc8"))
        cost_str  = f"${p['cost']:.4f}" if p["cost"] is not None else "—"
        tokens_k  = (p["input"] + p["output"] + p["cache_read"] + p["cache_create"]) // 1000
        warn_icon = (
            f' <span title="Pricing changed since this session" '
            f'style="color:#f9e2af">⚠</span>'
            if p.get("price_changed") else ""
        )
        rows += (
            f'<tr data-project="{h(p["project"])}" '
            f'style="border-bottom:1px solid var(--surface1)">'
            f'<td style="padding:6px 8px;font-size:11px;color:var(--subtext1)">{h(p["date"])}</td>'
            f'<td style="padding:6px 8px;font-size:11px;color:var(--text)">{h(p["project"])}</td>'
            f'<td style="padding:6px 8px">'
            f'<span style="font-size:10px;font-weight:600;color:{color};'
            f'background:{color}22;padding:2px 7px;border-radius:10px">{h(label)}</span></td>'
            f'<td style="padding:6px 8px;font-size:11px;color:var(--subtext0);'
            f'text-align:right">{tokens_k}K</td>'
            f'<td style="padding:6px 8px;font-size:11px;color:var(--text);'
            f'text-align:right;font-family:monospace">{h(cost_str)}{warn_icon}</td>'
            f'</tr>'
        )

    table = f"""<div class="label" style="margin-bottom:6px">Sessions (newest first)</div>
<div style="overflow-x:auto;margin-bottom:20px;max-height:360px;overflow-y:auto">
<table id="mc-table" style="width:100%;border-collapse:collapse">
<thead style="position:sticky;top:0;background:var(--base)"><tr style="border-bottom:1px solid var(--surface2)">
  <th style="padding:6px 8px;font-size:10px;text-align:left;color:var(--overlay0);font-weight:500">Date</th>
  <th style="padding:6px 8px;font-size:10px;text-align:left;color:var(--overlay0);font-weight:500">Project</th>
  <th style="padding:6px 8px;font-size:10px;text-align:left;color:var(--overlay0);font-weight:500">Model</th>
  <th style="padding:6px 8px;font-size:10px;text-align:right;color:var(--overlay0);font-weight:500">Tokens</th>
  <th style="padding:6px 8px;font-size:10px;text-align:right;color:var(--overlay0);font-weight:500">Cost</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>
</div>"""

    # ── What-if switcher (cascading provider → model) ───────────────────────────
    used_model_ids = set(s["model"] for s in sessions)

    prov_opts = '<option value="">All providers</option>'
    for prov in FEATURED_PROVIDERS:
        prov_opts += f'<option value="{h(prov)}">{h(PROVIDER_DISPLAY.get(prov, prov))}</option>'

    whatif = f"""<div style="background:var(--surface0);border-radius:8px;padding:14px 16px;margin-bottom:20px">
  <div class="label" style="margin-bottom:10px">What if you&apos;d used a different model?</div>
  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px">
    <select id="mc-whatif-provider" onchange="mcProviderChange(this.value)"
      style="background:var(--surface1);color:var(--text);border:1px solid var(--surface2);
             border-radius:6px;padding:4px 10px;font-size:12px">
      {prov_opts}
    </select>
    <select id="mc-whatif-model" onchange="mcWhatIf(this.value)"
      style="background:var(--surface1);color:var(--text);border:1px solid var(--surface2);
             border-radius:6px;padding:4px 10px;font-size:12px;min-width:260px">
      <option value="">Select model…</option>
    </select>
  </div>
  <div id="mc-whatif-result" style="font-size:13px;color:var(--subtext0);margin-bottom:4px"></div>
  <div style="font-size:10px;color:var(--overlay0)">
    ★ = models you have used &nbsp;|&nbsp; Applies alternative pricing to all tracked sessions
  </div>
</div>"""

    # ── Pricing history note ────────────────────────────────────────────────────
    source_note = f"""<div style="background:var(--surface0);border-radius:6px;
  padding:10px 12px;font-size:11px;color:var(--subtext0);margin-top:8px">
  <span style="color:var(--blue)">ℹ</span>
  Pricing from <strong style="color:var(--text)">models.dev</strong>
  (last fetched {h(latest_snap_ts)}, {n_snapshots} snapshot{'s' if n_snapshots != 1 else ''} stored).
  Refreshes bi-weekly. Historical sessions use the snapshot closest to their date.
  ⚠ = pricing changed between that session and the latest snapshot.
</div>"""

    return f"""<div class="section-inner">
  <h2 class="section-title">Model Cost</h2>
  {summary}
  {proj_filter}
  {chart}
  {table}
  {whatif}
  {source_note}
</div>"""


def _scheduler_banner(title, detail, footer):
    """Full-width red banner for a scheduler that cannot run.

    Deliberately loud and placed above the routine cards: the failure mode this exists
    for is a dead scheduler rendering as a page full of calm "PENDING" badges.
    """
    return (
        '<div style="border:1px solid var(--red);border-left:4px solid var(--red);'
        'border-radius:8px;padding:12px 14px;margin-bottom:14px;'
        'background:rgba(243,139,168,0.10)">'
        '<div style="color:var(--red);font-weight:700;font-size:12px;'
        'letter-spacing:0.4px;margin-bottom:4px">✗ ' + h(title) + '</div>'
        '<div style="color:var(--text);font-size:11px;line-height:1.5">'
        + h(detail) + '</div>'
        '<div style="color:var(--subtext1);font-size:10px;margin-top:6px;'
        'font-family:ui-monospace,monospace">' + footer + '</div>'
        '</div>'
    )


def render_scheduled_tasks(hd, repos_data=None, routines=None):
    # routines: pass the repo-scoped list from parse_scheduled_tasks(repo_path)
    # so per-repo routines (macro-eval, security-report, ...) show that repo's
    # own state, not whichever registered repo last ran. Falls back to hd's
    # harness-scoped list only for callers that don't care (none currently do).
    if routines is None:
        routines = hd.get("scheduled_tasks", [])
    scheduler = hd.get("scheduler", {}) or {}

    # ── Scheduler health card ────────────────────────────────────────────────
    inst_color = "var(--green)" if scheduler.get("installed") else "var(--red)"
    inst_text  = "installed" if scheduler.get("installed") else "NOT installed"
    nxt_iso    = scheduler.get("next_fire_iso")
    nxt_rel    = "—"
    if nxt_iso:
        try:
            dt = datetime.fromisoformat(nxt_iso)
            delta = (dt - datetime.now()).total_seconds()
            nxt_rel = (f"in {int(delta/3600)}h" if 0 < delta < 86400 else
                       f"in {int(delta/86400)}d" if delta >= 86400 else "due now")
        except Exception:
            pass

    last_act = scheduler.get("last_stdout_ts")
    last_act_str = rel_time(last_act) if last_act else "never"

    install_hint = ""
    # scheduler_alarm is rendered as a full-width banner ABOVE the automation cards, not
    # just as a line inside this card. A dead scheduler was previously reported only as
    # small yellow text here, while every routine below rendered a calm "PENDING" — so
    # a fleet that had executed nothing for four days looked merely idle. A scheduler
    # that cannot run is the single fact that invalidates everything under it.
    scheduler_alarm = ""
    if not scheduler.get("installed"):
        install_hint = (
            f'<div style="margin-top:8px;font-size:11px;color:var(--red)">'
            f'⚠ Scheduler not installed — run '
            f'<code style="background:var(--surface0);padding:1px 5px;border-radius:3px">'
            f'{h(scheduler.get("install_hint",""))}</code></div>'
        )
        scheduler_alarm = _scheduler_banner(
            "SCHEDULER NOT INSTALLED",
            "No routine below can run. Nothing on this page reflects scheduled work.",
            f'Run: {h(scheduler.get("install_hint", ""))}',
        )
    elif scheduler.get("last_exit") not in (None, 0):
        # launchd reports exit*256 (e.g. 32256 = exit 126 shifted left 8 bits)
        raw = scheduler.get("last_exit")
        shifted = raw >> 8 if raw and raw > 255 else raw
        install_hint = (
            f'<div style="margin-top:8px;font-size:11px;color:var(--red)">'
            f'⚠ Last orchestrator launch returned exit={shifted} — check '
            f'<code style="background:var(--surface0);padding:1px 5px;border-radius:3px">'
            f'{h(str(HARNESS_DIR / "logs" / "orchestrator.stderr.log"))}</code></div>'
        )
        # 126 is "found but could not execute" — on macOS almost always the TCC denial
        # you get when the harness sits under ~/Documents, ~/Desktop or ~/Downloads.
        detail = (
            "The scheduler is registered but its last launch could not execute. "
            "Every routine below is stale regardless of what its status says."
        )
        if shifted == 126:
            detail += (
                " Exit 126 means the OS refused to run the orchestrator — on macOS this "
                "is normally Full Disk Access: launchd cannot read ~/Documents, "
                "~/Desktop or ~/Downloads."
            )
        scheduler_alarm = _scheduler_banner(
            f"SCHEDULER FAILING — last launch exited {shifted}",
            detail,
            f'stderr: {h(str(HARNESS_DIR / "logs" / "orchestrator.stderr.log"))}',
        )

    scheduler_card = f"""<div style="border:1px solid {inst_color}55;border-radius:10px;
                        padding:12px 14px;margin-bottom:18px;
                        background:linear-gradient(90deg,rgba(0,0,0,0.2),transparent)">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
        <span style="font-size:14px">⏰</span>
        <span style="color:var(--text);font-weight:600;font-size:13px;flex:1">
          OS Scheduler</span>
        <span style="color:{inst_color};font-size:11px;font-weight:600;
                     text-transform:uppercase;letter-spacing:0.6px">{h(inst_text)}</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;
                  font-size:11px;color:var(--subtext1)">
        <div><div class="label">Mechanism</div>
             <div style="margin-top:2px">{h(scheduler.get("kind","—"))}</div></div>
        <div><div class="label">Next fire</div>
             <div style="margin-top:2px;color:var(--blue)">18:00 local ({h(nxt_rel)})</div></div>
        <div><div class="label">Last orchestrator activity</div>
             <div style="margin-top:2px">{h(last_act_str)}</div></div>
      </div>
      {install_hint}
    </div>"""

    intro = section_desc(
        "Each card below is a scheduled task wired into "
        "<code>scripts/daily-orchestrator.sh</code>. The orchestrator fires daily at "
        "18:00 local time; routines self-pace via their own MIN_GAP_DAYS guard. "
        "“Changes” shows the diff between the artifact this run produced and the "
        "snapshot from the previous run."
    )

    # ── Daily maintenance run status ─────────────────────────────────────────
    maintenance_html = ""
    if repos_data:
        orch_runs = hd.get("orchestrator_runs", [])
        latest_run: dict = {}
        for run in orch_runs:
            if run.get("runner") not in (None, "daily-maintenance"):
                continue
            p = run["path"]
            if p not in latest_run or run["ts"] > latest_run[p]["ts"]:
                latest_run[p] = run

        SCHED_HOUR = 18
        GRACE_SECS = 2 * 3600
        repo_cards = ""
        for rd in repos_data:
            path     = rd["path"]
            name     = Path(path).name
            last_run = rd.get("last_routine_run")
            run_info = latest_run.get(path)

            if last_run:
                now_local   = datetime.now()
                today_sched = now_local.replace(hour=SCHED_HOUR, minute=0, second=0, microsecond=0)
                last_local  = (last_run.astimezone().replace(tzinfo=None)
                               if last_run.tzinfo else last_run)
                if last_local.date() == now_local.date():
                    run_status = "ok"
                elif now_local < today_sched + timedelta(seconds=GRACE_SECS):
                    run_status = "pending"
                else:
                    run_status = "overdue"
                run_str = rel_time(last_run.isoformat())
                sbadge  = {"ok": badge("OK", "ok"),
                           "pending": badge("PENDING", "warn"),
                           "overdue": badge("OVERDUE", "missed")}[run_status]
                run_col = {"ok": "var(--green)", "pending": "var(--yellow)",
                           "overdue": "var(--red)"}[run_status]
            else:
                run_str = "never run"
                sbadge  = badge("UNKNOWN", "default")
                run_col = "var(--overlay0)"

            ec_html = ""
            if run_info:
                ec     = run_info["exit"]
                ec_col = "var(--green)" if ec == 0 else "var(--red)"
                ec_html = (f'<span style="color:{ec_col};font-size:10px"> · exit {ec}</span>')

            repo_cards += (
                f'<div style="background:var(--surface0);border-radius:6px;padding:10px 12px">'
                f'<div style="font-size:11px;font-weight:600;color:var(--text);margin-bottom:4px;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{h(name)}</div>'
                f'<div style="display:flex;gap:6px;align-items:center">{sbadge}'
                f'<span style="color:{run_col};font-size:10px">{h(run_str)}</span>{ec_html}</div></div>'
            )

        log_tail = ""
        if ORCH_LOG.exists():
            lines    = ORCH_LOG.read_text().splitlines()[-12:]
            log_tail = (
                f'<details style="margin-top:10px">'
                f'<summary style="cursor:pointer;font-size:9px;text-transform:uppercase;'
                f'letter-spacing:1px;color:var(--overlay0);list-style:none;padding:4px 0">'
                f'Orchestrator log (last 12 lines)</summary>'
                f'<pre style="background:var(--crust);border-radius:6px;padding:12px;margin-top:6px;'
                f'font-size:10px;color:var(--subtext0);overflow-x:auto;white-space:pre-wrap">'
                f'{h(chr(10).join(lines))}</pre></details>'
            )

        maintenance_html = (
            f'<div style="margin-bottom:20px">'
            f'<div class="label" style="margin-bottom:8px">Daily Maintenance (18:00 local)</div>'
            f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));'
            f'gap:8px">{repo_cards}</div>'
            f'{log_tail}</div>'
        )

    if not routines:
        return (f'<div class="section-inner">'
                f'<h2 class="section-title">Scheduled Tasks</h2>'
                f'{scheduler_alarm}{scheduler_card}{maintenance_html}{intro}'
                f'{empty_state("No scheduled tasks configured.")}</div>')

    # ── Per-routine cards ────────────────────────────────────────────────────
    cards = ""
    for r in routines:
        ms     = r["miss_status"]
        border = ("var(--red)"     if ms == "missed" else
                  "var(--yellow)"  if ms == "warn"   else "var(--surface0)")
        bg     = ("rgba(243,139,168,0.06)" if ms == "missed" else
                  "rgba(249,226,175,0.04)" if ms == "warn"   else "transparent")
        sbadge = (badge("MISSED",  "missed") if ms == "missed" else
                  badge("PENDING", "warn")   if ms == "warn"   else badge("OK", "ok"))

        # Artifact / changes
        art = r["artifact"]
        if art:
            uri      = Path(art["path"]).as_uri()
            rel_path = str(Path(art["path"]).relative_to(HARNESS_DIR)) \
                       if str(art["path"]).startswith(str(HARNESS_DIR)) else art["path"]
            size_b   = art["size_kb"] * 1024
            size_str = f"{int(size_b)} B" if size_b < 1024 else f"{art['size_kb']:.1f} KB"
            artifact_html = (
                f'<a href="{h(uri)}" target="_blank" '
                f'style="color:var(--mauve);font-size:11px;text-decoration:none">'
                f'{h(rel_path)} ↗</a> '
                f'<span style="color:var(--overlay0);font-size:10px;margin-left:6px">'
                f'{size_str} · {h(rel_time(art["mtime"]))}</span>'
            )
        else:
            artifact_html = (
                f'<span style="color:var(--overlay0);font-size:11px">'
                f'{h(r["artifact_label"])} <em>(not yet generated)</em></span>'
            )

        # Diff stats badge
        if art and (r["diff_added"] or r["diff_removed"]):
            diff_badge = (
                f'<span style="color:var(--green);font-size:11px">+{r["diff_added"]}</span>'
                f' <span style="color:var(--red);font-size:11px">−{r["diff_removed"]}</span>'
                f' <span style="color:var(--overlay0);font-size:10px"> vs. previous run</span>'
            )
        elif art:
            diff_badge = (f'<span style="color:var(--overlay0);font-size:11px">'
                          f'no changes vs. previous run</span>')
        else:
            diff_badge = ""

        # Reasoning / headline excerpt
        reasoning_html = ""
        if r["headline"]:
            short = r["headline"][:200]
            more  = r["headline"][200:] if len(r["headline"]) > 200 else ""
            more_html = (f'<span class="reasoning-more" style="display:none">'
                         f'{h(more)}</span>'
                         f' <a href="#" onclick="this.previousElementSibling.style.display=\'inline\';'
                         f'this.style.display=\'none\';return false" '
                         f'style="color:var(--mauve);font-size:11px">… more</a>') if more else ""
            reasoning_html = f"""<div style="margin-top:10px;padding:8px 12px;
                          background:rgba(49,50,68,0.4);border-radius:6px;
                          border-left:2px solid var(--mauve);font-size:11px;
                          color:var(--subtext1);line-height:1.6;font-style:italic">
        <div style="font-style:normal;color:var(--overlay0);font-size:10px;
                    font-weight:600;text-transform:uppercase;letter-spacing:0.6px;
                    margin-bottom:4px">Reasoning from this run</div>
        “{h(short)}{more_html}”
      </div>"""

        # Last-exit indicator
        if r["last_exit"] is None:
            exit_html = '<span style="color:var(--overlay0)">—</span>'
        elif r["last_exit"] == 0:
            exit_html = (f'<span style="color:var(--green)">exit=0</span>'
                         f' <span style="color:var(--overlay0)">({r["last_duration"]}s)</span>')
        else:
            exit_html = (f'<span style="color:var(--red)">exit={r["last_exit"]}</span>'
                         f' <span style="color:var(--overlay0)">({r["last_duration"]}s)</span>')

        # History strip (last 5 runs)
        hist_html = ""
        if r["history"]:
            dots = ""
            for rec in r["history"][:8]:
                color = "var(--green)" if rec["exit"] == 0 else "var(--red)"
                ts    = rec["ts"][:16].replace("T", " ")
                title = f"{ts} · {rec['repo']} · exit={rec['exit']} · {rec['duration']}s"
                dots += (f'<span title="{h(title)}" style="display:inline-block;'
                         f'width:8px;height:8px;border-radius:2px;background:{color};'
                         f'margin-right:3px"></span>')
            hist_html = (f'<div style="margin-top:8px;font-size:10px;color:var(--overlay0)">'
                         f'Recent: {dots}</div>')

        # Per-repo scope hint
        scope_html = ""
        if r["scope"] == "per-repo":
            scope_html = ('<span style="color:var(--overlay0);font-size:10px;margin-left:6px">'
                          'per-repo</span>')
        else:
            scope_html = ('<span style="color:var(--overlay0);font-size:10px;margin-left:6px">'
                          'harness</span>')

        cards += f"""<div style="border:1px solid {border};border-radius:10px;
                        overflow:hidden;margin-bottom:14px;background:{bg}">
      <div style="background:rgba(0,0,0,0.2);padding:10px 14px;
                  display:flex;align-items:center;gap:10px;
                  border-bottom:1px solid {border}33">
        <span style="color:var(--text);font-weight:600;font-size:13px">
          {h(r["name"])}</span>{scope_html}
        <span style="flex:1"></span>
        {sbadge}
      </div>
      <div style="padding:12px 14px">
        <div style="color:var(--overlay0);font-size:11px;margin-bottom:10px;font-style:italic">
          {h(r["what_it_does"])}
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:10px">
          <div><div class="label">Schedule</div>
               <div style="color:var(--subtext1);font-size:12px;margin-top:3px">
                 {h(r["schedule_human"])}</div></div>
          <div><div class="label">Last ran</div>
               <div style="color:var(--subtext1);font-size:12px;margin-top:3px">
                 {h(r["last_run_rel"])} &nbsp;{exit_html}</div></div>
          <div><div class="label">Artifact</div>
               <div style="margin-top:3px">{artifact_html}</div>
               <div style="margin-top:3px">{diff_badge}</div></div>
        </div>
        {reasoning_html}
        {hist_html}
      </div>
    </div>"""

    return (f'<div class="section-inner">'
            f'<h2 class="section-title">Scheduled Tasks</h2>'
            f'{scheduler_alarm}{scheduler_card}{maintenance_html}{intro}{cards}</div>')

def render_memory_changes(rd, hd):
    cards    = rd.get("memory_cards", [])
    obs      = rd.get("observations", {})

    git_entries     = [e for e in rd.get("memory_changes", []) if e.get("hash")]
    harness_entries = hd.get("harness_memory", [])

    git_based = bool(git_entries)
    desc_text = (
        "Git commits that touched memory files — shown alongside live file content below."
        if git_based else
        "<strong style='color:var(--text)'>.claude/memory/ is gitignored in this repo</strong> — "
        "git history is unavailable. Showing live file content and the observations feed instead. "
        "Files are updated by daily-maintenance (judge · reflect · housekeep) and Claude sessions."
    )

    # ── Observations feed (all tags, newest-first) ────────────────────────────
    all_obs = sorted(
        [(d, tag, t) for tag, entries in obs.items() for d, t in entries],
        key=lambda x: x[0], reverse=True
    )

    TAG_COLORS = {
        "judge":            "#cba6f7",
        "session-quality":  "#89b4fa",
        "keep-rate":        "#94e2d5",
        "memory-gap":       "#f9e2af",
        "debug":            "#a6e3a1",
        "impl":             "#a6e3a1",
        "decision":         "#fab387",
        "insight":          "#89b4fa",
        "design":           "#cba6f7",
        "spec":             "#cba6f7",
        "friction":         "#f38ba8",
        "pattern":          "#94e2d5",
        "kaizen":           "#fab387",
    }

    obs_rows = ""
    for date_str, tag, text in all_obs[:50]:
        tc = TAG_COLORS.get(tag, "#a6adc8")
        tag_pill = (
            f'<span style="font-size:9px;padding:1px 5px;border-radius:8px;'
            f'background:{tc}18;color:{tc};flex-shrink:0;font-weight:600">'
            f'[{h(tag)}]</span>'
        )
        obs_rows += (
            f'<div style="padding:6px 0;border-bottom:1px solid rgba(49,50,68,0.4);'
            f'display:flex;gap:8px;align-items:baseline">'
            f'<span style="color:var(--overlay0);font-size:10px;flex-shrink:0;min-width:72px">'
            f'{h(date_str)}</span>'
            f'{tag_pill}'
            f'<span style="flex:1;font-size:11px;color:var(--subtext1);line-height:1.5">'
            f'{h(text)}</span>'
            f'</div>'
        )

    # ── Memory file cards ──────────────────────────────────────────────────────
    file_cards = ""
    for c in cards:
        trunc_note = (
            f'<span style="color:var(--overlay0);font-size:9px"> (truncated at 4000 chars)</span>'
            if c.get("truncated") else ""
        )
        file_cards += (
            f'<details style="border:1px solid var(--surface0);border-radius:7px;'
            f'overflow:hidden;margin-bottom:6px">'
            f'<summary style="display:flex;align-items:center;gap:10px;padding:7px 12px;'
            f'background:var(--base);cursor:pointer">'
            f'<code style="color:var(--mauve);font-size:11px;flex:1">{h(c["name"])}</code>'
            f'<span style="color:var(--overlay0);font-size:10px">{c["lines"]} lines</span>'
            f'<span style="color:var(--overlay0);font-size:10px">{h(c["rel"])}</span>'
            f'</summary>'
            f'<div style="padding:10px 12px;background:rgba(0,0,0,0.15)">'
            f'<pre style="font-size:10px;color:var(--subtext0);white-space:pre-wrap;'
            f'word-break:break-word;margin:0;line-height:1.6">'
            f'{h(c["content"])}</pre>'
            f'{trunc_note}'
            f'</div>'
            f'</details>'
        )

    if not obs_rows and not file_cards:
        return f"""<div class="section-inner">
  <h2 class="section-title">Memory Changes</h2>
  {section_desc(desc_text)}
  {empty_state("No memory files found in .claude/memory/")}
</div>"""

    # ── Memory file cards with diff ───────────────────────────────────────────
    file_cards_html = ""
    for c in cards:
        # Status chip
        ds = c.get("diff_summary", "")
        if c.get("diff_lines"):
            chip_color, chip_bg = "#a6e3a1", "rgba(166,227,161,0.12)"
        elif "unchanged" in ds:
            chip_color, chip_bg = "#6c7086", "rgba(108,112,134,0.1)"
        else:
            chip_color, chip_bg = "#89b4fa", "rgba(137,180,250,0.1)"
        status_chip = (
            f'<span style="font-size:9px;padding:2px 7px;border-radius:8px;'
            f'background:{chip_bg};color:{chip_color};white-space:nowrap">'
            f'{h(ds)}</span>'
        )

        # Diff view (if there are changes)
        diff_html = ""
        if c.get("diff_lines"):
            rendered = ""
            for line in c["diff_lines"][:80]:
                if line.startswith("+") and not line.startswith("+++"):
                    rendered += (f'<div style="color:#a6e3a1;background:rgba(166,227,161,0.07);'
                                 f'white-space:pre-wrap;word-break:break-all">{h(line)}</div>')
                elif line.startswith("-") and not line.startswith("---"):
                    rendered += (f'<div style="color:#f38ba8;background:rgba(243,139,168,0.07);'
                                 f'white-space:pre-wrap;word-break:break-all">{h(line)}</div>')
                elif line.startswith("@@"):
                    rendered += (f'<div style="color:#89b4fa;opacity:0.6;'
                                 f'white-space:pre-wrap">{h(line)}</div>')
            diff_html = (
                f'<div style="padding:8px 12px;border-top:1px solid var(--surface0);'
                f'background:rgba(0,0,0,0.15)">'
                f'<div style="font-size:9px;text-transform:uppercase;letter-spacing:1px;'
                f'color:var(--overlay0);margin-bottom:6px">Diff (since last snapshot)</div>'
                f'<pre style="font-size:10px;line-height:1.55;margin:0">{rendered}</pre>'
                f'</div>'
            )

        # Full content — open by default when no diff, collapsed when diff is shown
        trunc = ('<span style="color:var(--overlay0);font-size:9px"> …truncated</span>'
                 if c.get("truncated") else "")
        open_attr = "" if c.get("diff_lines") else " open"
        full_html = (
            f'<details{open_attr} style="border-top:1px solid var(--surface0)">'
            f'<summary style="padding:5px 12px;font-size:9px;text-transform:uppercase;'
            f'letter-spacing:1px;color:var(--overlay0);cursor:pointer;list-style:none">'
            f'Full content ({c["lines"]} lines){trunc}</summary>'
            f'<div style="padding:8px 12px;background:rgba(0,0,0,0.1)">'
            f'<pre style="font-size:10px;color:var(--subtext0);white-space:pre-wrap;'
            f'word-break:break-word;margin:0;line-height:1.6">{h(c["content"])}</pre>'
            f'</div></details>'
        )

        file_cards_html += (
            f'<div style="border:1px solid var(--surface0);border-radius:7px;'
            f'overflow:hidden;margin-bottom:8px">'
            f'<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;'
            f'background:var(--base)">'
            f'<code style="color:var(--mauve);font-size:11px;flex:1">{h(c["name"])}</code>'
            f'{status_chip}'
            f'<span style="color:var(--overlay0);font-size:10px">{h(c["rel"])}</span>'
            f'</div>'
            f'{diff_html}'
            f'{full_html}'
            f'</div>'
        )

    if not obs_rows and not file_cards_html:
        return f"""<div class="section-inner">
  <h2 class="section-title">Memory Changes</h2>
  {section_desc(desc_text)}
  {empty_state("No memory files found in .claude/memory/")}
</div>"""

    n_obs   = len(all_obs)
    n_cards = len(cards)
    # Tab buttons use the global switchTab() from JS_TEMPLATE — no inline script needed
    return f"""<div class="section-inner">
  <h2 class="section-title">Memory Changes</h2>
  {section_desc(desc_text)}
  <div style="display:flex;gap:0;margin-bottom:14px;border-bottom:1px solid var(--surface0)">
    <button onclick="switchTab('mc','obs')"   id="mc-tab-obs"
            style="padding:6px 14px;font-size:11px;font-weight:600;cursor:pointer;border:none;
                   border-bottom:2px solid var(--mauve);background:transparent;color:var(--mauve)">
      Observations ({n_obs})</button>
    <button onclick="switchTab('mc','files')" id="mc-tab-files"
            style="padding:6px 14px;font-size:11px;font-weight:600;cursor:pointer;border:none;
                   border-bottom:2px solid transparent;background:transparent;color:var(--overlay0)">
      Memory Files ({n_cards})</button>
  </div>
  <div id="mc-pane-obs">{obs_rows if obs_rows else empty_state("No observations yet.")}</div>
  <div id="mc-pane-files" style="display:none">
    {file_cards_html if file_cards_html else empty_state("No .md files in .claude/memory/")}
  </div>
</div>"""

def render_skill_usage():
    """Hot/cold skill statistics from the skill-usage-tracker hook log.

    The PostToolUse(Skill) hook appends one JSON line per skill invocation to
    logs/skill-usage.jsonl. This surfaces the evidence the skill-curator uses
    to deprecate cold skills (Hermes-agent usage-log pattern). Returns '' when
    no log exists yet, so it composes cleanly above the curation report.
    """
    log_path = Path.home() / ".claude" / "sdd-harness" / "logs" / "skill-usage.jsonl"
    if not log_path.exists():
        return (
            '<div class="label" style="margin-bottom:6px">Skill Usage</div>'
            + empty_state(
                "No usage data yet. The <code>skill-usage-tracker.sh</code> "
                "PostToolUse hook logs every skill invocation here. Data appears "
                "after the first skill fires."
            )
            + '<div style="height:20px"></div>'
        )

    entries = []
    try:
        with log_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return ""

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)

    total_fires = 0
    fires_30d = 0
    counts_30d = {}        # skill -> count in last 30d
    last_seen = {}         # skill -> latest datetime
    for e in entries:
        sk = e.get("skill", "")
        if not sk:
            continue
        total_fires += 1
        ts = None
        try:
            ts = datetime.fromisoformat(e.get("ts", "").replace("Z", "+00:00"))
        except Exception:
            ts = None
        if ts is not None:
            if last_seen.get(sk) is None or ts > last_seen[sk]:
                last_seen[sk] = ts
            if ts >= cutoff:
                fires_30d += 1
                counts_30d[sk] = counts_30d.get(sk, 0) + 1

    # Installed skills → compute cold (never-fired-in-30d) candidates.
    installed = set()
    skills_root = Path.home() / ".claude" / "skills"
    try:
        for p in skills_root.glob("*/SKILL.md"):
            installed.add(p.parent.name)
    except Exception:
        pass

    fired_30d_set = set(counts_30d.keys())
    cold = sorted(installed - fired_30d_set) if installed else []
    used_30d = len(fired_30d_set)
    cold_n = len(cold)

    cn = ("var(--green)" if cold_n == 0 else
          "var(--yellow)" if cold_n < 40 else "var(--red)")

    summary = f"""<div style="display:grid;grid-template-columns:repeat(4,1fr);
                       gap:12px;margin-bottom:16px">
    <div class="stat-card">
      <div class="stat-val">{total_fires}</div>
      <div class="stat-lbl">total invocations</div></div>
    <div class="stat-card">
      <div class="stat-val">{fires_30d}</div>
      <div class="stat-lbl">invocations (30d)</div></div>
    <div class="stat-card">
      <div class="stat-val" style="color:var(--blue)">{used_30d}</div>
      <div class="stat-lbl">skills used (30d)</div></div>
    <div class="stat-card">
      <div class="stat-val" style="color:{cn}">{cold_n}</div>
      <div class="stat-lbl">cold skills (30d)</div></div>
  </div>"""

    top = sorted(counts_30d.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    top_html = ""
    if top:
        mx = top[0][1]
        rows = ""
        for sk, c in top:
            w = int(c / mx * 100) if mx else 0
            rows += (
                f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
                f'<div style="flex:0 0 180px;font-size:11px;color:var(--subtext1);'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{h(sk)}</div>'
                f'<div style="flex:1;background:var(--surface0);border-radius:3px;height:14px">'
                f'<div style="width:{w}%;background:var(--blue);height:100%;border-radius:3px;'
                f'opacity:0.8"></div></div>'
                f'<div style="flex:0 0 32px;text-align:right;font-size:11px;'
                f'color:var(--overlay1)">{c}</div></div>'
            )
        top_html = (
            '<div class="label" style="margin-bottom:6px">Top skills (30d)</div>'
            f'<div style="margin-bottom:16px">{rows}</div>'
        )

    cold_html = ""
    if cold:
        sample = ", ".join(h(s) for s in cold[:25])
        more = f" <span style='color:var(--overlay0)'>+{cold_n - 25} more</span>" if cold_n > 25 else ""
        cold_html = (
            '<div class="label" style="margin-bottom:6px">Cold skills — '
            'deprecate candidates (no invocation in 30d)</div>'
            f'<div style="font-size:11px;color:var(--subtext0);line-height:1.6;'
            f'margin-bottom:20px">{sample}{more}<br>'
            '<span style="color:var(--overlay0)">The weekly skill-curator uses this '
            'list as evidence for pruning. Pin a skill to protect it.</span></div>'
        )

    return (
        '<div class="label" style="margin-bottom:6px;font-size:13px;color:var(--text)">'
        'Skill Usage</div>'
        + summary + top_html + cold_html
    )

def render_skill_changes(hd, companion=False):
    usage    = render_skill_usage()
    content  = hd.get("skill_report_content")
    last_mod = hd.get("skill_report_age")
    if not content:
        sc = next((r for r in hd.get("scheduled_tasks", [])
                   if r["key"] == "skill-curator"), None)
        last_run = sc["last_run_rel"] if sc else "never"
        d = section_desc(
            "The weekly <em>Skill-Curator</em> scheduled task audits all skills in "
            "<code>~/.claude/skills/</code>, prunes stale ones, and writes a report here. "
            f"<strong style='color:var(--text)'>Last run: {last_run}</strong>. "
            "Until then, skill changes are visible via <em>Automation Audit</em> (session-quality entries)."
        )
        return f"""<div class="section-inner">
  <h2 class="section-title">Skill Changes</h2>
  {usage}
  {d}
  {empty_state("No report yet — waiting for first scheduled run.")}
</div>"""

    proposal_content, proposal_age = read_skill_proposal()

    action_html = ""
    if companion:
        if proposal_content:
            action_html = f"""<div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--surface0)">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
      <span style="color:var(--overlay0);font-size:12px">
        Proposal generated: <strong style="color:var(--subtext1)">{h(proposal_age)}</strong></span>
    </div>
    <div style="font-size:12px;line-height:1.7;margin-bottom:12px">{mini_md(proposal_content)}</div>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <input id="sc-instruction" type="text" value="apply all"
             style="background:var(--surface0);color:var(--text);border:1px solid var(--overlay0);
                    border-radius:6px;padding:8px 12px;font-size:12px;flex:1;min-width:180px"/>
      <button id="sc-apply-btn"
              style="background:var(--green);color:var(--crust);border:none;border-radius:6px;
                     padding:9px 18px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap"
              onclick="runSkillCuratorApply()">✅ Apply Approved</button>
      <button id="sc-propose-btn"
              style="background:transparent;color:var(--subtext0);border:1px solid var(--surface0);
                     border-radius:6px;padding:9px 14px;font-size:12px;cursor:pointer;white-space:nowrap"
              onclick="runSkillCuratorPropose()">🔍 Re-analyze</button>
    </div>
  </div>"""
        else:
            action_html = f"""<div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--surface0)">
    <button id="sc-propose-btn"
            style="background:var(--mauve);color:var(--crust);border:none;border-radius:6px;
                   padding:9px 18px;font-size:12px;font-weight:600;cursor:pointer"
            onclick="runSkillCuratorPropose()">🔍 Analyze &amp; Propose</button>
  </div>"""
    elif proposal_content:
        action_html = f"""<div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--surface0)">
    <div style="color:var(--overlay0);font-size:12px;margin-bottom:10px">
      Proposal generated: <strong style="color:var(--subtext1)">{h(proposal_age)}</strong></div>
    <div style="font-size:12px;line-height:1.7">{mini_md(proposal_content)}</div>
  </div>"""

    return f"""<div class="section-inner">
  <h2 class="section-title">Skill Changes</h2>
  {usage}
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px">
    <span style="color:var(--overlay0);font-size:12px">
      Last audit: <strong style="color:var(--subtext1)">{h(last_mod)}</strong></span>
    {badge("Weekly", "info")}
  </div>
  <div style="font-size:12px;line-height:1.7">{mini_md(content)}</div>
  <div id="sc-panel">{action_html}</div>
</div>"""

def _render_gap_details(gap_records, gap_observations):
    """Collapsible list of what was actually re-explained, newest day first.

    A count on its own is not actionable — the point of the tile is to name the
    topic so it can be written into memory. Topics come from the detector's own
    structured output; the prose observation is shown underneath for context.
    """
    days = [r for r in gap_records if r.get("value")]
    if not days:
        return ""

    prose_by_date = dict(gap_observations)
    blocks = ""
    for r in reversed(days[-10:]):
        topics = r.get("meta", {}).get("topics") or []
        items = "".join(
            f'<li style="margin:4px 0">{h(str(t))}</li>' for t in topics
        ) or '<li style="margin:4px 0;opacity:0.6">no topic recorded</li>'
        prose = prose_by_date.get(r["date"], "")
        note = (f'<div style="margin-top:6px;padding-top:6px;border-top:1px solid var(--surface1);'
                f'opacity:0.75">{h(prose)}</div>' if prose else "")
        blocks += (
            f'<details style="background:var(--surface0);border-radius:6px;'
            f'padding:8px 12px;margin-bottom:6px">'
            f'<summary style="cursor:pointer;color:var(--subtext1);font-size:12px">'
            f'{h(r["date"])} — {r["value"]:.0f} re-explanation'
            f'{"" if r["value"] == 1 else "s"}</summary>'
            f'<ul style="padding-left:18px;margin:8px 0 0;color:var(--subtext0);'
            f'font-size:12px;line-height:1.5">{items}</ul>{note}</details>'
        )

    total = sum(r["value"] for r in days)
    return (
        f'<div class="label" style="margin-bottom:6px">What you had to re-explain '
        f'({total:.0f} across {len(days)} day{"" if len(days) == 1 else "s"})</div>'
        f'<div style="margin-bottom:16px">{blocks}</div>'
    )

def render_session_quality(rd):
    # Every number here comes from metrics.jsonl. Nothing is extracted from the
    # prose in observations.md — that is what produced a 5% AI-adoption reading
    # from a 24.5% measurement. Prose below is quoted for context only.
    metrics = rd.get("metrics", {})
    scores  = metrics.get("session-quality", [])
    keeps   = metrics.get("keep-rate", [])
    gaps    = metrics.get("memory-gap", [])
    mg      = rd["observations"].get("memory-gap", [])

    if not scores and not keeps:
        return empty_state(
            "No session quality data yet. Run daily-maintenance to start tracking "
            "(measurements land in .claude/memory/metrics.jsonl)."
        )

    # Idle-routine windows have no user session to judge. The routine still writes
    # a neutral 3/5 placeholder so the day is accounted for, but averaging those in
    # drags the number toward mediocre by construction — so they are excluded.
    live_scores = [r for r in scores if not r.get("idle")]
    idle_count  = len(scores) - len(live_scores)

    avg_score  = sum(r["value"] for r in live_scores) / len(live_scores) if live_scores else None
    avg_keep   = sum(r["value"] for r in keeps) / len(keeps) if keeps else None
    total_gaps = sum(r["value"] for r in gaps)
    gaps_measured = bool(gaps)

    sc  = ("#a6e3a1" if (avg_score or 0) >= 4 else
           "#f9e2af" if (avg_score or 0) >= 2.5 else "#f38ba8")
    kc  = ("#a6e3a1" if (avg_keep or 0) >= 70 else
           "#f9e2af" if (avg_keep or 0) >= 40  else "#f38ba8")
    # No measurement is not the same as zero gaps. An unmeasured detector shows "—".
    gc  = ("var(--red)"    if total_gaps > 5 else
           "var(--yellow)" if total_gaps > 0  else "var(--green)")

    score_note = (f'<div class="stat-lbl" style="font-size:9px;opacity:0.7">'
                  f'{idle_count} idle day{"s" if idle_count != 1 else ""} excluded</div>'
                  if idle_count else "")

    summary = f"""<div style="display:grid;grid-template-columns:repeat(3,1fr);
                       gap:12px;margin-bottom:20px">
    <div class="stat-card">
      <div class="stat-val" style="color:{sc}">
        {f"{avg_score:.1f}/5" if avg_score is not None else "—"}</div>
      <div class="stat-lbl">avg session score</div>{score_note}</div>
    <div class="stat-card">
      <div class="stat-val" style="color:{kc}">
        {f"{avg_keep:.0f}%" if avg_keep is not None else "—"}</div>
      <div class="stat-lbl">avg keep-rate</div></div>
    <div class="stat-card">
      <div class="stat-val" style="color:{gc if gaps_measured else 'var(--overlay0)'}">
        {f"{total_gaps:.0f}" if gaps_measured else "—"}</div>
      <div class="stat-lbl">memory gaps{"" if gaps_measured else " (not measured)"}</div></div>
  </div>"""

    timeline = ""
    if live_scores:
        bars = ""
        for r in live_scores[-20:]:
            d, s = r["date"], r["value"]
            bh = int(s / 5 * 44)
            bc = ("#a6e3a1" if s >= 4 else "#f9e2af" if s >= 2.5 else "#f38ba8")
            bars += (
                f'<div title="{h(d)}: {s:.0f}/5" style="flex:1;display:flex;flex-direction:column;'
                f'align-items:center;justify-content:flex-end;gap:2px;min-width:14px">'
                f'<div style="font-size:8px;color:{bc};font-weight:600">{s:.0f}</div>'
                f'<div style="background:{bc};height:{bh}px;width:100%;border-radius:2px 2px 0 0;'
                f'opacity:0.85"></div></div>'
            )
        timeline = (
            f'<div class="label" style="margin-bottom:6px">Session scores '
            f'(recent, 0–5, idle days excluded)</div>'
            f'<div style="display:flex;align-items:flex-end;gap:3px;height:64px;'
            f'margin-bottom:16px">{bars}</div>'
        )

    recent = _render_gap_details(gaps, mg)

    slop = metrics.get("sloppiness", [])
    sloppiness_card = ""
    sloppiness_glossary = ""
    if slop:
        avg_verbosity = sum(r["value"] for r in slop) / len(slop)
        high_slop_days = sum(1 for r in slop if r.get("meta", {}).get("verdict") == "high-slop")
        slc = ("#a6e3a1" if avg_verbosity < 0.15 else
               "#f9e2af" if avg_verbosity < 0.33 else "#f38ba8")
        sloppiness_card = f"""<div style="display:grid;grid-template-columns:repeat(2,1fr);
                           gap:12px;margin-bottom:20px">
    <div class="stat-card">
      <div class="stat-val" style="color:{slc}">{avg_verbosity:.2f}</div>
      <div class="stat-lbl">avg verbosity (sloppiness)</div></div>
    <div class="stat-card">
      <div class="stat-val" style="color:{'var(--red)' if high_slop_days else 'var(--green)'}">
        {high_slop_days}</div>
      <div class="stat-lbl">high-slop day{"s" if high_slop_days != 1 else ""}</div></div>
  </div>"""
        sloppiness_glossary = """<div style="background:var(--surface0);border-radius:6px;padding:10px 12px;font-size:11px;margin-top:10px">
      <div style="color:var(--yellow);font-weight:600;margin-bottom:4px">🧹 Sloppiness (Verbosity)</div>
      <div style="color:var(--subtext0);line-height:1.55">
        Exact-line-dedup ratio of the last file written each day, scored by
        <code style="font-size:10px">sloppiness-score.sh</code> — a non-LLM-judge proxy
        for duplicated code, not a rigorous AST tool. Human baseline ~0.15, AI-agent
        baseline ~0.33; days at/above the AI-agent baseline on either verbosity or
        erosion count as high-slop. See the <code style="font-size:10px">clean-code</code> skill.
      </div>
    </div>"""

    glossary = """<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:20px">
    <div style="background:var(--surface0);border-radius:6px;padding:10px 12px;font-size:11px">
      <div style="color:var(--blue);font-weight:600;margin-bottom:4px">📊 Session Score (0–5)</div>
      <div style="color:var(--subtext0);line-height:1.55">
        Judged by the <code style="font-size:10px">session-quality</code> skill during
        daily-maintenance, from the day's git activity: reverts, files reworked, forward commits.
        Days with no user session are recorded but excluded from the average.
        <strong style="color:var(--subtext1)">Different from trust battery</strong> —
        trust is cumulative long-term; session score is per-session behavior.
      </div>
    </div>
    <div style="background:var(--surface0);border-radius:6px;padding:10px 12px;font-size:11px">
      <div style="color:var(--teal);font-weight:600;margin-bottom:4px">📌 Keep-Rate %</div>
      <div style="color:var(--subtext0);line-height:1.55">
        Of the lines Claude committed in the last 30 days, what % is still in HEAD
        (<code style="font-size:10px">git blame</code> against the commits that carry the
        Co-Authored-By trailer). 100% = nothing rewritten. Doc-sync churn and Claude
        rewriting its own earlier lines both count as loss.
      </div>
    </div>
    <div style="background:var(--surface0);border-radius:6px;padding:10px 12px;font-size:11px">
      <div style="color:var(--yellow);font-weight:600;margin-bottom:4px">🧠 Memory Gaps</div>
      <div style="color:var(--subtext0);line-height:1.55">
        Times you had to re-explain something Claude should have remembered.
        Detected at session end by <code style="font-size:10px">detect_reexplanation.py</code>.
        A dash means the detector never ran — that is not the same as zero.
      </div>
    </div>
  </div>"""
    return f"""<div class="section-inner">
  <h2 class="section-title">Session Quality</h2>
  {summary}{sloppiness_card}{timeline}{recent}
  {glossary}{sloppiness_glossary}
</div>"""

def render_prompt_quality():
    log_path = Path.home() / ".code-insights" / "pq-log.jsonl"
    if not log_path.exists():
        return empty_state(
            "No prompt quality data yet. The <code>prompt-quality-check.sh</code> hook scores "
            "every Agent tool call and logs here. Data appears after the first agent spawn."
        )

    entries = []
    try:
        with log_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        return empty_state("Could not read prompt quality log.")

    if not entries:
        return empty_state("Prompt quality log is empty.")

    recent = entries[-50:]
    scores = [(e["ts"][:10], e["overall"]) for e in recent
              if isinstance(e.get("overall"), (int, float))]
    if not scores:
        return empty_state("No scored entries yet.")

    DIM_KEYS = ["context_provision", "request_specificity", "scope_management", "information_timing"]
    DIM_LABELS = {
        "context_provision":   "context provision",
        "request_specificity": "request specificity",
        "scope_management":    "scope management",
        "information_timing":  "information timing",
    }
    dim_avgs: dict[str, float] = {}
    for dk in DIM_KEYS:
        vals = [e["dims"][dk] for e in recent
                if isinstance(e.get("dims", {}).get(dk), (int, float))]
        if vals:
            dim_avgs[dk] = round(sum(vals) / len(vals), 1)

    overall_avg = round(sum(s for _, s in scores) / len(scores), 1)
    oc = ("#a6e3a1" if overall_avg >= 4.0 else
          "#f9e2af" if overall_avg >= 3.0 else "#f38ba8")

    # Summary strip
    total_spawns = len(entries)
    weakest_dim  = min(dim_avgs, key=lambda k: dim_avgs[k]) if dim_avgs else None
    weakest_val  = dim_avgs[weakest_dim] if weakest_dim else None
    summary = f"""<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px">
    <div class="stat-card">
      <div class="stat-val" style="color:{oc}">{overall_avg}/5</div>
      <div class="stat-lbl">avg PQ score (last {len(scores)})</div></div>
    <div class="stat-card">
      <div class="stat-val" style="color:var(--subtext1)">{total_spawns}</div>
      <div class="stat-lbl">total agent spawns scored</div></div>
    <div class="stat-card">
      <div class="stat-val" style="color:#f9e2af;font-size:13px">
        {h(DIM_LABELS.get(weakest_dim, "—")) if weakest_dim else "—"}</div>
      <div class="stat-lbl">weakest dimension ({weakest_val if weakest_val else "—"}/5)</div></div>
  </div>"""

    # Dimension bars
    dim_bars = ""
    for dk in DIM_KEYS:
        v = dim_avgs.get(dk)
        if v is None:
            continue
        bc = ("#a6e3a1" if v >= 4.0 else "#f9e2af" if v >= 3.0 else "#f38ba8")
        bw = int(v / 5 * 100)
        dim_bars += (
            f'<div style="margin-bottom:10px">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:3px">'
            f'<span style="font-size:11px;color:var(--subtext1)">{h(DIM_LABELS[dk])}</span>'
            f'<span style="font-size:11px;color:{bc};font-weight:600">{v}/5</span></div>'
            f'<div style="background:var(--surface0);border-radius:4px;height:6px">'
            f'<div style="background:{bc};width:{bw}%;height:6px;border-radius:4px;opacity:0.85"></div>'
            f'</div></div>'
        )

    # Score trend (last 20)
    trend_bars = ""
    for d, s in scores[-20:]:
        bh = max(4, int(s / 5 * 44))
        bc = "#a6e3a1" if s >= 4.0 else "#f9e2af" if s >= 3.0 else "#f38ba8"
        trend_bars += (
            f'<div title="{h(d)}: {s}/5" style="flex:1;display:flex;flex-direction:column;'
            f'align-items:center;justify-content:flex-end;gap:2px;min-width:10px">'
            f'<div style="font-size:8px;color:{bc};font-weight:600">{s}</div>'
            f'<div style="background:{bc};height:{bh}px;width:100%;border-radius:2px 2px 0 0;opacity:0.85"></div>'
            f'</div>'
        )
    trend = (
        f'<div class="label" style="margin-bottom:6px">Score trend (last {min(len(scores),20)} spawns)</div>'
        f'<div style="display:flex;align-items:flex-end;gap:2px;height:64px;margin-bottom:20px">{trend_bars}</div>'
    ) if trend_bars else ""

    glossary = """<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:16px">
    <div style="background:var(--surface0);border-radius:6px;padding:10px 12px;font-size:11px">
      <div style="color:var(--blue);font-weight:600;margin-bottom:4px">✨ What is PQ score?</div>
      <div style="color:var(--subtext0);line-height:1.55">
        Heuristic score 1–5 per agent spawn across 4 active dimensions (context provision, request
        specificity, scope management, information timing). Logged by
        <code style="font-size:10px">prompt-quality-check.sh</code> on every Agent tool call.
        Low scores predict agent confusion, wasted spawns, and rework.
      </div>
    </div>
    <div style="background:var(--surface0);border-radius:6px;padding:10px 12px;font-size:11px">
      <div style="color:var(--teal);font-weight:600;margin-bottom:4px">🛠 How to improve</div>
      <div style="color:var(--subtext0);line-height:1.55">
        Invoke the <code style="font-size:10px">prompt-quality-assess</code> skill before
        writing agent prompts. It applies the same 6-dimension rubric with concrete rewrite
        patterns for each weak dimension. Target ≥4.0 average overall.
      </div>
    </div>
  </div>"""

    return f"""<div class="section-inner">
  <h2 class="section-title">Prompt Quality</h2>
  {section_desc("Scores every agent spawn against 6 PQ dimensions (heuristic, no LLM required). "
                "Logged to <code>~/.code-insights/pq-log.jsonl</code>. Invoke <code>prompt-quality-assess</code> skill to improve before spawning.",
                icon="✨", color="var(--mauve)")}
  {summary}
  <div style="margin-bottom:20px">{dim_bars}</div>
  {trend}
  {glossary}
</div>"""


def _startup_payload_card(repo_path):
    """Render the startup-payload audit card (fixed per-session token tax).

    Reads .claude/reports/context/startup-payload.json produced by the
    startup-payload-audit routine. Returns "" if no audit has run yet.
    """
    try:
        f = Path(repo_path) / ".claude" / "reports" / "context" / "startup-payload.json"
        if not f.is_file():
            return ""
        data = json.loads(f.read_text())
    except Exception:
        return ""

    total   = data.get("total_tokens", 0)
    budget  = data.get("budget", 0)
    over    = data.get("over_budget", False)
    fcount  = data.get("file_count", 0)
    stale   = data.get("stale_count", 0)
    ghosts  = data.get("ghosts", []) or []
    files   = data.get("files", []) or []
    gen     = data.get("generated", "")
    # Ratchet fields — absent in reports written before the ceiling existed.
    ceiling      = data.get("ceiling")
    delta        = data.get("delta")
    over_ceiling = data.get("over_ceiling", False)

    status_color = "#f38ba8" if over else ("#f9e2af" if over_ceiling else "#a6e3a1")
    status_text  = f"over budget ({budget:,})" if over else f"within budget ({budget:,})"
    status_badge = badge(status_text, "missed" if over else "ok")
    if over_ceiling:
        status_badge += badge(f"above ceiling ({delta:+,})", "missed")

    if ceiling is None:
        ceiling_val, ceiling_lbl = "—", "ceiling (not yet set)"
    else:
        ceiling_val = f"{ceiling:,}"
        ceiling_lbl = "ceiling" if delta is None else f"ceiling · {delta:+,} vs last"

    # Top files by token weight
    rows = ""
    for fe in files[:5]:
        stale_tag = (' <span style="color:#f9e2af;font-size:9px">stale</span>'
                     if fe.get("stale") else "")
        rows += (
            f'<tr style="border-bottom:1px solid var(--surface1)">'
            f'<td style="padding:4px 10px;font-size:11px;color:var(--subtext1);font-family:monospace">{h(fe.get("path",""))}{stale_tag}</td>'
            f'<td style="padding:4px 10px;font-size:11px;color:var(--subtext0);text-align:right">{fe.get("tokens",0):,} tok</td>'
            f'<td style="padding:4px 10px;font-size:10px;color:var(--overlay0);text-align:right">{fe.get("age_days",0)}d</td>'
            f'</tr>'
        )
    files_table = (
        '<table style="width:100%;border-collapse:collapse;margin-top:8px">'
        '<thead><tr style="background:var(--base)">'
        '<th style="padding:4px 10px;text-align:left;font-size:9px;color:var(--overlay0)">FILE</th>'
        '<th style="padding:4px 10px;text-align:right;font-size:9px;color:var(--overlay0)">TOKENS</th>'
        '<th style="padding:4px 10px;text-align:right;font-size:9px;color:var(--overlay0)">AGE</th>'
        f'</tr></thead><tbody>{rows}</tbody></table>'
    ) if rows else ""

    ghost_line = ""
    if ghosts:
        ghost_line = (
            f'<div style="margin-top:8px;font-size:11px;color:#f38ba8">'
            f'⚠ {len(ghosts)} ghost reference(s) — referenced but missing: '
            f'<span style="font-family:monospace">{h(", ".join(ghosts[:4]))}</span></div>'
        )

    return f"""<div style="background:var(--surface0);border-radius:8px;padding:14px 16px;margin-bottom:20px">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
    <span style="font-size:14px">📦</span>
    <span style="font-size:13px;font-weight:700;color:var(--text)">Startup Payload</span>
    {status_badge}
    <span style="font-size:10px;color:var(--overlay0);margin-left:auto">audited {rel_time(gen)}</span>
  </div>
  <div style="font-size:11px;color:var(--subtext0);margin-bottom:10px;line-height:1.5">
    Fixed per-session token tax — CLAUDE.md + @imports + .claude/rules + auto-loaded MEMORY.md.
    RTK/lean-ctx/Headroom don't cover this; reduce it by structuring what auto-loads
    (<code style="font-size:10px">read on demand, not upfront</code>).
  </div>
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px">
    <div class="stat-card"><div class="stat-val" style="color:{status_color}">{total:,}</div>
      <div class="stat-lbl">tokens at startup</div></div>
    <div class="stat-card"><div class="stat-val" style="color:{'#f9e2af' if over_ceiling else 'var(--subtext1)'}">{ceiling_val}</div>
      <div class="stat-lbl">{h(ceiling_lbl)}</div></div>
    <div class="stat-card"><div class="stat-val" style="color:var(--subtext1)">{fcount}</div>
      <div class="stat-lbl">files loaded</div></div>
    <div class="stat-card"><div class="stat-val" style="color:{'#f9e2af' if stale else 'var(--subtext1)'}">{stale}</div>
      <div class="stat-lbl">stale files</div></div>
    <div class="stat-card"><div class="stat-val" style="color:{'#f38ba8' if ghosts else 'var(--subtext1)'}">{len(ghosts)}</div>
      <div class="stat-lbl">ghost refs</div></div>
  </div>
  {files_table}
  {ghost_line}
</div>"""


def _live_context_state(repo_path: str):
    """Read the last context-usage % the caveman-statusline hook observed for this repo.

    Written by hooks/global/caveman-statusline.sh on every statusline render
    while a Claude Code session is open for this repo — keyed by
    sha256(repo_path)[:16] since CLAUDE_CONFIG_DIR is machine-global, not
    per-repo. Returns None if no session has ever reported for this repo, or
    if the last report is older than LIVE_CONTEXT_MAX_AGE (session likely
    closed — showing a stale % as "live" would be misleading).
    """
    key = hashlib.sha256(repo_path.encode()).hexdigest()[:16]
    state_file = CLAUDE_CONFIG_DIR / "dashboard-context" / f"{key}.json"
    if not state_file.is_file() or state_file.is_symlink():
        return None
    try:
        data = json.loads(state_file.read_text())
        pct = int(data["pct"])
        updated_at = int(data["updated_at"])
    except Exception:
        return None
    age = NOW.timestamp() - updated_at
    if age < 0 or age > LIVE_CONTEXT_MAX_AGE:
        return None
    return {"pct": pct, "age_seconds": int(age)}


def _live_context_card(repo_path: str) -> str:
    live = _live_context_state(repo_path)
    if live is None:
        return ""
    pct = live["pct"]
    color = "#f38ba8" if pct >= 90 else "#f9e2af" if pct >= 70 else "#a6e3a1"
    age = live["age_seconds"]
    age_label = f"{age}s ago" if age < 90 else f"{age // 60}m ago"
    return f"""<div style="background:var(--surface0);border-radius:6px;padding:10px 12px;
      margin-bottom:16px;display:flex;align-items:center;gap:12px">
    <div style="font-size:22px;font-weight:700;color:{color}">{pct}%</div>
    <div>
      <div style="font-size:11px;color:var(--subtext1);font-weight:600">Live context usage</div>
      <div style="font-size:10px;color:var(--overlay1)">from the open Claude Code session's statusline &middot; updated {age_label}</div>
    </div>
  </div>"""


def render_context_health(rd):
    startup_html = _startup_payload_card(rd["path"])
    live_html = _live_context_card(rd["path"])
    sessions = rd.get("session_history", [])
    if not sessions:
        return f"""<div class="section-inner">
  <h2 class="section-title">Context Health</h2>
  {startup_html}
  {live_html}
  {empty_state("No session history yet. Sessions are logged at stop time once the stop hook has run at least once.")}
</div>"""

    cutoff_7d  = NOW - timedelta(days=7)
    cutoff_30d = NOW - timedelta(days=30)
    recent_7d  = [s for s in sessions if s >= cutoff_7d]
    recent_30d = [s for s in sessions if s >= cutoff_30d]

    last_session = sessions[-1]
    sessions_per_day_7d = len(recent_7d) / 7
    total_shown = len(recent_30d)

    freq_color = (
        "#a6e3a1" if sessions_per_day_7d <= 3 else
        "#f9e2af" if sessions_per_day_7d <= 6 else
        "#f38ba8"
    )
    freq_label = (
        "healthy" if sessions_per_day_7d <= 3 else
        "moderate" if sessions_per_day_7d <= 6 else
        "high — consider /compact"
    )

    summary = f"""<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px">
    <div class="stat-card">
      <div class="stat-val" style="color:{freq_color}">{len(recent_7d)}</div>
      <div class="stat-lbl">sessions (last 7d)</div></div>
    <div class="stat-card">
      <div class="stat-val" style="color:{freq_color}">{sessions_per_day_7d:.1f}</div>
      <div class="stat-lbl">sessions / day</div></div>
    <div class="stat-card">
      <div class="stat-val" style="color:var(--subtext1)">{rel_time(last_session.isoformat())}</div>
      <div class="stat-lbl">last session</div></div>
  </div>"""

    freq_badge = badge(freq_label, "ok" if sessions_per_day_7d <= 3 else "warn" if sessions_per_day_7d <= 6 else "missed")
    status_line = (
        f'<div style="margin-bottom:16px;font-size:12px;color:var(--subtext0)">'
        f'Frequency: {freq_badge}&nbsp; '
        f'<span style="color:var(--overlay1)">({total_shown} sessions tracked in last 30d)</span>'
        f'</div>'
    )

    bars = ""
    if recent_7d:
        day_counts: dict[str, int] = {}
        for s in recent_7d:
            day_str = s.strftime("%Y-%m-%d")
            day_counts[day_str] = day_counts.get(day_str, 0) + 1
        max_count = max(day_counts.values(), default=1)
        for s in sorted(day_counts):
            cnt = day_counts[s]
            bh = max(4, int(cnt / max_count * 44))
            bc = "#a6e3a1" if cnt <= 3 else "#f9e2af" if cnt <= 6 else "#f38ba8"
            bars += (
                f'<div title="{h(s)}: {cnt} session(s)" style="flex:1;display:flex;flex-direction:column;'
                f'align-items:center;justify-content:flex-end;gap:2px;min-width:14px">'
                f'<div style="font-size:8px;color:{bc};font-weight:600">{cnt}</div>'
                f'<div style="background:{bc};height:{bh}px;width:100%;border-radius:2px 2px 0 0;opacity:0.85"></div>'
                f'</div>'
            )
    chart = (
        f'<div class="label" style="margin-bottom:6px">Sessions per day (last 7d)</div>'
        f'<div style="display:flex;align-items:flex-end;gap:3px;height:64px;margin-bottom:16px">{bars}</div>'
    ) if bars else ""

    tips = """<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:16px">
    <div style="background:var(--surface0);border-radius:6px;padding:10px 12px;font-size:11px">
      <div style="color:var(--blue);font-weight:600;margin-bottom:4px">🗜 Compact heavy contexts</div>
      <div style="color:var(--subtext0);line-height:1.55">
        Run <code style="font-size:10px">/compact</code> when a session grows deep to
        summarize context and prevent quality degradation. Use <code style="font-size:10px">/kiro:context-budget</code>
        for a structured context health check.
      </div>
    </div>
    <div style="background:var(--surface0);border-radius:6px;padding:10px 12px;font-size:11px">
      <div style="color:var(--teal);font-weight:600;margin-bottom:4px">🪵 Subagents protect main context</div>
      <div style="color:var(--subtext0);line-height:1.55">
        Delegate research-heavy work to subagents so findings return as a summary,
        not as hundreds of tool-call lines. Use
        <code style="font-size:10px">/superpowers:dispatching-parallel-agents</code>.
      </div>
    </div>
  </div>"""

    return f"""<div class="section-inner">
  <h2 class="section-title">Context Health</h2>
  {section_desc("Tracks session frequency as a proxy for context load. High session counts often indicate heavy contexts that benefit from <code>/compact</code> or subagent delegation.", icon="🧵", color="var(--teal)")}
  {startup_html}
  {live_html}
  {summary}{status_line}{chart}{tips}
</div>"""

def count_debt_markers(repo) -> int:
    """Count deliberate-deferral markers in tracked code.

    Surfaces shortcuts consciously taken under simplicity pressure (the
    `DEBT:` convention from the karpathy-guidelines skill) so they stay
    visible without a command to run. Comment-anchored so prose mentions
    don't false-match; Markdown is excluded since markers belong in code,
    not docs. git grep respects .gitignore and scans only tracked files.
    Recomputed on every dashboard launch.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "grep", "-InE", r"(#|//|--|/\*)\s*DEBT:",
             "--", ".", ":(exclude)*.md"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return 0
    # git grep exit codes: 0 = matches found, 1 = none, >1 = real error
    if out.returncode not in (0, 1):
        return 0
    return sum(1 for line in out.stdout.splitlines() if line.strip())


def render_maintenance_status(selected_rd, all_repos_data, hd):
    runs = hd.get("orchestrator_runs", [])
    # Show the latest *daily-maintenance* run per repo (other runners are surfaced
    # in the Scheduled Tasks tab). New log format tags non-daily runs explicitly.
    latest = {}
    for run in runs:
        if run.get("runner") not in (None, "daily-maintenance"):
            continue
        p = run["path"]
        if p not in latest or run["ts"] > latest[p]["ts"]:
            latest[p] = run

    ccr_note = section_desc(
        "<strong style='color:var(--text)'>Local daily maintenance</strong> — runs via local system scheduler "
        "(judge · reflect · keep-rate · housekeep · augment). "
        "Runs locally so it can reach <code>.claude/memory/</code>. "
        "See <em>Scheduled Tasks</em> for the full routine list and "
        "<em>Automation Audit</em> for per-event history."
    )

    SCHED_HOUR  = 18   # daily-orchestrator.sh fires at 18:00 via local system scheduler
    GRACE_SECS  = 2 * 3600  # 2h grace after scheduled time before OVERDUE

    def _repo_card(rd, compact=False):
        path     = rd["path"]
        name     = Path(path).name
        last_run = rd.get("last_routine_run")
        run_info = latest.get(path)

        if last_run:
            now_local   = datetime.now()
            today_sched = now_local.replace(
                hour=SCHED_HOUR, minute=0, second=0, microsecond=0)
            last_local  = (last_run.astimezone().replace(tzinfo=None)
                           if last_run.tzinfo else last_run)

            if last_local.date() == now_local.date():
                run_status = "ok"
            elif now_local < today_sched + timedelta(seconds=GRACE_SECS):
                run_status = "pending"   # today's 18:00 slot hasn't fired yet
            else:
                run_status = "overdue"

            run_str = rel_time(last_run.isoformat())
            run_col = {"ok": "var(--green)",
                       "pending": "var(--yellow)",
                       "overdue": "var(--red)"}[run_status]
            sbadge  = {"ok":      badge("OK",      "ok"),
                       "pending": badge("PENDING",  "warn"),
                       "overdue": badge("OVERDUE",  "missed")}[run_status]

            # "next run" hint for the detail card
            if run_status == "ok":
                next_hint = "Next: tomorrow at 18:00"
            elif run_status == "pending":
                next_hint = f"Scheduled today at {SCHED_HOUR:02d}:00"
            else:
                next_hint = f"Was due at {SCHED_HOUR:02d}:00 today — check local system scheduler"
        else:
            run_str   = "never run"
            run_col   = "var(--overlay0)"
            sbadge    = badge("UNKNOWN", "default")
            next_hint = f"Next: today at {SCHED_HOUR:02d}:00 (if installed)"

        ec_html = dur_html = "—"
        if run_info:
            ec      = run_info["exit"]
            ec_col  = "var(--green)" if ec == 0 else "var(--red)"
            ec_html = f'<span style="color:{ec_col}">exit {ec}</span>'
            d       = run_info["duration"]
            dur_html = f"{d}s" if d > 0 else "—"

        if compact:
            return (
                f'<div style="background:var(--surface0);border-radius:6px;padding:10px 12px">'
                f'<div style="font-size:11px;font-weight:600;color:var(--text);'
                f'margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
                f'{h(name)}</div>'
                f'<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">'
                f'{sbadge}'
                f'<span style="color:{run_col};font-size:10px">{h(run_str)}</span>'
                f'</div></div>'
            )

        return f"""<div style="border:1px solid var(--surface0);border-radius:8px;
                       padding:16px 18px;margin-bottom:12px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <span style="font-size:14px;font-weight:700;color:var(--text)">{h(name)}</span>
        {sbadge}
      </div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">
        <div>
          <div class="label">Last run</div>
          <div style="color:{run_col};font-size:13px;margin-top:3px">{h(run_str)}</div>
        </div>
        <div>
          <div class="label">Next run</div>
          <div style="font-size:12px;color:var(--subtext0);margin-top:3px">{h(next_hint)}</div>
        </div>
        <div>
          <div class="label">Duration</div>
          <div style="font-size:13px;color:var(--subtext1);margin-top:3px">{dur_html}</div>
        </div>
        <div>
          <div class="label">Exit code</div>
          <div style="font-size:13px;margin-top:3px">{ec_html}</div>
        </div>
      </div>
    </div>"""

    sel_path = selected_rd["path"]
    others   = [rd for rd in all_repos_data if rd["path"] != sel_path]

    other_html = ""
    if others:
        grid = "".join(_repo_card(rd, compact=True) for rd in others)
        other_html = (
            f'<div style="margin-top:8px">'
            f'<div class="label" style="margin-bottom:8px">Other repos</div>'
            f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));'
            f'gap:8px">{grid}</div></div>'
        )

    log_tail = ""
    if ORCH_LOG.exists():
        lines    = ORCH_LOG.read_text().splitlines()[-12:]
        log_tail = (
            f'<details style="margin-top:16px">'
            f'<summary style="cursor:pointer;font-size:9px;text-transform:uppercase;'
            f'letter-spacing:1px;color:var(--overlay0);list-style:none;'
            f'padding:4px 0">Orchestrator log (last 12 lines)</summary>'
            f'<pre style="background:var(--crust);border-radius:6px;padding:12px;margin-top:6px;'
            f'font-size:10px;color:var(--subtext0);overflow-x:auto;white-space:pre-wrap">'
            f'{h(chr(10).join(lines))}</pre></details>'
        )

    total_debt = sum(rd.get("debt_markers", 0) for rd in all_repos_data)
    if total_debt:
        debt_note = (
            '<div style="margin:6px 0 14px;padding:10px 12px;border-radius:6px;'
            'background:var(--surface0);border-left:3px solid var(--yellow)">'
            '<span style="color:var(--yellow);font-weight:600;font-size:12px">'
            f'⚠ {total_debt} deferred marker{"" if total_debt == 1 else "s"}</span>'
            '<span style="color:var(--subtext0);font-size:11px;margin-left:8px">'
            'deliberate shortcuts tagged <code>DEBT:</code> in tracked code &mdash; '
            'run <code>git grep -nE "(#|//)\\s*DEBT:"</code> to list</span></div>'
        )
    else:
        debt_note = (
            '<div style="margin:6px 0 14px;font-size:11px;color:var(--overlay0)">'
            'No deferred <code>DEBT:</code> markers in tracked code.</div>'
        )

    return f"""<div class="section-inner">
  <h2 class="section-title">Maintenance Status</h2>
  {ccr_note}
  {debt_note}
  {_repo_card(selected_rd)}
  {other_html}
  {log_tail}
</div>"""

def render_automation_audit(rd, hd):
    """Timeline of every automated event: maintenance, trust judge, session signals, scheduled tasks."""
    from collections import defaultdict

    events = []
    repo_path = rd["path"]

    RUNNER_META = {
        "daily-maintenance": ("🔧", "Daily Maintenance",    "#a6e3a1"),
        "macro-eval":        ("📊", "Macro-Eval Sweep",     "#89b4fa"),
        "skill-curator":     ("🎯", "Skill Curator",        "#fab387"),
        "harness-health":    ("🏥", "Harness Health",       "#cba6f7"),
        "tool-failure-review":("🔍","Tool-Failure Review",  "#f9e2af"),
        "security-report":   ("🔒", "Security Report",      "#f38ba8"),
        "drift-review":      ("📐", "Drift Review",         "#94e2d5"),
    }

    # 1. Orchestrator runs for this repo — skip duration=0 (not-due checks, pure noise)
    brief_dir = Path(repo_path) / ".claude" / "memory" / "daily"
    for run in hd.get("orchestrator_runs", []):
        if run["path"] != repo_path or run["duration"] == 0:
            continue
        ok     = run["exit"] == 0
        runner = run.get("runner", "daily-maintenance")
        icon, label, color = RUNNER_META.get(runner, ("⚙️", runner, "#a6adc8"))
        if not ok:
            color = "#f38ba8"

        # For daily-maintenance, try to surface the brief for that date
        detail = None
        if runner == "daily-maintenance":
            run_date = run["ts"][:10]
            brief_f  = brief_dir / f"{run_date}-brief.md"
            if brief_f.exists():
                try:
                    detail = brief_f.read_text(encoding="utf-8", errors="replace")[:2000]
                except Exception:
                    pass
            if detail is None:
                detail = f"Ran for {run['duration']}s · exit={run['exit']}"

        events.append({
            "ts":      run["ts"],
            "icon":    icon,
            "label":   label,
            "color":   color,
            "summary": f"ran {run['duration']}s" + ("" if ok else f" · exit={run['exit']}"),
            "detail":  detail,
            "scope":   "local",
        })

    # 2. Trust judge entries (structured, from trust-score.jsonl)
    for score in rd.get("trust_scores", []):
        ts    = score.get("ts", "")
        delta = score.get("delta_applied", 0)
        cur   = score.get("score", 0)
        if delta > 0:
            delta_str, color = f"▲ +{delta:.1f}", "#a6e3a1"
        elif delta < 0:
            delta_str, color = f"▼ {delta:.1f}", "#f38ba8"
        else:
            delta_str, color = "▬ ±0", "#6c7086"
        events.append({
            "ts":      ts,
            "icon":    "⚡",
            "label":   "Trust Judge",
            "color":   color,
            "summary": f"{delta_str} → {cur:.0f}%",
            "detail":  score.get("summary") or None,
            "scope":   "local",
        })

    # 3. Automated observation tags (session signals, memory, skill queue)
    obs = rd.get("observations", {})
    OBS_TAGS = [
        ("session-quality",  "📊", "Session Quality", "#89b4fa"),
        ("keep-rate",        "📌", "Keep-Rate",        "#94e2d5"),
        ("memory-gap",       "🧠", "Memory Gap",       "#f9e2af"),
        ("skill-audit-queue","🎯", "Skill Queue",      "#fab387"),
    ]
    for tag, icon, label, color in OBS_TAGS:
        for date_str, text in obs.get(tag, []):
            events.append({
                "ts":      date_str + "T12:00:00+00:00",
                "icon":    icon,
                "label":   label,
                "color":   color,
                "summary": text[:110],
                "detail":  text if len(text) > 110 else None,
                "scope":   "local",
            })

    # 4. Scheduled-task runs (harness-wide, shown for context)
    for r in hd.get("scheduled_tasks", []):
        ts_iso = r.get("last_run_ts_iso")
        if ts_iso:
            ex = r.get("last_exit")
            exit_str = f"exit={ex}" if ex is not None else "—"
            events.append({
                "ts":      ts_iso,
                "icon":    "📅",
                "label":   r["name"],
                "color":   "#cba6f7",
                "summary": f"Scheduled · {r['schedule_human']} · {exit_str}",
                "detail":  f"Artifact: {r['artifact_label']}\nChanges: +{r['diff_added']}/−{r['diff_removed']} lines",
                "scope":   "harness",
            })

    # 5. PR review pipeline — log_review.sh writes + learning-sweep reports
    pr_review_dir = Path(repo_path) / ".claude" / "memory" / "pr-reviews"
    if pr_review_dir.is_dir():
        for md_f in pr_review_dir.glob("pr-*.md"):
            try:
                mtime = datetime.fromtimestamp(md_f.stat().st_mtime, tz=timezone.utc)
            except Exception:
                continue
            pr_num = md_f.stem.replace("pr-", "")
            events.append({
                "ts":      mtime.isoformat(),
                "icon":    "📝",
                "label":   "PR Review Logged",
                "color":   "#89dceb",
                "summary": f"PR #{pr_num} review written",
                "detail":  None,
                "scope":   "local",
            })

    learning_report = Path(repo_path) / "docs" / "code-review-learning-report.md"
    if learning_report.is_file():
        try:
            text = learning_report.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        for date_str, body in _split_sweep_sections(text):
            pending_section = (
                body.split("## Pending Approval", 1)[-1]
                if "## Pending Approval" in body else ""
            )
            pending_count = 0 if "None this sweep" in pending_section else sum(
                1 for ln in pending_section.splitlines() if _is_numbered_bullet(ln)
            )
            events.append({
                "ts":      date_str + "T12:00:00+00:00",
                "icon":    "🎓",
                "label":   "Review Learning Sweep",
                "color":   "#89dceb",
                "summary": (f"sweep ran · {pending_count} pending approval"
                            if pending_count else "sweep ran · nothing pending"),
                "detail":  body[:2000].strip() or None,
                "scope":   "local",
            })

    if not events:
        return f"""<div class="section-inner">
  <h2 class="section-title">Automation Audit</h2>
  {empty_state("No automation history yet. Run daily-maintenance to start tracking.")}
</div>"""

    events.sort(key=lambda e: e["ts"], reverse=True)

    # Group by calendar date
    by_date = defaultdict(list)
    for ev in events[:100]:
        by_date[ev["ts"][:10]].append(ev)

    feed = ""
    for date_key in sorted(by_date.keys(), reverse=True):
        day_evs = by_date[date_key]
        try:
            dt   = datetime.fromisoformat(date_key + "T12:00:00+00:00")
            secs = (NOW - dt).total_seconds()
            if secs < 86400:
                rel = "today"
            elif secs < 172800:
                rel = "yesterday"
            else:
                rel = f"{int(secs/86400)}d ago"
            date_lbl = dt.strftime("%a %d %b %Y")
        except Exception:
            date_lbl, rel = date_key, ""

        n = len(day_evs)
        feed += (
            f'<div style="margin-bottom:18px">'
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">'
            f'<span style="font-size:11px;font-weight:700;color:var(--subtext1)">{h(date_lbl)}</span>'
            f'<span style="font-size:10px;color:var(--overlay0)">{h(rel)}</span>'
            f'<div style="flex:1;height:1px;background:var(--surface0)"></div>'
            f'<span style="font-size:10px;color:var(--overlay0)">'
            f'{n} event{"s" if n != 1 else ""}</span>'
            f'</div>'
        )

        for ev in day_evs:
            scope_pill = (
                '<span style="font-size:9px;padding:1px 5px;border-radius:8px;'
                'background:rgba(137,180,250,0.12);color:#89b4fa">local</span>'
                if ev["scope"] == "local" else
                '<span style="font-size:9px;padding:1px 5px;border-radius:8px;'
                'background:rgba(203,166,247,0.12);color:#cba6f7">harness</span>'
            )
            row_inner = (
                f'<span style="font-size:14px;width:22px;text-align:center;flex-shrink:0">'
                f'{h(ev["icon"])}</span>'
                f'<span style="font-size:12px;font-weight:600;min-width:134px;flex-shrink:0;'
                f'color:{h(ev["color"])}">{h(ev["label"])}</span>'
                f'{scope_pill}'
                f'<span style="flex:1;font-size:11px;color:var(--subtext0);'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'
                f'padding:0 8px">{h(ev["summary"])}</span>'
                f'<span style="font-size:10px;color:var(--overlay0);flex-shrink:0">'
                f'{h(rel_time(ev["ts"]))}</span>'
            )
            wrap_s = ("margin-bottom:3px;border:1px solid var(--surface0);"
                      "border-radius:6px;overflow:hidden")
            row_s  = ("display:flex;align-items:center;gap:8px;padding:7px 12px;"
                      "background:var(--base)")
            if ev.get("detail"):
                detail_block = (
                    f'<div style="padding:8px 12px 8px 38px;font-size:11px;'
                    f'color:var(--subtext0);background:rgba(0,0,0,0.2);'
                    f'border-top:1px solid var(--surface0);line-height:1.65;'
                    f'white-space:pre-wrap;word-break:break-word">'
                    f'{h(ev["detail"])}</div>'
                )
                feed += (
                    f'<details style="{wrap_s}">'
                    f'<summary style="{row_s};cursor:pointer">{row_inner}</summary>'
                    f'{detail_block}</details>'
                )
            else:
                feed += (
                    f'<div style="{wrap_s}">'
                    f'<div style="{row_s}">{row_inner}</div></div>'
                )

        feed += "</div>"

    return f"""<div class="section-inner">
  <h2 class="section-title">Automation Audit</h2>
  <div style="color:var(--overlay0);font-size:11px;margin-bottom:16px">
    Every automated task and scheduled routine — what ran and what changed.
    Click any row with a ▶ to expand detail.
  </div>
  {feed}
</div>"""

# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --base:#1e1e2e;--mantle:#181825;--crust:#11111b;
  --surface0:#313244;--surface1:#45475a;
  --overlay0:#6c7086;--overlay1:#7f849c;
  --subtext0:#a6adc8;--subtext1:#bac2de;--text:#cdd6f4;
  --mauve:#cba6f7;--red:#f38ba8;--peach:#fab387;
  --yellow:#f9e2af;--green:#a6e3a1;--teal:#94e2d5;--blue:#89b4fa;
}
html,body{height:100%;background:var(--mantle);color:var(--text);
  font-family:system-ui,-apple-system,sans-serif;font-size:14px;line-height:1.5}
.app{display:flex;height:100vh;overflow:hidden}
.sidebar{width:210px;min-width:210px;background:var(--base);
  border-right:1px solid var(--surface0);display:flex;
  flex-direction:column;overflow-y:auto}
.sidebar-header{padding:18px 16px 14px;font-size:15px;font-weight:700;
  color:var(--text);letter-spacing:-.3px;border-bottom:1px solid var(--surface0)}
.sidebar-header span{color:var(--mauve)}
.repo-wrap{padding:10px 12px;border-bottom:1px solid var(--surface0)}
.repo-wrap select{width:100%;background:var(--surface0);color:var(--text);
  border:1px solid var(--surface1);border-radius:6px;padding:5px 8px;
  font-size:11px;cursor:pointer;outline:none}
.repo-wrap select:focus{border-color:var(--mauve)}
.nav-group-label{padding:10px 14px 4px;font-size:9px;text-transform:uppercase;
  letter-spacing:1.2px;color:var(--overlay0)}
.nav-item{display:flex;align-items:center;gap:8px;padding:7px 14px;
  font-size:12px;color:var(--subtext0);cursor:pointer;
  border-left:2px solid transparent;text-decoration:none;
  transition:all .12s;user-select:none}
.nav-item:hover{background:rgba(255,255,255,.04);color:var(--text)}
.nav-item.active{color:var(--mauve);border-left-color:var(--mauve);
  background:rgba(203,166,247,.08)}
.nav-icon{font-size:13px}
.sidebar-footer{margin-top:auto;padding:12px 14px;
  border-top:1px solid var(--surface0)}
.content{flex:1;overflow-y:auto;background:var(--mantle)}
.section-inner{padding:24px 28px;max-width:920px}
.section-title{font-size:18px;font-weight:700;color:var(--text);margin-bottom:20px}
.label{font-size:9px;text-transform:uppercase;letter-spacing:1px;color:var(--overlay0)}
.stat-card{background:var(--surface0);border-radius:8px;padding:12px 14px;text-align:center}
.stat-val{font-size:22px;font-weight:800;font-family:ui-monospace,Consolas,monospace}
.stat-lbl{font-size:10px;color:var(--overlay0);text-transform:uppercase;
  letter-spacing:.8px;margin-top:2px}
.th{text-align:left;padding:8px 10px;font-size:9px;text-transform:uppercase;
  letter-spacing:.8px;color:var(--overlay0);font-weight:600}
td{padding:8px 10px;border-bottom:1px solid rgba(49,50,68,.5)}
.sub-tab-bar{display:flex;gap:0;border-bottom:1px solid var(--surface0);margin-bottom:20px}
.sub-tab{background:none;border:none;border-bottom:2px solid transparent;padding:8px 16px;
  font-size:12px;color:var(--overlay0);cursor:pointer;transition:all .12s;white-space:nowrap}
.sub-tab:hover{color:var(--subtext1);background:rgba(255,255,255,.03)}
.sub-tab.active{color:var(--mauve);border-bottom-color:var(--mauve)}
"""

# ── JS ────────────────────────────────────────────────────────────────────────

JS_TEMPLATE = r"""
const SD = __SECTIONS_JSON__;
let repo = __INIT_REPO__;
let sec  = 'session_health';

const GN_WEB_UI    = '__GN_WEB_UI__';
const GN_PROBE_URL = '__GN_PROBE_URL__';

function show(sectionKey) {
  // Release gitnexus WebGL context before destroying the iframe element
  var oldFrame = document.getElementById('gn-frame');
  if (oldFrame && oldFrame.src) { oldFrame.src = ''; }
  _gnProbing = false;
  sec = sectionKey;
  var d = SD[repo];
  document.getElementById('panel').innerHTML =
    d ? (d[sectionKey] || '<div class="section-inner"><p style="color:var(--overlay0)">Not available.</p></div>')
      : '<div class="section-inner"><p style="color:var(--overlay0)">No data.</p></div>';
  document.querySelectorAll('.nav-item').forEach(function(el) {
    el.classList.toggle('active', el.dataset.s === sectionKey);
  });
  if (sectionKey === 'gitnexus') probeGitnexus();
  if (sectionKey === 'workshop') loadWorkshopRuns();
  if (sectionKey === 'budget_efficiency') loadHeadroom();
  // Guarded: the herder functions live in a companion-only block, so in a
  // --static page this identifier does not exist at all.
  if (sectionKey === 'herder' && typeof herderRefresh === 'function') {
    _hdRosterSig = '';          // panel HTML was just replaced; force a rebuild
    herderLoadOptions();
    herderRefresh();
  }
}

function switchRepo(v) { repo = v; show(sec); }

var _gnProbing = false;
function probeGitnexus() {
  if (_gnProbing) return;
  _gnProbing = true;
  var ctrl  = new AbortController();
  var timer = setTimeout(function() { ctrl.abort(); }, 2500);
  // Probe /api/repos, not '/': gitnexus serve is API-only and 404s at the root.
  // Real CORS mode (not no-cors) so an error status is visible instead of opaque.
  fetch(GN_PROBE_URL, { signal: ctrl.signal })
    .then(function(res) {
      clearTimeout(timer);
      _gnProbing = false;
      if (!res.ok) { gnOffline('GitNexus API returned HTTP ' + res.status); return; }
      var f  = document.getElementById('gn-frame');
      var fb = document.getElementById('gn-fallback');
      if (f)  { f.src = f.dataset.src || GN_WEB_UI; f.style.display = 'block'; }
      if (fb) fb.style.display = 'none';
    })
    .catch(function() {
      clearTimeout(timer);
      _gnProbing = false;
      gnOffline('GitNexus not running');
    });
}

function gnOffline(message) {
  var st    = document.getElementById('gn-status');
  var btn   = document.getElementById('gn-copy-btn');
  var hint  = document.getElementById('gn-copy-hint');
  var sbtn  = document.getElementById('gn-start-btn');
  var rbtn  = document.getElementById('gn-retry-btn');
  if (st)   st.textContent = message;
  if (btn)  btn.style.display  = 'block';
  if (hint) hint.style.display = 'block';
  if (sbtn) sbtn.style.display = 'block';
  if (rbtn) rbtn.style.display = 'block';
}

__GN_SERVE_FUNS__

__WORKSHOP_FUNS__

__HEADROOM_FUNS__

__MC_FUNS__

// Generic tab switcher — used by Memory Changes and any future tabbed section.
// Tab pane IDs follow the pattern: <prefix>-pane-<name>
// Tab button IDs follow: <prefix>-tab-<name>
function switchTab(prefix, pane, panes) {
  panes = panes || ['obs', 'files'];
  panes.forEach(function(p) {
    var el  = document.getElementById(prefix + '-pane-' + p);
    var btn = document.getElementById(prefix + '-tab-' + p);
    if (el)  el.style.display = (p === pane) ? 'block' : 'none';
    if (btn) {
      btn.style.borderBottomColor = (p === pane) ? 'var(--mauve)' : 'transparent';
      btn.style.color             = (p === pane) ? 'var(--mauve)' : 'var(--overlay0)';
    }
  });
}

document.addEventListener('DOMContentLoaded', function() { show('session_health'); });
"""

# ── HTML Assembly ─────────────────────────────────────────────────────────────

def _platform_data_dir(app: str) -> Path:
    """OS-native per-user *data* dir for `app`, mirroring Rust's `dirs::data_dir`.

    RTK is a Rust binary and writes its SQLite history here, so the path differs
    by platform:
        macOS   → ~/Library/Application Support/<app>
        Windows → %APPDATA%\\<app>          (roaming)
        Linux   → $XDG_DATA_HOME/<app> or ~/.local/share/<app>
    """
    home = Path.home()
    plat = str(sys.platform)  # via local so static analysers don't narrow to one OS
    if plat == "darwin":
        return home / "Library" / "Application Support" / app
    if plat.startswith("win"):
        base = os.environ.get("APPDATA")
        return (Path(base) if base else home / "AppData" / "Roaming") / app
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else home / ".local" / "share") / app


def _platform_config_dir(app: str) -> Path:
    """OS-native per-user *config* dir for `app`, honouring XDG_CONFIG_HOME.

    macOS/Linux both resolve to ~/.config/<app> by default (XDG-style tools);
    Windows uses %APPDATA%. lean-ctx / caveman follow this convention.
    """
    home = Path.home()
    plat = str(sys.platform)  # via local so static analysers don't narrow to one OS
    if plat.startswith("win"):
        base = os.environ.get("APPDATA")
        return (Path(base) if base else home / "AppData" / "Roaming") / app
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else home / ".config") / app


RTK_DB           = _platform_data_dir("rtk") / "history.db"
# lean-ctx writes its real data ledger to XDG_DATA_HOME/lean-ctx (~/.local/share on
# macOS too — it does NOT follow the macOS "Application Support" convention that
# _platform_data_dir assumes for other apps). ~/.config/lean-ctx only holds config.toml.
_LEAN_CTX_DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "lean-ctx"
LEAN_CTX_STATS   = _LEAN_CTX_DATA_DIR / "stats.json"
LEAN_CTX_LEDGER  = _LEAN_CTX_DATA_DIR / "savings" / "ledger.jsonl"
CAVEMAN_CONFIG   = _platform_config_dir("caveman") / "config.json"
# Sonnet 4.6 input price per million tokens (used to estimate RTK $ savings)
_SONNET_INPUT_PER_M = 3.0
# Sonnet 4.6 output price per million tokens (used to estimate caveman $ savings) — approximate.
_SONNET_OUTPUT_PER_M = 15.0


def _repo_to_lean_ctx_hash(repo_path: str) -> str:
    """Compute lean-ctx repo_hash from absolute path (SHA256 first 16 hex chars)."""
    import hashlib as _hl
    return _hl.sha256(repo_path.encode()).hexdigest()[:16]


def _read_lean_ctx_stats(repo_hash: "str | None" = None) -> dict:
    """Read lean-ctx savings from ledger.jsonl, optionally filtered by repo_hash."""
    empty = {"saved_tokens": 0, "actual_tokens": 0, "baseline_tokens": 0,
             "saved_usd": 0.0, "cep_sessions": 0, "commands": 0, "installed": False}
    import shutil as _sh
    _cfg = _platform_config_dir("lean-ctx") / "config.toml"
    # Installed if the tool is present at all — stats/ledger only appear
    # after the first compressed read, which lags a fresh install.
    installed = (LEAN_CTX_STATS.exists() or LEAN_CTX_LEDGER.exists()
                 or _cfg.exists() or _sh.which("lean-ctx") is not None)
    if not installed:
        return empty

    commands = 0
    cep_sessions = 0
    if LEAN_CTX_STATS.exists():
        try:
            d = json.loads(LEAN_CTX_STATS.read_text())
            commands = d.get("total_commands", 0)
            cep_sessions = d.get("cep", {}).get("sessions", 0)
        except Exception:
            pass

    saved_tokens = 0
    actual_tokens = 0
    baseline_tokens = 0
    saved_usd = 0.0
    if LEAN_CTX_LEDGER.exists():
        try:
            for line in LEAN_CTX_LEDGER.read_text().splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                if repo_hash and entry.get("repo_hash") != repo_hash:
                    continue
                saved_tokens    += entry.get("saved_tokens", 0)
                actual_tokens   += entry.get("actual_tokens", 0)
                baseline_tokens += entry.get("baseline_tokens", 0)
                saved_usd       += entry.get("saved_usd", 0.0)
        except Exception:
            pass

    return {
        "saved_tokens":   saved_tokens,
        "actual_tokens":  actual_tokens,
        "baseline_tokens": baseline_tokens,
        "saved_usd":      saved_usd,
        "cep_sessions":   cep_sessions,
        "commands":       commands,
        "installed":      True,
    }


def _read_caveman_config() -> dict:
    """Read caveman mode from whichever implementation is installed.

    Two variants ship in the wild:
      - JuliusBrussee plugin -> ~/.config/caveman/config.json  {"defaultMode": ...}
      - harness homegrown    -> ~/.claude/.caveman-active       (plain text, e.g. "lite")
    Check both so the dashboard reports "active" regardless of which is wired.
    """
    if CAVEMAN_CONFIG.exists():
        try:
            d    = json.loads(CAVEMAN_CONFIG.read_text())
            mode = d.get("defaultMode")
            if mode:
                return {"active": True, "mode": mode}
        except Exception:
            pass
    flag = Path.home() / ".claude" / ".caveman-active"
    if flag.exists():
        try:
            mode = flag.read_text().strip() or None
            if mode:
                return {"active": True, "mode": mode}
        except Exception:
            pass
    return {"active": False, "mode": None}


def _read_caveman_savings(repo_path: "str | None" = None) -> dict:
    """Read .claude/memory/caveman-savings.jsonl — written by
    caveman-savings-hook.sh, one sample per day when caveman mode is active.

    Values are word-count-based estimates, not exact BPE token counts (see
    the hook's header comment for why usage.output_tokens isn't trustworthy
    here — it's contaminated by extended-thinking tokens on the "actual"
    side). Treat this as directionally useful, not a billing-grade figure.
    """
    empty = {"saved_tokens": 0, "actual_tokens": 0, "baseline_tokens": 0,
             "samples": 0, "avg_pct": 0.0}
    base = Path(repo_path) if repo_path else HARNESS_DIR
    ledger = base / ".claude" / "memory" / "caveman-savings.jsonl"
    if not ledger.exists():
        return empty
    saved = actual = baseline = 0
    pcts = []
    try:
        for line in ledger.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            saved    += e.get("saved_tokens", 0)
            actual   += e.get("actual_tokens", 0)
            baseline += e.get("baseline_tokens", 0)
            if "saved_pct" in e:
                pcts.append(e["saved_pct"])
    except Exception:
        return empty
    avg_pct = sum(pcts) / len(pcts) if pcts else 0.0
    return {"saved_tokens": saved, "actual_tokens": actual, "baseline_tokens": baseline,
            "samples": len(pcts), "avg_pct": avg_pct}


def _read_rtk_stats() -> dict:
    """Read lifetime RTK savings from SQLite. Returns zeros if DB absent."""
    if not RTK_DB.exists():
        return {"baseline": 0, "saved": 0, "after": 0, "commands": 0, "effective": 0}
    try:
        import sqlite3 as _sq
        conn = _sq.connect(str(RTK_DB))
        row  = conn.execute(
            "SELECT SUM(input_tokens+saved_tokens), SUM(saved_tokens), "
            "SUM(input_tokens), COUNT(*), "
            "SUM(CASE WHEN saved_tokens>0 THEN 1 ELSE 0 END) FROM commands"
        ).fetchone()
        conn.close()
        return {
            "baseline": row[0] or 0,
            "saved":    row[1] or 0,
            "after":    row[2] or 0,
            "commands": row[3] or 0,
            "effective": row[4] or 0,
        }
    except Exception:
        return {"baseline": 0, "saved": 0, "after": 0, "commands": 0, "effective": 0}


def _read_rtk_net_effect(repo_path: "str | None" = None) -> dict:
    """Read .claude/memory/rtk-net-effect.json — written by
    rtk-net-effect-runner.sh. Global rerun/reread signal, not RTK's own
    local-savings figure: high rerun rate is evidence, not proof, that
    compression cost detail the agent had to go get back.
    """
    empty = {"available": False, "bash_rerun_rate_pct": 0.0, "read_reread_rate_pct": 0.0}
    base = Path(repo_path) if repo_path else HARNESS_DIR
    snapshot = base / ".claude" / "memory" / "rtk-net-effect.json"
    if not snapshot.exists():
        return empty
    try:
        data = json.loads(snapshot.read_text())
        data["available"] = True
        return data
    except Exception:
        return empty


def render_headroom(repo_path: "str | None" = None) -> str:
    """Compression pipeline tab — RTK + headroom + lean-ctx (per-repo if repo_path given)."""
    repo_hash = _repo_to_lean_ctx_hash(repo_path) if repo_path else None

    # ── RTK data ──────────────────────────────────────────────────────────────
    rtk = _read_rtk_stats()
    rtk_saved    = rtk["saved"]
    rtk_baseline = rtk["baseline"]
    rtk_pct      = (rtk_saved / rtk_baseline * 100) if rtk_baseline else 0.0
    rtk_cost_est = rtk_saved * _SONNET_INPUT_PER_M / 1_000_000

    # ── Headroom data ─────────────────────────────────────────────────────────
    hr_data, hr_lifetime, hr_sess, hr_history = {}, {}, {}, []
    if HEADROOM_SAVINGS.exists():
        try:
            hr_data    = json.loads(HEADROOM_SAVINGS.read_text())
            hr_lifetime = hr_data.get("lifetime", {})
            hr_sess     = hr_data.get("display_session", {})
            hr_history  = hr_data.get("history", [])
        except Exception:
            pass

    hr_saved    = hr_lifetime.get("tokens_saved", 0)
    hr_before   = hr_lifetime.get("total_input_tokens", 0) + hr_saved
    hr_after    = hr_lifetime.get("total_input_tokens", 0)
    hr_pct      = (hr_saved / hr_before * 100) if hr_before else 0.0
    hr_cost_saved = hr_lifetime.get("compression_savings_usd", 0.0)
    hr_total_cost = hr_lifetime.get("total_input_cost_usd", 0.0)
    hr_requests   = hr_lifetime.get("requests", 0)

    # ── Combined ──────────────────────────────────────────────────────────────
    total_saved    = rtk_saved + hr_saved
    total_cost_est = rtk_cost_est + hr_cost_saved

    # ── Helper ────────────────────────────────────────────────────────────────
    def _fmt_ts(ts: str) -> str:
        if not ts:
            return "–"
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.strftime("%b %d %H:%M UTC")
        except Exception:
            return ts

    def _bar(pct: float, color: str = "var(--green)") -> str:
        w = max(0, min(100, pct))
        return (
            f'<div style="background:var(--surface1);border-radius:4px;height:6px;margin-top:4px">'
            f'<div style="background:{color};width:{w:.1f}%;height:100%;border-radius:4px"></div></div>'
        )

    def _layer(icon, name, baseline_lbl, baseline_val, saved_tok, pct, cost_str,
               after_lbl, after_val, note="", dimmed=False):
        dim = "opacity:.45;" if dimmed else ""
        return f"""<div style="background:var(--surface0);border-radius:8px;padding:14px 16px;{dim}">
  <div style="display:flex;justify-content:space-between;align-items:flex-start">
    <div style="flex:1">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <span style="font-size:16px">{icon}</span>
        <span style="font-size:13px;font-weight:700;color:var(--text)">{name}</span>
        {f'<span style="font-size:10px;color:var(--overlay0);font-style:italic">{note}</span>' if note else ''}
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px">
        <div>
          <div class="label">{baseline_lbl}</div>
          <div style="font-size:13px;color:var(--subtext1);font-family:monospace">{baseline_val}</div>
        </div>
        <div>
          <div class="label">tokens saved</div>
          <div style="font-size:13px;color:var(--green);font-weight:700">{saved_tok:,}
            <span style="font-size:11px;color:var(--subtext0)">({pct:.1f}%)</span></div>
          {_bar(pct)}
        </div>
        <div>
          <div class="label">{after_lbl}</div>
          <div style="font-size:13px;color:var(--subtext1);font-family:monospace">{after_val}</div>
        </div>
      </div>
    </div>
    <div style="text-align:right;margin-left:16px;flex-shrink:0">
      <div class="label">cost saved</div>
      <div style="font-size:18px;font-weight:800;color:var(--green)">{cost_str}</div>
    </div>
  </div>
</div>"""

    # ── Per-layer data ────────────────────────────────────────────────────────
    lctx    = _read_lean_ctx_stats(repo_hash=repo_hash)
    cav_cfg = _read_caveman_config()

    lctx_saved   = lctx["saved_tokens"]
    lctx_orig    = lctx["baseline_tokens"]
    lctx_actual  = lctx["actual_tokens"]
    lctx_pct     = (lctx_saved / lctx_orig * 100) if lctx_orig else 0.0
    lctx_cost_est = lctx["saved_usd"]
    total_saved  += lctx_saved
    total_cost_est += lctx_cost_est

    # ── Pipeline layers ───────────────────────────────────────────────────────
    arrow = '<div style="text-align:center;font-size:18px;color:var(--overlay0);margin:2px 0">↓</div>'

    rtk_ne = _read_rtk_net_effect(repo_path)
    rtk_note = f"{rtk['commands']:,} commands · {rtk['effective']:,} effective"
    if rtk_ne["available"]:
        rtk_note += (
            f" · rerun {rtk_ne['bash_rerun_rate_pct']:.0f}% · reread {rtk_ne['read_reread_rate_pct']:.0f}%"
        )

    rtk_layer = _layer(
        "⚡", "RTK — Shell Output",
        "raw shell output", f"{rtk_baseline:,}",
        rtk_saved, rtk_pct,
        f"~${rtk_cost_est:.2f}",
        "into context", f"{rtk['after']:,}",
        note=rtk_note,
    )

    hr_layer = _layer(
        "🗜", "Headroom — API Context",
        "context sent", f"{hr_before:,}",
        hr_saved, hr_pct,
        f"${hr_cost_saved:.3f}",
        "to Claude API", f"{hr_after:,}",
        note=f"{hr_requests:,} requests",
    )

    scope_note = f" · this repo" if repo_hash else " · all repos"
    if lctx["installed"] and lctx_orig > 0:
        leancx_layer = _layer(
            "📄", "lean-ctx — File Reads",
            "raw file tokens", f"{lctx_orig:,}",
            lctx_saved, lctx_pct,
            f"~${lctx_cost_est:.4f}",
            "after compression", f"{lctx_actual:,}",
            note=f"{lctx['commands']} commands{scope_note}",
        )
    elif lctx["installed"]:
        leancx_layer = _layer(
            "📄", "lean-ctx — File Reads",
            "–", "–", 0, 0.0, "–", "–", "–",
            note=f"installed · {lctx['commands']} commands · no savings recorded{scope_note}",
            dimmed=False,
        )
    else:
        leancx_layer = _layer(
            "📄", "lean-ctx — File Reads",
            "–", "–", 0, 0.0, "–", "–", "–",
            note="not installed",
            dimmed=True,
        )

    cav_mode = cav_cfg.get("mode") or "off"
    cav_active = cav_cfg.get("active", False)
    cav_data = _read_caveman_savings(repo_path=repo_path)
    if cav_active and cav_data["samples"] > 0:
        cav_cost_est = cav_data["saved_tokens"] * _SONNET_OUTPUT_PER_M / 1_000_000
        caveman_layer = _layer(
            "🦴", "Caveman — Response Style",
            "baseline (est.)", f"{cav_data['baseline_tokens']:,}",
            cav_data["saved_tokens"], cav_data["avg_pct"],
            f"~${cav_cost_est:.3f}",
            "actual (est.)", f"{cav_data['actual_tokens']:,}",
            note=(f"mode: {cav_mode} · {cav_data['samples']} sampled day{'s' if cav_data['samples'] != 1 else ''} "
                  f"· word-count estimate, not exact tokens"),
        )
    else:
        cav_cost_est = 0.0
        caveman_layer = _layer(
            "🦴", "Caveman — Response Style",
            "–", "–", 0, 0.0, "–", "output tokens", "shorter responses",
            note=(f"mode: {cav_mode} · measuring — first sample lands after today's session ends" if cav_active
                  else "not active"),
            dimmed=not cav_active,
        )

    if cav_active and cav_data["samples"] > 0:
        total_saved    += cav_data["saved_tokens"]
        total_cost_est += cav_cost_est

    pipeline_html = f"""
<div style="margin-bottom:20px">
  <div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;
              color:var(--overlay0);margin-bottom:10px">Compression Pipeline (lifetime)</div>
  <div style="display:flex;flex-direction:column;gap:4px">
    {rtk_layer}
    {arrow}
    {hr_layer}
    {arrow}
    {leancx_layer}
    {arrow}
    {caveman_layer}
  </div>
</div>"""

    # ── Combined totals ───────────────────────────────────────────────────────
    total_html = f"""<div style="background:var(--base);border:1px solid var(--mauve);
                               border-radius:8px;padding:14px 18px;margin-bottom:20px">
  <div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;
              color:var(--mauve);margin-bottom:10px">Combined Savings (all layers)</div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">
    <div class="stat-card">
      <div class="stat-val" style="color:var(--green)">{total_saved:,}</div>
      <div class="stat-lbl">total tokens saved</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" style="color:var(--green)">${total_cost_est:.2f}</div>
      <div class="stat-lbl">total cost saved</div>
    </div>
    <div class="stat-card">
      <div class="stat-val">${hr_total_cost:.2f}</div>
      <div class="stat-lbl">actual API spend</div>
    </div>
  </div>
  <div style="font-size:10px;color:var(--overlay0);margin-top:8px">
    RTK cost estimate uses Sonnet 4.6 input rate ($3/M); caveman uses the output rate ($15/M, approx).
    Caveman figures are word-count estimates, not exact tokens, and only appear once sampled.
    Layers are additive (different pipeline stages, no double-counting).
  </div>
</div>"""

    # ── Current headroom session card ─────────────────────────────────────────
    started_at  = hr_sess.get("started_at", "")
    last_act    = hr_sess.get("last_activity_at", "")
    savings_pct = hr_sess.get("savings_percent", 0.0)
    sess_tokens = hr_sess.get("tokens_saved", 0)
    sess_cost   = hr_sess.get("compression_savings_usd", 0.0)

    session_card = f"""<div style="background:var(--surface0);border-radius:8px;padding:14px 16px;margin-bottom:16px">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
    <span style="font-size:13px;font-weight:600;color:var(--text)">Headroom — Current Session</span>
    <span id="headroom-live-badge"
          style="font-size:10px;background:var(--overlay0);color:var(--mantle);
                 padding:2px 8px;border-radius:10px;font-weight:600">LOADING…</span>
  </div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
    <div><div class="label">started</div>
         <div style="font-size:12px;color:var(--text)">{_fmt_ts(started_at)}</div></div>
    <div><div class="label">last activity</div>
         <div style="font-size:12px;color:var(--text)">{_fmt_ts(last_act)}</div></div>
    <div><div class="label">compression</div>
         <div id="hr-live-savings" style="font-size:12px;color:var(--green)">
           {savings_pct:.1f}% &nbsp;
           <span style="color:var(--subtext0);font-size:11px">{sess_tokens:,} tok · ${sess_cost:.3f}</span>
         </div></div>
  </div>
</div>"""

    # ── History accordion (headroom blocks) ───────────────────────────────────
    SPLIT_GAP_S = 1800
    blocks: list[list[dict]] = []
    for entry in sorted(hr_history, key=lambda e: e.get("timestamp", "")):
        if not blocks:
            blocks.append([entry])
            continue
        last_ts = blocks[-1][-1].get("timestamp", "")
        this_ts = entry.get("timestamp", "")
        try:
            gap_s = (
                datetime.fromisoformat(this_ts.replace("Z", "+00:00"))
                - datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            ).total_seconds()
        except Exception:
            gap_s = 0
        if gap_s > SPLIT_GAP_S:
            blocks.append([entry])
        else:
            blocks[-1].append(entry)

    accordion_items = []
    for bidx, block in enumerate(reversed(blocks)):
        first_e    = block[0]
        last_e     = block[-1]
        ts0        = first_e.get("timestamp", "")
        ts1        = last_e.get("timestamp", "")
        blk_tokens = last_e.get("total_tokens_saved", 0) - first_e.get("total_tokens_saved", 0)
        blk_cost   = last_e.get("compression_savings_usd", 0.0) - first_e.get("compression_savings_usd", 0.0)

        try:
            dt0      = datetime.fromisoformat(ts0.replace("Z", "+00:00"))
            dt1      = datetime.fromisoformat(ts1.replace("Z", "+00:00"))
            label    = dt0.strftime("%b %d, %H:%M")
            mins     = int((dt1 - dt0).total_seconds() / 60)
            dur_str  = f"{mins}m" if mins < 60 else f"{mins // 60}h {mins % 60}m"
        except Exception:
            label   = ts0
            dur_str = "?"

        row_htmls = []
        prev_tok  = first_e.get("total_tokens_saved", 0)
        prev_cost = first_e.get("compression_savings_usd", 0.0)
        for chk in block[1:]:
            chk_ts = chk.get("timestamp", "")
            try:
                chk_str = datetime.fromisoformat(chk_ts.replace("Z", "+00:00")).strftime("%H:%M:%S")
            except Exception:
                chk_str = chk_ts
            cur_tok  = chk.get("total_tokens_saved", 0)
            cur_cost = chk.get("compression_savings_usd", 0.0)
            row_htmls.append(
                f'<tr style="border-bottom:1px solid var(--surface1)">'
                f'<td style="padding:5px 10px;font-size:11px;color:var(--subtext0);font-family:monospace">{chk_str}</td>'
                f'<td style="padding:5px 10px;font-size:12px;color:var(--green)">+{cur_tok-prev_tok:,}</td>'
                f'<td style="padding:5px 10px;font-size:12px;color:var(--green)">+${cur_cost-prev_cost:.5f}</td>'
                f'<td style="padding:5px 10px;font-size:11px;color:var(--overlay0)">{cur_tok:,} total</td>'
                f'</tr>'
            )
            prev_tok, prev_cost = cur_tok, cur_cost

        # Newest checkpoints matter most; a long session (600+ checkpoints) would
        # otherwise dump its entire history into the DOM. Show the last 10, hide
        # the rest behind a toggle.
        MAX_VISIBLE_CHECKPOINTS = 10
        if len(row_htmls) > MAX_VISIBLE_CHECKPOINTS:
            hidden_rows  = row_htmls[:-MAX_VISIBLE_CHECKPOINTS]
            visible_rows = row_htmls[-MAX_VISIBLE_CHECKPOINTS:]
        else:
            hidden_rows, visible_rows = [], row_htmls
        rows = "".join(visible_rows)

        blk_id    = f"hr-blk-{bidx}"
        toggle_fn = (
            f"var d=document.getElementById('{blk_id}');"
            f"d.style.display=d.style.display==='none'?'block':'none';"
            f"this.querySelector('span.hr-arr').textContent=d.style.display==='none'?'▸ ':'▾ ';"
        )
        more_tbody = ""
        if hidden_rows:
            more_id = f"hr-more-{bidx}"
            more_fn = (
                f"var d=document.getElementById('{more_id}');"
                f"var showing=d.style.display!=='none';"
                f"d.style.display=showing?'none':'table-row-group';"
                f"this.querySelector('span').textContent=(showing?'▸ Show {len(hidden_rows)} earlier':'▾ Hide earlier');"
            )
            more_tbody = (
                f'<tbody><tr onclick="{h(more_fn)}" style="cursor:pointer;border-bottom:1px solid var(--surface1)">'
                f'<td colspan="4" style="padding:5px 10px;font-size:11px;color:var(--overlay0)">'
                f'<span>▸ Show {len(hidden_rows)} earlier</span></td></tr></tbody>'
                f'<tbody id="{more_id}" style="display:none">{"".join(hidden_rows)}</tbody>'
            )

        detail_html = (
            f'<table style="width:100%;border-collapse:collapse">'
            f'<thead><tr style="background:var(--base)">'
            f'<th style="padding:5px 10px;text-align:left;font-size:10px;color:var(--overlay0)">TIME</th>'
            f'<th style="padding:5px 10px;text-align:left;font-size:10px;color:var(--overlay0)">+TOKENS</th>'
            f'<th style="padding:5px 10px;text-align:left;font-size:10px;color:var(--overlay0)">+SAVED</th>'
            f'<th style="padding:5px 10px;text-align:left;font-size:10px;color:var(--overlay0)">CUMULATIVE</th>'
            f'</tr></thead>{more_tbody}<tbody>{rows}</tbody></table>'
        ) if rows else '<div style="padding:10px 14px;font-size:11px;color:var(--overlay0)">Single checkpoint.</div>'

        accordion_items.append(
            f'<div style="background:var(--surface0);border-radius:8px;margin-bottom:6px;overflow:hidden">'
            f'<div onclick="{h(toggle_fn)}" style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:10px 14px;cursor:pointer;user-select:none">'
            f'<div style="display:flex;align-items:center;gap:8px">'
            f'<span style="font-size:13px;color:var(--overlay0);font-family:monospace" class="hr-arr">'
            f'{"▾" if bidx == 0 else "▸"}</span>'
            f'<span style="font-size:13px;font-weight:600;color:var(--text)">{h(label)}</span>'
            f'<span style="font-size:11px;color:var(--overlay0)">{dur_str} · {len(block)} checkpoints</span>'
            f'</div>'
            f'<div style="font-size:12px;color:var(--green)">{blk_tokens:,} tok · ${blk_cost:.4f}</div>'
            f'</div>'
            f'<div id="{blk_id}" style="display:{"block" if bidx == 0 else "none"};'
            f'border-top:1px solid var(--surface1)">{detail_html}</div>'
            f'</div>'
        )

    history_html = ""
    if accordion_items:
        history_html = (
            '<div style="margin-top:4px">'
            '<div style="font-size:11px;font-weight:600;color:var(--subtext0);text-transform:uppercase;'
            'letter-spacing:.8px;margin-bottom:8px">Headroom Session Blocks (newest first)</div>'
            + "".join(accordion_items)
            + "</div>"
        )

    return f"""<div class="section-inner">
  <h2 class="section-title">Compression Savings</h2>
  <div id="headroom-panel">
    {total_html}
    {pipeline_html}
    {session_card}
    {history_html}
  </div>
</div>"""


def render_herder(rd, companion=False):
    """Spawn form + live agent roster, backed by Herdr.

    The target repo is the one selected in the dashboard's own repo dropdown —
    there is deliberately no second repo picker here. Two selectors for the same
    concept is exactly the kind of thing you get wrong at 1am.

    Static mode (`--static`) has no server to call, so the controls are replaced
    by a note rather than rendered dead — a button that silently does nothing is
    worse than an absent one.
    """
    repo_name = Path(rd["path"]).name
    desc = section_desc(
        "Start Claude Code sessions and watch them from here. The session opens in "
        "whichever repo the <b>dropdown at the top</b> is set to. Sessions run under "
        "<b>Herdr</b> and outlive this dashboard — closing the tab does not kill them, "
        "and <code>herdr agent attach &lt;name&gt;</code> drops you into one from a "
        "terminal.",
        icon="🐑", color="var(--mauve)",
    )

    if not companion:
        return ('<div class="section-inner"><h2 class="section-title">Herder</h2>'
                + desc
                + empty_state("Spawning needs the live dashboard. "
                              "Run: python3 scripts/utils/dashboard.py")
                + "</div>")

    if herder is None:
        return ('<div class="section-inner"><h2 class="section-title">Herder</h2>'
                + desc
                + empty_state(f"herder module unavailable: {HERDER_IMPORT_ERROR}")
                + "</div>")

    kind_opts = "\n".join(
        f'<option value="{k}"{" selected" if k == "claude" else ""}>{k}</option>'
        for k in herder.AGENT_KINDS
    )

    fld = ("background:var(--surface0);border:1px solid var(--surface1);color:var(--text);"
           "border-radius:6px;padding:7px 9px;font-size:12px;font-family:inherit")

    return f"""<div class="section-inner">
  <h2 class="section-title">Herder</h2>
  {desc}
  <div id="herder-status" style="font-size:11px;color:var(--subtext0);margin-bottom:14px">
    checking Herdr…</div>

  <div style="background:var(--surface0);border-radius:8px;padding:14px;margin-bottom:18px">
    <div style="font-size:11px;color:var(--subtext0);margin-bottom:12px">
      target repo —
      <span style="color:var(--mauve);font-weight:600;font-family:monospace">{h(repo_name)}</span>
      <span style="color:var(--overlay0)">· change it with the dropdown at the top</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
                gap:10px;margin-bottom:10px">
      <label style="font-size:10px;color:var(--subtext1)">agent
        <select id="hd-kind" onchange="herderLoadOptions()"
                style="{fld};width:100%;margin-top:3px">{kind_opts}</select></label>
      <label style="font-size:10px;color:var(--subtext1)">model
        <select id="hd-model" style="{fld};width:100%;margin-top:3px">
          <option value="">loading…</option></select></label>
      <label style="font-size:10px;color:var(--subtext1)">permission mode
        <select id="hd-mode" style="{fld};width:100%;margin-top:3px">
          <option value="">loading…</option></select></label>
      <label style="font-size:10px;color:var(--subtext1)">label
        <input id="hd-label" value="agent" style="{fld};width:100%;margin-top:3px"></label>
    </div>
    <div id="hd-opt-src" style="font-size:10px;color:var(--overlay0);margin-bottom:8px"></div>
    <textarea id="hd-prompt" rows="3"
      placeholder="Opening prompt (optional). / for skills, @ for files · Enter spawns, Shift+Enter new line."
      oninput="herderAcInput(this, repo)" onkeydown="herderSpawnKeydown(event)" onblur="herderAcClose()"
      style="{fld};width:100%;box-sizing:border-box;resize:vertical"></textarea>
    <div style="display:flex;align-items:center;gap:14px;margin-top:10px;flex-wrap:wrap">
      <button onclick="herderSpawn()" id="hd-spawn-btn"
        style="background:var(--mauve);color:var(--base);border:none;border-radius:6px;
               padding:8px 18px;font-size:12px;font-weight:600;cursor:pointer">
        Spawn agent</button>
      <span id="hd-spawn-msg" style="font-size:11px;color:var(--subtext0)"></span>
    </div>
  </div>

  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
    <span style="font-size:11px;color:var(--subtext1);font-weight:600">Running agents</span>
    <label style="font-size:10px;color:var(--overlay1);display:flex;align-items:center;gap:5px">
      <input type="checkbox" id="hd-live" checked> live tail (5s)</label>
    <span id="hd-live-note" style="font-size:10px;color:var(--overlay0)"></span>
  </div>
  <div id="herder-roster" style="display:grid;gap:12px;
       grid-template-columns:repeat(auto-fill,minmax(290px,1fr))">
    {empty_state("No agents running.")}
  </div>
</div>"""


def build_html(repos_data, harness_data, usage_sessions, pricing_snapshots, initial_idx=0, companion=False):
    repos = [rd["path"] for rd in repos_data]

    repo_opts = "\n".join(
        f'<option value="{h(p)}"{"" if i != initial_idx else " selected"}>'
        f'{h(Path(p).name)}</option>'
        for i, p in enumerate(repos)
    )

    nav = "\n".join(
        f'<a class="nav-item{" active" if key == "trust_battery" else ""}" '
        f'data-s="{key}" href="#" '
        f'onclick="show(\'{key}\');return false;">'
        f'<span class="nav-icon">{icon}</span><span>{label}</span></a>'
        for key, icon, label in SECTION_DEFS
    )

    # Render global (non-repo) sections once — these are repo-invariant
    skill_html      = render_skill_changes(harness_data, companion=companion)
    model_cost_html = render_model_cost(usage_sessions, pricing_snapshots)

    sections_map = {}
    for rd in repos_data:
        headroom_html = render_headroom(repo_path=rd["path"])
        # Scheduled tasks must be recomputed per repo — per-repo-scoped routines
        # (macro-eval, security-report, ...) each have their own state files.
        repo_scheduled_tasks = parse_scheduled_tasks(rd["path"])
        scheduled_html = render_scheduled_tasks(harness_data, repos_data, routines=repo_scheduled_tasks)
        sections_map[rd["path"]] = {
            "session_health": _combined_section("Session Health", "sh", [
                ("trust",   "⚡", "Trust Battery",   render_trust_battery(rd)),
                ("quality", "📊", "Session Quality", render_session_quality(rd)),
                ("pq",      "✨", "Prompt Quality",  render_prompt_quality()),
            ]),
            "herder":         render_herder(rd, companion=companion),
            "gitnexus":       render_gitnexus(rd, companion=companion),
            "workshop":       render_workshop(rd, companion=companion),
            "budget_efficiency": _combined_section("Budget & Efficiency", "be", [
                ("headroom", "🗜", "Headroom",        headroom_html),
                ("context",  "🧵", "Context Health",  render_context_health(rd)),
                ("cost",     "💰", "Model Cost",      model_cost_html),
            ]),
            "automation": _combined_section("Automation", "au", [
                ("hooks",  "🪝", "Hooks History",    render_hooks_history(rd)),
                ("sched",  "📅", "Scheduled Tasks",  scheduled_html),
                ("audit",  "🤖", "Automation Audit", render_automation_audit(rd, harness_data)),
            ]),
            "knowledge_base": _combined_section("Knowledge Base", "kb", [
                ("memory", "🧠", "Memory Changes", render_memory_changes(rd, harness_data)),
                ("skills", "🎯", "Skill Changes",  skill_html),
            ]),
        }

    sj  = json.dumps(sections_map, ensure_ascii=False)
    # Escape any </script> or </Script> etc. that would break the enclosing script tag
    sj  = _escape_script_close(sj)
    ir  = json.dumps(repos[initial_idx] if repos else "")

    gn_funs = """
function startGitnexus(repoPath) {
  var st   = document.getElementById('gn-status');
  var sbtn = document.getElementById('gn-start-btn');
  var cbtn = document.getElementById('gn-copy-btn');
  var hint = document.getElementById('gn-copy-hint');
  if (st)   st.textContent = 'Starting gitnexus serve…';
  if (sbtn) sbtn.style.display = 'none';
  if (cbtn) cbtn.style.display = 'none';
  if (hint) hint.style.display = 'none';
  fetch('/api/gitnexus-serve?repo=' + encodeURIComponent(repoPath), { method: 'POST' })
    .then(function() { pollGitnexus(0); })
    .catch(function() {
      if (st) st.textContent = 'Could not reach companion server';
    });
}
function pollGitnexus(n) {
  var st = document.getElementById('gn-status');
  if (n > 20) {
    if (st) st.textContent = 'Timed out — is gitnexus installed?';
    return;
  }
  if (st) st.textContent = 'Waiting for gitnexus… (' + (n + 1) + '/20)';
  var ctrl = new AbortController();
  setTimeout(function() { ctrl.abort(); }, 1500);
  fetch(GN_PROBE_URL, { signal: ctrl.signal })
    .then(function(res) {
      if (!res.ok) { setTimeout(function() { pollGitnexus(n + 1); }, 1000); return; }
      var f  = document.getElementById('gn-frame');
      var fb = document.getElementById('gn-fallback');
      if (f)  { f.src = f.dataset.src || GN_WEB_UI; f.style.display = 'block'; }
      if (fb) { fb.style.display = 'none'; }
    })
    .catch(function() {
      setTimeout(function() { pollGitnexus(n + 1); }, 1000);
    });
}""" if companion else ""

    workshop_funs = """
function _wsOpen()       { window.open('http://localhost:5899', '_blank'); }
function _wsHover(el,on) { el.style.background = on ? 'var(--surface0)' : ''; }
function _wsCopy(el)     { navigator.clipboard.writeText('raindrop workshop'); el.textContent = '✓ Copied!'; }

function loadWorkshopRuns() {
  var panel = document.getElementById('ws-runs-panel');
  if (!panel) return;
  var eventName = panel.dataset.eventName || '';
  panel.innerHTML = '<div style="color:var(--subtext0);padding:40px;text-align:center">Loading…</div>';
  fetch('/api/workshop-runs?event_name=' + encodeURIComponent(eventName))
    .then(function(r) { if (!r.ok) throw new Error('offline'); return r.json(); })
    .then(function(runs) { _wsRenderRuns(runs, panel, eventName); })
    .catch(function() { _wsRenderOffline(panel); });
}

function _wsRelTime(ms) {
  var d = Date.now() - ms;
  if (d < 60000)   return Math.round(d / 1000) + 's ago';
  if (d < 3600000) return Math.round(d / 60000) + 'm ago';
  if (d < 86400000) return Math.round(d / 3600000) + 'h ago';
  return new Date(ms).toLocaleDateString();
}

function _wsEsc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function _wsRenderRuns(runs, panel, eventName) {
  if (!runs.length) {
    panel.innerHTML =
      '<div style="color:var(--subtext0);padding:40px;text-align:center">' +
      '<div style="font-size:28px;margin-bottom:8px">🔬</div>' +
      '<div>No runs yet for <code style="color:var(--text)">' + _wsEsc(eventName) + '</code></div>' +
      '<div style="margin-top:8px;font-size:11px;color:var(--overlay0)">Run the app and a trace will appear here.</div>' +
      '</div>';
    return;
  }
  var shown = Math.min(runs.length, 50);
  var rows = runs.slice(0, shown).map(function(r) {
    var ts     = _wsRelTime(r.started_at);
    var input  = '';
    try { input = (JSON.parse(r.metadata || '{}').input || '').slice(0, 80); } catch(e) {}
    if (!input) input = '(no input)';
    var status = r.finished
      ? '<span style="color:var(--green)">&#x2713;</span>'
      : '<span style="color:var(--yellow)">⧗ live</span>';
    var user   = _wsEsc(r.user_id || '–');
    var convo  = r.convo_id ? '<span style="color:var(--overlay0)">' + _wsEsc(r.convo_id) + '</span>' : '';
    var spans  = r.span_count || 0;
    return '<tr style="border-bottom:1px solid var(--surface0);cursor:pointer"'
      + ' onclick="_wsOpen()" onmouseenter="_wsHover(this,true)" onmouseleave="_wsHover(this,false)">' +
      '<td style="padding:10px 12px;color:var(--subtext1);font-size:11px;white-space:nowrap">' + ts + '</td>' +
      '<td style="padding:10px 12px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">' + _wsEsc(input) + '</td>' +
      '<td style="padding:10px 12px;font-size:11px;color:var(--subtext1)">' + user + (convo ? '<br>' + convo : '') + '</td>' +
      '<td style="padding:10px 8px;font-size:11px;text-align:center">' + status + '</td>' +
      '<td style="padding:10px 12px;font-size:11px;color:var(--overlay0);text-align:right">' + spans + '</td>' +
      '</tr>';
  }).join('');
  panel.innerHTML =
    '<div style="padding:8px 12px;font-size:11px;color:var(--subtext0);' +
    'border-bottom:1px solid var(--surface0);display:flex;justify-content:space-between;align-items:center">' +
    '<span>Showing ' + shown + ' of ' + runs.length + ' runs — click a row to open in Workshop</span>' +
    '<span style="color:var(--overlay0)">' + _wsEsc(eventName) + '</span></div>' +
    '<table style="width:100%;border-collapse:collapse"><thead>' +
    '<tr style="background:var(--base)">' +
    '<th style="padding:6px 12px;text-align:left;font-size:10px;color:var(--overlay0);font-weight:500">TIME</th>' +
    '<th style="padding:6px 12px;text-align:left;font-size:10px;color:var(--overlay0);font-weight:500">INPUT</th>' +
    '<th style="padding:6px 12px;text-align:left;font-size:10px;color:var(--overlay0);font-weight:500">USER / SESSION</th>' +
    '<th style="padding:6px 8px;font-size:10px;color:var(--overlay0);font-weight:500">OK</th>' +
    '<th style="padding:6px 12px;text-align:right;font-size:10px;color:var(--overlay0);font-weight:500">SPANS</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table>';
}

function _wsRenderOffline(panel) {
  panel.innerHTML =
    '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;' +
    'height:240px;gap:12px;padding:40px;text-align:center">' +
    '<div style="font-size:32px">🔬</div>' +
    '<div style="color:var(--subtext0);font-size:13px">Workshop not running</div>' +
    '<button style="background:var(--teal);color:var(--crust);border:none;border-radius:6px;' +
    'padding:9px 18px;font-size:12px;font-weight:600;cursor:pointer" ' +
    'onclick="startWorkshop()">▶ Start raindrop workshop</button>' +
    '<div style="background:var(--surface0);border-radius:6px;padding:8px 16px;' +
    'font-family:monospace;font-size:12px;color:var(--text);cursor:pointer" ' +
    'onclick="_wsCopy(this)">raindrop workshop  📋</div>' +
    '</div>';
}

function startWorkshop() {
  var panel = document.getElementById('ws-runs-panel');
  if (panel) panel.innerHTML = '<div style="color:var(--subtext0);padding:40px;text-align:center">Starting Workshop…</div>';
  fetch('/api/workshop-start', { method: 'POST' })
    .then(function() { _wsPollThenLoad(0); })
    .catch(function() {
      if (panel) panel.innerHTML = '<div style="color:var(--red);padding:40px;text-align:center">Could not reach companion server</div>';
    });
}

function _wsPollThenLoad(n) {
  if (n > 20) { var panel = document.getElementById('ws-runs-panel'); if (panel) _wsRenderOffline(panel); return; }
  fetch('/api/workshop-runs')
    .then(function(r) { if (r.ok) loadWorkshopRuns(); else throw new Error(); })
    .catch(function() { setTimeout(function() { _wsPollThenLoad(n + 1); }, 1000); });
}

function runEvalLoop(repoPath, repoName) {
  var btn = document.getElementById('ws-eval-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⚗ Starting…'; }
  fetch('/api/workshop-eval?repo=' + encodeURIComponent(repoPath), { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function() {
      if (btn) { btn.textContent = '✓ Running in terminal'; }
      setTimeout(function() { if (btn) { btn.disabled = false; btn.textContent = '⚗ Run Eval Loop'; } }, 8000);
    })
    .catch(function() { if (btn) { btn.disabled = false; btn.textContent = '⚗ Run Eval Loop'; } });
}

function _scEsc(s) {
  var d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function _scRenderPanel(content, age) {
  var panel = document.getElementById('sc-panel');
  if (!panel) return;
  panel.innerHTML =
    '<div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--surface0)">' +
    '<div style="color:var(--overlay0);font-size:12px;margin-bottom:10px">' +
    'Proposal generated: <strong style="color:var(--subtext1)">' + _scEsc(age) + '</strong></div>' +
    '<div style="font-size:12px;line-height:1.7;white-space:pre-wrap">' + _scEsc(content) + '</div>' +
    '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:12px">' +
    '<input id="sc-instruction" type="text" value="apply all" ' +
    'style="background:var(--surface0);color:var(--text);border:1px solid var(--overlay0);' +
    'border-radius:6px;padding:8px 12px;font-size:12px;flex:1;min-width:180px"/>' +
    '<button id="sc-apply-btn" style="background:var(--green);color:var(--crust);border:none;' +
    'border-radius:6px;padding:9px 18px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap" ' +
    'onclick="runSkillCuratorApply()">✅ Apply Approved</button>' +
    '<button id="sc-propose-btn" style="background:transparent;color:var(--subtext0);' +
    'border:1px solid var(--surface0);border-radius:6px;padding:9px 14px;font-size:12px;' +
    'cursor:pointer;white-space:nowrap" onclick="runSkillCuratorPropose()">🔍 Re-analyze</button>' +
    '</div></div>';
}

function _scPollProposal(n) {
  if (n > 40) {
    var btn = document.getElementById('sc-propose-btn');
    if (btn) { btn.disabled = false; btn.textContent = '🔍 Analyze & Propose (taking longer than expected — try again)'; }
    return;
  }
  fetch('/api/skill-curator-proposal')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d && d.content) { _scRenderPanel(d.content, d.age); }
      else { setTimeout(function() { _scPollProposal(n + 1); }, 3000); }
    })
    .catch(function() { setTimeout(function() { _scPollProposal(n + 1); }, 3000); });
}

// ── Herder ──────────────────────────────────────────────────────────────────
// Every herder call carries the per-process token. Without it the server replies
// 403, so a page not served by this dashboard process cannot drive the API.
var HERDER_TOKEN = '__HERDER_TOKEN__';
var _hdRosterSig = '';

function _hdFetch(url, opts) {
  opts = opts || {};
  opts.headers = opts.headers || {};
  opts.headers['X-Herder-Token'] = HERDER_TOKEN;
  return fetch(url, opts);
}

function _hdBadge(status) {
  var map = { working: ['#f9e2af', 'working'], idle: ['#89b4fa', 'idle'],
              done: ['#a6e3a1', 'done'], blocked: ['#f38ba8', 'blocked'] };
  var e = map[status] || ['#6c7086', status || 'unknown'];
  return '<span style="display:inline-block;padding:2px 8px;border-radius:20px;' +
         'font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:.6px;' +
         'color:' + e[0] + ';background:' + e[0] + '22;border:1px solid ' + e[0] + '55">' +
         e[1] + '</span>';
}

// Model and permission-mode choices are pulled from the selected agent itself —
// never hardcoded here. Re-fetched whenever the agent kind changes.
function herderLoadOptions() {
  var kind = document.getElementById('hd-kind');
  var mSel = document.getElementById('hd-model');
  var pSel = document.getElementById('hd-mode');
  var src  = document.getElementById('hd-opt-src');
  if (!kind || !mSel || !pSel) { return; }
  mSel.innerHTML = '<option value="">loading…</option>';
  pSel.innerHTML = '<option value="">loading…</option>';

  _hdFetch('/api/herder-options?kind=' + encodeURIComponent(kind.value))
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var mo = '<option value="">(agent default)</option>';
      var models = (d.models && d.models.models) || [];
      var aliases = (d.models && d.models.aliases) || [];
      if (aliases.length) {
        mo += '<optgroup label="aliases — always the latest of that family">';
        aliases.forEach(function(a) { mo += '<option value="' + a + '">' + a + '</option>'; });
        mo += '</optgroup>';
      }
      if (models.length) {
        mo += '<optgroup label="pinned versions">';
        models.forEach(function(m) { mo += '<option value="' + m + '">' + m + '</option>'; });
        mo += '</optgroup>';
      }
      mSel.innerHTML = mo;

      var modes = (d.permission_modes && d.permission_modes.modes) || [];
      var po = '';
      modes.forEach(function(m) {
        var sel = (m === 'acceptEdits') ? ' selected' : '';
        var warn = (m === 'bypassPermissions' || m === 'dontAsk') ? ' ⚠' : '';
        po += '<option value="' + m + '"' + sel + '>' + m + warn + '</option>';
      });
      pSel.innerHTML = po || '<option value="acceptEdits">acceptEdits</option>';

      // Say plainly when a list is a fallback rather than something we read from
      // the agent — a guessed list that looks authoritative is worse than none.
      if (src) {
        var mOk = d.models && d.models.discovered;
        var pOk = d.permission_modes && d.permission_modes.discovered;
        var bits = [];
        bits.push((mOk ? '✓ models from ' : '⚠ models NOT discovered — ') +
                  ((d.models && d.models.source) || '?'));
        bits.push((pOk ? '✓ modes from ' : '⚠ modes NOT discovered — ') +
                  ((d.permission_modes && d.permission_modes.source) || '?'));
        src.innerHTML = bits.join(' · ');
        src.style.color = (mOk && pOk) ? 'var(--overlay0)' : '#f9e2af';
      }
    })
    .catch(function(e) {
      mSel.innerHTML = '<option value="">(agent default)</option>';
      pSel.innerHTML = '<option value="acceptEdits">acceptEdits</option>';
      if (src) { src.textContent = 'could not load agent options: ' + e; }
    });
}

function _hdNum(n) {
  if (n === null || n === undefined) { return '—'; }
  if (n >= 1000000) { return (n / 1000000).toFixed(1) + 'M'; }
  if (n >= 1000)    { return (n / 1000).toFixed(1) + 'k'; }
  return String(n);
}

// One box per agent, each tailing its own pane, so several runs are watchable at
// once rather than one at a time through a shared log pane.
function _hdBox(a) {
  _hdCwd[a.name] = a.cwd;   // the repo `/` and `@` complete against for this agent
  var badge = _hdBadge(a.status);
  var meta = [];
  if (a.repo)  { meta.push(a.repo); }
  if (a.model) { meta.push(a.model); }
  if (a.permission_mode) { meta.push(a.permission_mode); }

  var spend = '<span style="color:var(--overlay0)">spend: not yet attributed</span>';
  if (a.spend) {
    spend = 'tokens <b style="color:var(--text)">' + _hdNum(a.spend.tokens) + '</b>' +
            ' · cost-weighted <b style="color:var(--text)">' +
            _hdNum(Math.round(a.spend.weighted_cost)) + '</b>' +
            ' · cache-create ' + _hdNum(a.spend.cache_create);
  } else if (!a.herder_spawned) {
    spend = '<span style="color:var(--overlay0)">opened outside the herder — no ledger</span>';
  }

  var btn = 'background:var(--surface1);color:var(--text);border:none;border-radius:5px;' +
            'padding:3px 9px;font-size:10.5px;cursor:pointer';
  var fld = 'background:var(--surface0);border:1px solid var(--surface1);color:var(--text);' +
            'border-radius:6px;padding:6px 9px;font-size:10px;font-family:inherit';

  // Collapsed by default: the grid answers "what is running" at a glance, and the
  // disclosure answers "what is it doing" only for the one you open.
  return '' +
  '<div style="background:var(--surface0);border:1px solid var(--surface1);border-radius:8px;padding:11px;min-width:0">' +
    '<div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:5px">' +
      '<span style="font-family:monospace;font-size:11.5px;color:var(--text);overflow:hidden;' +
        'text-overflow:ellipsis;white-space:nowrap;max-width:140px">' + a.name + '</span>' +
      badge +
    '</div>' +
    '<div style="font-size:9.5px;color:var(--subtext0);margin-bottom:4px;overflow:hidden;' +
      'text-overflow:ellipsis;white-space:nowrap">' + meta.join(' · ') + '</div>' +
    '<div style="font-size:9.5px;color:var(--subtext0);margin-bottom:7px">' + spend + '</div>' +
    '<div style="display:flex;gap:5px;margin-bottom:7px">' +
      '<button onclick="herderStop(\\'' + a.workspace_id + '\\')" style="background:#f38ba822;color:#f38ba8;border:1px solid #f38ba855;border-radius:5px;padding:3px 9px;font-size:10.5px;cursor:pointer">stop</button>' +
    '</div>' +
    '<div id="hdchat-' + a.name + '" style="background:var(--crust);border:1px solid var(--surface1);' +
      'border-radius:6px;padding:9px;font-size:10px;line-height:1.5;max-height:400px;overflow-y:auto;' +
      'margin-bottom:8px;display:flex;flex-direction:column;gap:8px">loading…</div>' +
    '<div id="hderr-' + a.name + '" style="display:none;margin-bottom:6px;font-size:9.5px;' +
      'color:#f38ba8"></div>' +
    '<div style="display:flex;gap:6px;align-items:flex-end">' +
      '<textarea id="hdinput-' + a.name + '" data-agent="' + a.name + '" ' +
        'placeholder="Reply… / for skills, @ for files · Shift+Enter new line" ' +
        'style="flex:1;' + fld + ';font-size:10px;padding:6px;border-radius:4px;' +
        'resize:none;min-height:32px;max-height:100px" onkeydown="herderInputKeydown(event, \\'' + a.name + '\\')" ' +
        'oninput="herderAcInput(this, _hdCwd[this.dataset.agent])" onblur="herderAcClose()"></textarea>' +
      '<button onclick="herderFileTag(\\'' + a.name + '\\')" style="' + btn + ';padding:6px 10px">📎</button>' +
      '<button onclick="herderSendReply(\\'' + a.name + '\\')" style="' + btn + ';padding:6px 12px">send</button>' +
    '</div>' +
    '<details id="hddet-' + a.name + '" ontoggle="herderChatOpen(\\'' + a.name + '\\')" style="margin-top:6px">' +
      '<summary style="cursor:pointer;font-size:9.5px;color:var(--overlay0);outline:none">raw pane</summary>' +
      '<pre id="hdlog-' + a.name + '" style="background:var(--crust);border-radius:6px;' +
        'padding:9px;font-size:9px;max-height:200px;overflow:auto;white-space:pre-wrap;' +
        'color:var(--subtext1);margin-top:6px;white-space:pre-wrap;word-break:break-word"></pre>' +
    '</details>' +
  '</div>';
}

// Render the transcript feed: reasoning, tool calls, tool results — the same
// shape as a chat transcript, not the terminal's redrawn TUI.
function _hdEventHtml(e) {
  var wrap = 'margin-bottom:6px;padding-left:8px;border-left:2px solid ';
  if (e.t === 'thinking') {
    return '<div style="' + wrap + 'var(--overlay0);color:var(--overlay1);font-style:italic">' +
           _hdEsc(e.text) + '</div>';
  }
  if (e.t === 'text') {
    return '<div style="' + wrap + 'var(--mauve);color:var(--text)">' + _hdEsc(e.text) + '</div>';
  }
  if (e.t === 'tool') {
    return '<div style="' + wrap + '#89b4fa;color:var(--subtext1)">' +
           '<b style="color:#89b4fa">' + _hdEsc(e.name) + '</b>' +
           (e.summary ? ' <span style="color:var(--overlay1)">' + _hdEsc(e.summary) + '</span>' : '') +
           '</div>';
  }
  if (e.t === 'result') {
    var col = e.error ? '#f38ba8' : '#a6e3a1';
    return '<div style="' + wrap + col + ';color:var(--overlay1)">' +
           (e.error ? '✗ ' : '✓ ') + _hdEsc(e.name) +
           (e.preview ? ' — ' + _hdEsc(e.preview) : '') + '</div>';
  }
  return '';
}

function _hdEsc(s) {
  return String(s === null || s === undefined ? '' : s)
    .split('&').join('&amp;').split('<').join('&lt;').split('>').join('&gt;');
}

function herderStream(name, force) {
  var det = document.getElementById('hddet-' + name);
  if (!det) { return; }
  // Only poll what is open. Streaming every collapsed agent would read every
  // transcript on the machine every 5 seconds for output nobody is looking at.
  if (!det.open && !force) { return; }
  var box = document.getElementById('hdfeed-' + name);
  if (!box) { return; }

  _hdFetch('/api/herder-stream?name=' + encodeURIComponent(name))
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.error)   { box.textContent = d.error; return; }
      if (d.pending) { box.innerHTML = '<span style="color:var(--overlay0)">no transcript yet — the session has not written its first line</span>'; return; }
      var evs = d.events || [];
      if (!evs.length) {
        box.innerHTML = '<span style="color:var(--overlay0)">no activity recorded yet</span>';
        return;
      }
      var atBottom = (box.scrollHeight - box.scrollTop - box.clientHeight) < 40;
      box.innerHTML = (d.truncated
        ? '<div style="color:var(--overlay0);margin-bottom:6px">…earlier activity trimmed</div>' : '')
        + evs.map(_hdEventHtml).join('');
      if (atBottom) { box.scrollTop = box.scrollHeight; }
    })
    .catch(function(e) { box.textContent = String(e); });
}

// The rendered terminal, kept as an escape hatch: the feed above is derived from
// the transcript, so the pane is the way to see what the TUI itself shows.
function herderRawPane(name) {
  var pre = document.getElementById('hdlog-' + name);
  if (!pre) { return; }
  pre.style.display = 'block';
  pre.textContent = 'reading pane…';
  _hdFetch('/api/herder-read?name=' + encodeURIComponent(name))
    .then(function(r) { return r.json(); })
    .then(function(d) { pre.textContent = d.output || d.error || '(empty)'; })
    .catch(function(e) { pre.textContent = String(e); });
}

function herderRefresh() {
  var st = document.getElementById('herder-status');
  _hdFetch('/api/herder-status')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!st) { return; }
      if (d.error)        { st.innerHTML = '<span style="color:#f38ba8">' + d.error + '</span>'; }
      else if (!d.installed) { st.innerHTML = '<span style="color:#f38ba8">Herdr not installed — curl -fsSL https://herdr.dev/install.sh | sh</span>'; }
      else if (!d.running)   { st.innerHTML = '<span style="color:#f9e2af">Herdr server not running — it starts automatically on first spawn.</span>'; }
      else { st.innerHTML = 'Herdr running · ' + d.agents + ' agent(s), ' + d.workspaces + ' workspace(s)'; }
    })
    .catch(function() { if (st) { st.textContent = 'dashboard server unreachable'; } });

  _hdFetch('/api/herder-list')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var box = document.getElementById('herder-roster');
      if (!box) { return; }
      // Scope the roster to the sidebar's repo: an agent belongs to the repo its
      // cwd is, or sits under. Herdr itself is machine-wide, so the list is not.
      var all = (d && d.agents) || [];
      var rows = all.filter(_hdInRepo);
      if (!rows.length) {
        // Clear the signature, or an agent set that returns unchanged after this
        // empty state would match it and never be re-rendered over the message.
        _hdRosterSig = '';
        var other = all.length - rows.length;
        box.innerHTML = '<div style="padding:36px;text-align:center;color:var(--overlay0);font-size:13px">' +
          'No agents running for this repo.' +
          (other ? ' ' + other + ' running in other repos.' : '') + '</div>';
        var none = document.getElementById('hd-live-note');
        if (none) { none.textContent = '0 agent(s) in this repo'; }
        return;
      }
      // Only rebuild the boxes when the set of agents changes. Re-rendering every
      // poll would reset each pre's scroll position mid-read.
      // Rebuild only when the agent set or a status changes; a full re-render on
      // every poll would collapse any <details> the user has open.
      var sig = rows.map(function(a) { return a.name + ':' + a.status; }).join('|');
      if (sig !== _hdRosterSig) {
        var openNames = rows.filter(function(a) {
          var d = document.getElementById('hddet-' + a.name);
          return d && d.open;
        }).map(function(a) { return a.name; });

        // A status change rebuilds every card. Carry each card's half-typed reply,
        // caret, focus, error line and chat scroll across it, so a working→idle
        // flip does not eat what the user is typing or yank them out of history.
        var keep = {};
        rows.forEach(function(a) {
          var inp = document.getElementById('hdinput-' + a.name);
          var chat = document.getElementById('hdchat-' + a.name);
          var err = document.getElementById('hderr-' + a.name);
          keep[a.name] = {
            value: inp ? inp.value : '',
            s: inp ? inp.selectionStart : 0,
            e: inp ? inp.selectionEnd : 0,
            focused: !!inp && document.activeElement === inp,
            disabled: !!inp && inp.disabled,
            err: err ? err.textContent : '',
            chat: chat ? chat.innerHTML : null,
            scroll: chat ? chat.scrollTop : 0
          };
        });

        // Snapshot the open completion list: removing its textarea may fire blur,
        // which closes and clears it before it can be re-anchored below.
        var ac = (_hdAc.el && _hdAc.el.dataset && _hdAc.el.dataset.agent && _hdAc.items.length)
          ? { name: _hdAc.el.dataset.agent, items: _hdAc.items, idx: _hdAc.idx,
              start: _hdAc.start, kind: _hdAc.kind, repo: _hdAc.repo }
          : null;

        _hdRosterSig = sig;
        box.innerHTML = rows.map(_hdBox).join('');

        rows.forEach(function(a) {
          var k = keep[a.name];
          var inp = document.getElementById('hdinput-' + a.name);
          var chat = document.getElementById('hdchat-' + a.name);
          var err = document.getElementById('hderr-' + a.name);
          if (inp) {
            inp.value = k.value;
            inp.disabled = k.disabled;
            if (k.focused) { inp.focus(); inp.setSelectionRange(k.s, k.e); }
          }
          if (err && k.err) { err.textContent = k.err; err.style.display = 'block'; }
          if (chat && k.chat !== null) { chat.innerHTML = k.chat; chat.scrollTop = k.scroll; }
        });
        var acEl = ac && document.getElementById('hdinput-' + ac.name);
        if (acEl && document.activeElement === acEl) {
          _hdAc.el = acEl; _hdAc.items = ac.items; _hdAc.idx = ac.idx;
          _hdAc.start = ac.start; _hdAc.kind = ac.kind; _hdAc.repo = ac.repo;
          _hdAcRender();
        } else if (_hdAc.el && !document.body.contains(_hdAc.el)) {
          herderAcClose();
        }

        openNames.forEach(function(n) {
          var d = document.getElementById('hddet-' + n);
          if (d) { d.open = true; }
        });
      }
      var live = document.getElementById('hd-live');
      if (live && live.checked) {
        rows.forEach(function(a) {
          var chat = document.getElementById('hdchat-' + a.name);
          if (chat) { herderUpdateChat(a.name); }
        });
      }

      var note = document.getElementById('hd-live-note');
      if (note) {
        var open = rows.filter(function(a) {
          var d = document.getElementById('hddet-' + a.name);
          return d && d.open;
        }).length;
        note.textContent = rows.length + ' agent(s)' +
          (open ? ' — ' + open + ' expanded, streaming' : ' — expand one to stream it');
      }
    })
    .catch(function() {});
}

function _hdInRepo(a) {
  var root = String(repo || '');
  while (root.length > 1 && root.charAt(root.length - 1) === '/') { root = root.slice(0, -1); }
  var cwd = String(a.cwd || '');
  return !!root && (cwd === root || cwd.indexOf(root + '/') === 0);
}

function herderSpawn() {
  var btn = document.getElementById('hd-spawn-btn');
  var msg = document.getElementById('hd-spawn-msg');
  // `repo` is the dashboard-wide selection driven by the top dropdown
  // (switchRepo). The herder deliberately has no repo picker of its own.
  var body = {
    repo:  repo,
    label: document.getElementById('hd-label').value || 'agent',
    kind:  document.getElementById('hd-kind').value,
    model: document.getElementById('hd-model').value,
    prompt: document.getElementById('hd-prompt').value,
    permission_mode: document.getElementById('hd-mode').value || 'acceptEdits'
  };
  if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
  if (msg) { msg.textContent = 'starting a session — this takes a few seconds'; }
  _hdFetch('/api/herder-spawn', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (btn) { btn.disabled = false; btn.textContent = 'Spawn agent'; }
      if (!msg) { return; }
      if (d.ok) {
        msg.innerHTML = '<span style="color:#a6e3a1">started ' + d.agent.name +
                        ' (' + d.agent.argv.join(' ') + ')</span>';
        document.getElementById('hd-prompt').value = '';
      } else {
        // Errors carry herdr and pane text — escape, never inject as HTML.
        msg.innerHTML = '<span style="color:#f38ba8">' + _hdEsc(d.error || 'failed') + '</span>' +
          (d.blocked_on
            ? '<pre style="' + _HD_MONO + 'margin:6px 0 0;background:var(--crust);border:1px solid #f38ba855;' +
              'border-radius:4px;padding:7px 9px;font-size:10.5px;line-height:1.45;white-space:pre-wrap;' +
              'color:var(--text)">' + _hdEsc(d.blocked_on) + '</pre>'
            : '');
      }
      herderRefresh();
    })
    .catch(function(e) {
      if (btn) { btn.disabled = false; btn.textContent = 'Spawn agent'; }
      if (msg) { msg.innerHTML = '<span style="color:#f38ba8">' + e + '</span>'; }
    });
}

function herderStop(ws) {
  _hdFetch('/api/herder-stop?workspace=' + encodeURIComponent(ws), { method: 'POST' })
    .then(function() { herderRefresh(); })
    .catch(function() {});
}

var _hdChat = {};

// Chat markdown. Agents answer in markdown (lists, fences, `paths`, tables);
// shown as escaped plain text it reads as one run-on paragraph. Line- and
// character-scanning only — no regex, per the repo-wide ban. Every piece of
// agent text goes through _hdEsc before it reaches innerHTML.
var _HD_MONO = 'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;';

function _hdInline(s) {
  var out = '';
  var i = 0;
  while (i < s.length) {
    var c = s.charAt(i);
    if (c === '`') {
      var end = s.indexOf('`', i + 1);
      if (end > i + 1) {
        var code = s.slice(i + 1, end);
        // Paths get their own colour so files stand out from commands.
        var isPath = code.indexOf(' ') === -1 && code.indexOf('/') !== -1;
        out += '<code style="' + _HD_MONO + 'background:var(--surface0);padding:1px 5px;' +
               'border-radius:3px;font-size:10.5px;color:' + (isPath ? 'var(--peach)' : 'var(--text)') + '">' +
               _hdEsc(code) + '</code>';
        i = end + 1;
        continue;
      }
    }
    if (c === '*' && s.charAt(i + 1) === '*') {
      var close = s.indexOf('**', i + 2);
      if (close > i + 2) {
        out += '<strong style="color:var(--text);font-weight:600">' + _hdInline(s.slice(i + 2, close)) + '</strong>';
        i = close + 2;
        continue;
      }
    }
    out += _hdEsc(c);
    i++;
  }
  return out;
}

function _hdHeading(t) {
  var d = 0;
  while (d < t.length && t.charAt(d) === '#') { d++; }
  if (d >= 1 && d <= 6 && t.charAt(d) === ' ') { return { depth: d, body: t.slice(d + 1) }; }
  return null;
}

// `- x`, `* x`, `+ x` or `12. x` / `12) x`; null for anything else.
function _hdListItem(line) {
  var s = line.trimStart();
  var indent = line.length - s.length;
  var two = s.slice(0, 2);
  if (two === '- ' || two === '* ' || two === '+ ') { return { indent: indent, marker: '•', body: s.slice(2) }; }
  var n = 0;
  while (n < s.length && s.charAt(n) >= '0' && s.charAt(n) <= '9') { n++; }
  if (n > 0 && n < 4 && (s.charAt(n) === '.' || s.charAt(n) === ')') && s.charAt(n + 1) === ' ') {
    return { indent: indent, marker: s.slice(0, n) + '.', body: s.slice(n + 2) };
  }
  return null;
}

function _hdList(items) {
  var base = Math.min.apply(null, items.map(function(it) { return it.indent; }));
  return '<div style="margin:0 0 6px;display:flex;flex-direction:column;gap:3px">' +
    items.map(function(it) {
      var level = Math.min(3, Math.floor((it.indent - base) / 2));
      return '<div style="display:flex;gap:6px;margin-left:' + (level * 14) + 'px">' +
             '<span style="flex:none;min-width:16px;text-align:right;color:var(--mauve);' +
             'font-weight:600;font-variant-numeric:tabular-nums">' + _hdEsc(it.marker) + '</span>' +
             '<span style="flex:1;min-width:0">' + it.lines.map(_hdInline).join('<br>') + '</span></div>';
    }).join('') + '</div>';
}

function _hdTableCells(t) {
  var parts = t.split('|');
  if (parts.length && parts[0].trim() === '') { parts.shift(); }
  if (parts.length && parts[parts.length - 1].trim() === '') { parts.pop(); }
  return parts.map(function(p) { return p.trim(); });
}

function _hdIsTableRule(t) {
  for (var i = 0; i < t.length; i++) {
    if ('|-: '.indexOf(t.charAt(i)) === -1) { return false; }
  }
  return t.indexOf('-') !== -1;
}

function _hdTable(rows) {
  if (!rows.length) { return ''; }
  var td = 'padding:3px 8px;border:1px solid var(--surface0);vertical-align:top;text-align:left';
  var th = td + ';font-weight:600;color:var(--mauve);background:var(--surface0)';
  var cells = function(row, style, tag) {
    return '<tr>' + row.map(function(c) {
      return '<' + tag + ' style="' + style + '">' + _hdInline(c) + '</' + tag + '>';
    }).join('') + '</tr>';
  };
  return '<div style="overflow-x:auto;margin:0 0 8px"><table style="border-collapse:collapse;font-size:10.5px">' +
         '<thead>' + cells(rows[0], th, 'th') + '</thead><tbody>' +
         rows.slice(1).map(function(r) { return cells(r, td, 'td'); }).join('') +
         '</tbody></table></div>';
}

function _hdMarkdown(text) {
  var lines = String(text === null || text === undefined ? '' : text).split('\\n');
  var out = '';
  var para = [];
  var flush = function() {
    if (para.length) { out += '<p style="margin:0 0 6px">' + para.map(_hdInline).join('<br>') + '</p>'; }
    para = [];
  };
  var i = 0;
  while (i < lines.length) {
    var t = lines[i].trim();

    if (t.slice(0, 3) === '```') {
      flush();
      var lang = t.slice(3).trim();
      var body = [];
      i++;
      while (i < lines.length && lines[i].trim().slice(0, 3) !== '```') { body.push(lines[i]); i++; }
      i++;  // closing fence
      out += '<div style="margin:2px 0 8px">' +
             (lang ? '<div style="font-size:8.5px;color:var(--overlay0);text-transform:uppercase;margin-bottom:2px">' +
                     _hdEsc(lang) + '</div>' : '') +
             '<pre style="' + _HD_MONO + 'margin:0;background:var(--crust);border:1px solid var(--surface0);' +
             'border-radius:4px;padding:7px 9px;font-size:10.5px;line-height:1.5;white-space:pre;' +
             'overflow-x:auto;color:var(--text)">' + _hdEsc(body.join('\\n')) + '</pre></div>';
      continue;
    }
    if (!t) { flush(); i++; continue; }
    if (t === '---' || t === '***') {
      flush();
      out += '<hr style="border:none;border-top:1px solid var(--surface0);margin:8px 0">';
      i++;
      continue;
    }
    var hd = _hdHeading(t);
    if (hd) {
      flush();
      var size = hd.depth === 1 ? 13 : (hd.depth === 2 ? 12 : 11.5);
      out += '<div style="font-weight:600;font-size:' + size + 'px;margin:6px 0 4px;color:' +
             (hd.depth <= 2 ? 'var(--text)' : 'var(--mauve)') + '">' + _hdInline(hd.body) + '</div>';
      i++;
      continue;
    }
    if (t.charAt(0) === '|') {
      flush();
      var rows = [];
      while (i < lines.length && lines[i].trim().charAt(0) === '|') {
        var r = lines[i].trim();
        if (!_hdIsTableRule(r)) { rows.push(_hdTableCells(r)); }
        i++;
      }
      out += _hdTable(rows);
      continue;
    }
    if (t.charAt(0) === '>') {
      flush();
      var quote = [];
      while (i < lines.length && lines[i].trim().charAt(0) === '>') { quote.push(lines[i].trim().slice(1).trim()); i++; }
      out += '<div style="border-left:2px solid var(--overlay0);padding-left:8px;color:var(--subtext1);margin:0 0 6px">' +
             quote.map(_hdInline).join('<br>') + '</div>';
      continue;
    }
    if (_hdListItem(lines[i])) {
      flush();
      var items = [];
      while (i < lines.length) {
        var raw = lines[i];
        var it = _hdListItem(raw);
        if (it) { items.push({ indent: it.indent, marker: it.marker, lines: [it.body] }); i++; continue; }
        var rt = raw.trim();
        // A blank line between items does not end the list; one before prose does.
        if (!rt) {
          if (i + 1 < lines.length && _hdListItem(lines[i + 1])) { i++; continue; }
          break;
        }
        // Indented text under an item is that item's continuation.
        if (raw.charAt(0) === ' ' && rt.slice(0, 3) !== '```') { items[items.length - 1].lines.push(rt); i++; continue; }
        break;
      }
      out += _hdList(items);
      continue;
    }
    para.push(t);
    i++;
  }
  flush();
  return out;
}

function _hdChatMessage(role, text) {
  var bg = role === 'user' ? '#89b4fa22' : '#a6e3a122';
  var accent = role === 'user' ? '#89b4fa' : '#a6e3a1';
  var label = role === 'user' ? 'question' : 'response';
  return '<div style="background:' + bg + ';border-left:2px solid ' + accent + ';color:var(--text);' +
         'border-radius:4px;padding:6px 9px 2px;word-break:break-word;font-size:11px;line-height:1.55">' +
         '<div style="font-size:8.5px;color:' + accent + ';margin-bottom:3px;text-transform:uppercase">' + label + '</div>' +
         _hdMarkdown(text) +
         '</div>';
}

function herderChatOpen(name) {
  var det = document.getElementById('hddet-' + name);
  var chat = document.getElementById('hdchat-' + name);
  if (det && det.open) {
    if (!_hdChat[name]) { _hdChat[name] = { cursor: 0 }; }
    herderUpdateChat(name, true);
  }
}

function herderUpdateChat(name, force) {
  var chat = document.getElementById('hdchat-' + name);
  if (!chat) { return; }

  _hdFetch('/api/herder-stream?name=' + encodeURIComponent(name))
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.error) { chat.textContent = d.error; return; }
      if (d.pending) { chat.innerHTML = '<span style="color:var(--overlay0);font-size:9px">Starting session…</span>'; return; }

      var evs = d.events || [];
      if (!evs.length) {
        chat.innerHTML = '<span style="color:var(--overlay0);font-size:9px">No messages yet. Send the first prompt.</span>';
        return;
      }

      var msgs = [];
      for (var i = 0; i < evs.length; i++) {
        var e = evs[i];
        if (e.t === 'text') { msgs.push({ role: e.role === 'user' ? 'user' : 'assistant', text: e.text }); }
      }

      var html = msgs.map(function(m) { return _hdChatMessage(m.role, m.text); }).join('');
      var atBottom = (chat.scrollHeight - chat.scrollTop - chat.clientHeight) < 40;
      chat.innerHTML = html || '<span style="color:var(--overlay0);font-size:9px">No messages yet.</span>';
      if (atBottom) { chat.scrollTop = chat.scrollHeight; }
    })
    .catch(function(e) { chat.textContent = String(e); });
}

// Enter sends, Shift+Enter is a newline — unless the completion list is open
// (Enter picks) or an IME is mid-composition (Enter commits the character).
function herderInputKeydown(e, name) {
  if (herderAcKeydown(e)) { return; }
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    herderSendReply(name);
  }
}

function herderSpawnKeydown(e) {
  if (herderAcKeydown(e)) { return; }
  var btn = document.getElementById('hd-spawn-btn');
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    if (btn && !btn.disabled) { herderSpawn(); }
  }
}

// `/skill` and `@path` completion: `/` at the start of any word completes a skill
// or command, `@` at the start of any word completes a repo path. Candidates come from /api/herder-complete for the
// repo the session runs in, so the list is what that session can invoke.
var _hdAc = { el: null, repo: '', start: 0, kind: '', items: [], idx: 0, seq: 0 };
var _hdCwd = {};

function _hdAcToken(el) {
  var pos = el.selectionStart;
  if (pos !== el.selectionEnd) { return null; }
  var v = el.value;
  var i = pos;
  while (i > 0 && ' \\n\\t'.indexOf(v.charAt(i - 1)) < 0) { i--; }
  var ch = v.charAt(i);
  var q = v.slice(i + 1, pos);
  // `/` opens skills at the start of any word, not only the message. A second
  // `/` in the token makes it a path (`/tmp/x`) — skill names never contain one.
  if (ch === '/') { return q.indexOf('/') < 0 ? { kind: 'skill', start: i, q: q } : null; }
  if (ch === '@') { return { kind: 'file', start: i, q: q }; }
  return null;
}

function _hdAcBox() {
  var b = document.getElementById('hd-ac');
  if (b) { return b; }
  b = document.createElement('div');
  b.id = 'hd-ac';
  b.style.cssText = 'position:fixed;z-index:9999;display:none;max-height:260px;overflow-y:auto;' +
    'background:var(--base);border:1px solid var(--surface1);border-radius:6px;' +
    'box-shadow:0 6px 20px #0008;font-size:11px;min-width:260px;max-width:560px';
  // mousedown, not click: preventing it keeps focus in the textarea, so its blur
  // handler does not close the list before the pick lands.
  b.addEventListener('mousedown', function(e) {
    e.preventDefault();
    var row = e.target.closest('[data-i]');
    if (row) { _hdAcPick(Number(row.getAttribute('data-i'))); }
  });
  document.body.appendChild(b);
  return b;
}

function herderAcClose() {
  _hdAc.seq++;
  _hdAc.el = null;
  _hdAc.items = [];
  var b = document.getElementById('hd-ac');
  if (b) { b.style.display = 'none'; }
}

function herderAcInput(el, repoPath) {
  var t = _hdAcToken(el);
  if (!t || !repoPath) { herderAcClose(); return; }
  var seq = ++_hdAc.seq;
  _hdAc.el = el;
  _hdAc.repo = repoPath;
  _hdAc.start = t.start;
  _hdAc.kind = t.kind;
  _hdFetch('/api/herder-complete?kind=' + t.kind + '&repo=' + encodeURIComponent(repoPath) +
           '&q=' + encodeURIComponent(t.q))
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (seq !== _hdAc.seq) { return; }   // a later keystroke superseded this one
      if (d.error || !(d.items || []).length) { herderAcClose(); return; }
      _hdAc.items = d.items;
      _hdAc.idx = 0;
      _hdAcRender();
    })
    .catch(function() { herderAcClose(); });
}

function _hdAcRender() {
  var el = _hdAc.el;
  if (!el || !document.body.contains(el)) { herderAcClose(); return; }
  var b = _hdAcBox();
  var sigil = _hdAc.kind === 'skill' ? '/' : '@';
  b.innerHTML = _hdAc.items.map(function(it, i) {
    return '<div data-i="' + i + '" style="padding:5px 9px;cursor:pointer;display:flex;gap:9px;' +
      'align-items:baseline;' + (i === _hdAc.idx ? 'background:var(--surface1)' : '') + '">' +
      '<span style="' + _HD_MONO + 'color:var(--mauve);white-space:nowrap">' + sigil + _hdEsc(it.name) + '</span>' +
      (it.description
        ? '<span style="color:var(--overlay1);font-size:10px;overflow:hidden;text-overflow:ellipsis;' +
          'white-space:nowrap">' + _hdEsc(it.description) + '</span>'
        : '') +
      '</div>';
  }).join('');
  b.style.display = 'block';
  // Below the box, or above it when the viewport has no room below.
  var r = el.getBoundingClientRect();
  var h = b.offsetHeight;
  b.style.left = r.left + 'px';
  b.style.top = ((r.bottom + 4 + h > window.innerHeight) ? Math.max(4, r.top - h - 4) : r.bottom + 4) + 'px';
  var cur = b.querySelector('[data-i="' + _hdAc.idx + '"]');
  if (cur) { cur.scrollIntoView({ block: 'nearest' }); }
}

function _hdAcPick(i) {
  var el = _hdAc.el;
  var it = _hdAc.items[i];
  if (!el || !it) { herderAcClose(); return; }
  var v = el.value;
  // Replace the whole token, including any part of it right of the caret.
  var end = el.selectionStart;
  while (end < v.length && ' \\n\\t'.indexOf(v.charAt(end)) < 0) { end++; }
  // A directory keeps the list open for the next level; a finished file or skill
  // gets the trailing space Claude Code inserts.
  var dir = _hdAc.kind === 'file' && it.name.charAt(it.name.length - 1) === '/';
  var ins = (_hdAc.kind === 'skill' ? '/' : '@') + it.name + (dir ? '' : ' ');
  el.value = v.slice(0, _hdAc.start) + ins + v.slice(end);
  var caret = _hdAc.start + ins.length;
  el.focus();
  el.setSelectionRange(caret, caret);
  if (dir) { herderAcInput(el, _hdAc.repo); } else { herderAcClose(); }
}

// True when the completion list consumed the key.
function herderAcKeydown(e) {
  var b = document.getElementById('hd-ac');
  if (!b || b.style.display === 'none' || e.target !== _hdAc.el || !_hdAc.items.length) { return false; }
  var n = _hdAc.items.length;
  if (e.key === 'ArrowDown') { _hdAc.idx = (_hdAc.idx + 1) % n; }
  else if (e.key === 'ArrowUp') { _hdAc.idx = (_hdAc.idx - 1 + n) % n; }
  else if ((e.key === 'Enter' && !e.shiftKey && !e.isComposing) || e.key === 'Tab') {
    e.preventDefault();
    _hdAcPick(_hdAc.idx);
    return true;
  }
  else if (e.key === 'Escape') { e.preventDefault(); herderAcClose(); return true; }
  else { return false; }
  e.preventDefault();
  _hdAcRender();
  return true;
}

// The input is cleared only once the server confirms the prompt landed. fetch()
// resolves on a 403/500 too, so the reply body's `ok` is what decides — otherwise
// a failed send silently throws away what was typed.
function herderSendReply(name) {
  var input = document.getElementById('hdinput-' + name);
  if (!input || input.disabled) { return; }
  var text = input.value.trim();
  if (!text) { return; }

  herderAcClose();
  _hdSendError(name, '');
  input.disabled = true;
  _hdFetch('/api/herder-prompt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name, prompt: text })
  })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      // Look the box up again: a roster rebuild may have replaced it mid-send.
      var cur = document.getElementById('hdinput-' + name) || input;
      cur.disabled = false;
      if (!d.ok) { _hdSendError(name, d.error || 'send failed'); cur.focus(); return; }
      cur.value = '';
      cur.focus();
      herderUpdateChat(name, true);
      setTimeout(function() { herderUpdateChat(name, true); }, 1000);
    })
    .catch(function(e) {
      var cur = document.getElementById('hdinput-' + name) || input;
      cur.disabled = false;
      _hdSendError(name, String(e));
    });
}

function _hdSendError(name, msg) {
  var el = document.getElementById('hderr-' + name);
  if (!el) { return; }
  el.textContent = msg ? 'not sent — ' + msg : '';
  el.style.display = msg ? 'block' : 'none';
}

// 📎 starts an `@` mention at the caret and opens the file list — the same path
// as typing `@`, so attaching a file has one implementation, not two.
function herderFileTag(name) {
  var input = document.getElementById('hdinput-' + name);
  if (!input || input.disabled) { return; }
  var v = input.value;
  var pos = input.selectionStart;
  var pre = (pos > 0 && ' \\n\\t'.indexOf(v.charAt(pos - 1)) < 0) ? ' @' : '@';
  input.value = v.slice(0, pos) + pre + v.slice(pos);
  input.focus();
  input.setSelectionRange(pos + pre.length, pos + pre.length);
  herderAcInput(input, _hdCwd[name]);
}

setInterval(function() {
  var el = document.querySelector('.nav-item.active');
  if (el && el.getAttribute('data-s') === 'herder') { herderRefresh(); }
}, 5000);

function runSkillCuratorPropose() {
  var btn = document.getElementById('sc-propose-btn');
  if (btn) { btn.disabled = true; btn.textContent = '🔍 Analyzing…'; }
  fetch('/api/skill-curator-propose', { method: 'POST' })
    .then(function() { setTimeout(function() { _scPollProposal(0); }, 5000); })
    .catch(function() { if (btn) { btn.disabled = false; btn.textContent = '🔍 Analyze & Propose'; } });
}

function runSkillCuratorApply() {
  var input = document.getElementById('sc-instruction');
  var instruction = input ? input.value : 'apply all';
  var btn = document.getElementById('sc-apply-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Applying…'; }
  fetch('/api/skill-curator-apply?instruction=' + encodeURIComponent(instruction), { method: 'POST' })
    .then(function() { if (btn) btn.textContent = '✓ Refresh to see results'; })
    .catch(function() { if (btn) { btn.disabled = false; btn.textContent = '✅ Apply Approved'; } });
}""" if companion else ""

    headroom_funs = f"""
var _hrRefreshTimer = null;
function loadHeadroom() {{
  var badge = document.getElementById('headroom-live-badge');
  fetch('/api/headroom-stats')
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      if (badge) {{
        badge.textContent = 'LIVE';
        badge.style.background = 'var(--green)';
      }}
      _hrApplyLive(d);
      if (!_hrRefreshTimer) {{
        _hrRefreshTimer = setInterval(function() {{
          fetch('/api/headroom-stats').then(function(r) {{ return r.json(); }})
            .then(_hrApplyLive).catch(function() {{ _hrSetOffline(); }});
        }}, 30000);
      }}
    }})
    .catch(function() {{ _hrSetOffline(); }});
}}
function _hrSetOffline() {{
  var badge = document.getElementById('headroom-live-badge');
  if (badge) {{ badge.textContent = 'OFFLINE'; badge.style.background = 'var(--overlay0)'; }}
  if (_hrRefreshTimer) {{ clearInterval(_hrRefreshTimer); _hrRefreshTimer = null; }}
}}
function _hrApplyLive(d) {{
  if (!d || !d.display_session) return;
  var s = d.display_session;
  var el = document.getElementById('hr-live-savings');
  if (el) el.textContent = (s.savings_percent || 0).toFixed(1) + '%  '
    + (s.tokens_saved || 0).toLocaleString() + ' tokens  $'
    + (s.compression_savings_usd || 0).toFixed(3) + ' saved';
}}
"""

    # Model cost JS — embed pricing + session data as globals for mcFilter/mcWhatIf
    latest_pricing   = pricing_snapshots[-1]["models"] if pricing_snapshots else {}
    mc_sessions_data = []
    mc_actual_total  = 0.0
    for s in usage_sessions:
        if s["date"] == "unknown":
            continue
        hist_p = get_pricing_at(pricing_snapshots, s["date"])
        cost   = compute_session_cost(s, hist_p)
        if cost is not None:
            mc_sessions_data.append({
                "m": f"anthropic/{s['model']}",
                "i": s["input"],
                "o": s["output"],
                "r": s["cache_read"],
                "w": s["cache_create"],
            })
            mc_actual_total += cost

    # Build cross-provider pricing dict: only featured providers, only priced models,
    # with prov + name metadata for JS-side filtering/display
    featured_set = set(FEATURED_PROVIDERS)
    mc_pricing_dict: dict = {}
    for k, v in latest_pricing.items():
        prov = k.split("/", 1)[0]
        if prov not in featured_set:
            continue
        if v["input"] == 0 and v["output"] == 0:
            continue   # skip free/unknown-priced models
        mid  = k.split("/", 1)[-1]
        mc_pricing_dict[k] = {
            "prov":        prov,
            "name":        mid,
            "input":       v["input"],
            "output":      v["output"],
            "cache_read":  v["cache_read"],
            "cache_write": v["cache_write"],
        }
    mc_pricing_js  = json.dumps(mc_pricing_dict).replace("</", "<\\/")
    mc_sessions_js = json.dumps(mc_sessions_data).replace("</", "<\\/")

    # Provider display map for JS
    mc_prov_display_js = json.dumps(PROVIDER_DISPLAY).replace("</", "<\\/")

    mc_funs = f"""
var MC_PRICING = {mc_pricing_js};
var MC_SESSIONS = {mc_sessions_js};
var MC_ACTUAL = {mc_actual_total:.6f};
var MC_PROV_DISPLAY = {mc_prov_display_js};
function mcFilter(proj) {{
  var rows = document.querySelectorAll('#mc-table tbody tr');
  rows.forEach(function(r) {{
    r.style.display = (!proj || r.dataset.project === proj) ? '' : 'none';
  }});
}}
function mcProviderChange(prov) {{
  var modelSel = document.getElementById('mc-whatif-model');
  var res      = document.getElementById('mc-whatif-result');
  if (res) res.innerHTML = '';
  if (!modelSel) return;
  var opts = '<option value="">Select model…</option>';
  var keys = Object.keys(MC_PRICING).filter(function(k) {{
    return !prov || MC_PRICING[k].prov === prov;
  }});
  keys.sort(function(a, b) {{
    var pa = MC_PRICING[a], pb = MC_PRICING[b];
    return (pb.input + pb.output) - (pa.input + pa.output);
  }});
  keys.forEach(function(k) {{
    var m = MC_PRICING[k];
    opts += '<option value="' + k + '">' + m.name
          + ' — $' + m.input + '/$' + m.output + '/M</option>';
  }});
  modelSel.innerHTML = opts;
}}
function mcWhatIf(modelKey) {{
  var res = document.getElementById('mc-whatif-result');
  if (!res) return;
  if (!modelKey) {{ res.innerHTML = ''; return; }}
  var p = MC_PRICING[modelKey];
  if (!p) {{ res.textContent = 'No pricing data for this model.'; return; }}
  var projected = 0;
  MC_SESSIONS.forEach(function(s) {{
    projected += s.i * p.input / 1e6 + s.o * p.output / 1e6
               + s.r * p.cache_read / 1e6 + s.w * p.cache_write / 1e6;
  }});
  var delta = projected - MC_ACTUAL;
  var sign  = delta >= 0 ? '+' : '';
  var col   = delta > 0 ? '#f38ba8' : '#a6e3a1';
  res.innerHTML = 'Projected: <strong style="color:var(--text)">$' + projected.toFixed(2)
    + '</strong> vs actual <strong style="color:var(--text)">$' + MC_ACTUAL.toFixed(2) + '</strong> '
    + '<span style="color:' + col + ';font-weight:600">(' + sign
    + '$' + Math.abs(delta).toFixed(2) + ')</span>';
}}
"""

    js  = (JS_TEMPLATE
           .replace("__SECTIONS_JSON__", sj)
           .replace("__INIT_REPO__", ir)
           .replace("__GN_WEB_UI__", GN_WEB_UI)
           .replace("__GN_PROBE_URL__", GN_PROBE_URL)
           .replace("__GN_SERVE_FUNS__", gn_funs)
           .replace("__WORKSHOP_FUNS__", workshop_funs)
           .replace("__HEADROOM_FUNS__", headroom_funs)
           .replace("__MC_FUNS__", mc_funs)
           # Static output is written to a file and has no server to authorize
           # against, so it must never carry a live token.
           .replace("__HERDER_TOKEN__", HERDER_TOKEN if companion else ""))
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>SDD Harness Dashboard</title>
  <style>{CSS}</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="sidebar-header"><span>⬡</span> SDD Harness</div>
    <div class="repo-wrap">
      <div class="label" style="margin-bottom:5px">Repository</div>
      <select onchange="switchRepo(this.value)">{repo_opts}</select>
    </div>
    <div class="nav-group-label">Sections</div>
    <nav>{nav}</nav>
    <div class="sidebar-footer">
      <div style="color:var(--overlay0);font-size:10px">Generated {ts}</div>
    </div>
  </aside>
  <main class="content"><div id="panel"></div></main>
</div>
<script>{js}</script>
</body>
</html>"""

# ── Companion Server ──────────────────────────────────────────────────────────

def _wsl_to_windows(path: str) -> str | None:
    """Convert /mnt/c/foo → C:\\foo. Returns None if not a /mnt/<drive>/ path."""
    prefix = "/mnt/"
    if not path.startswith(prefix) or len(path) < len(prefix) + 1:
        return None
    drive = path[len(prefix)]
    rest  = path[len(prefix) + 1:]
    if not drive.isascii() or not drive.isalpha():
        return None
    if rest and not rest.startswith("/"):
        return None
    return f"{drive.upper()}:{rest.replace('/', chr(92))}"


def _start_gitnexus_serve(repo_path: str) -> None:
    """Start gitnexus serve for repo_path.

    - Windows filesystem paths (/mnt/<drive>/…) → launch via powershell.exe
      so the database reads happen at native NTFS speed.
    - WSL-native paths → launch directly.
    """
    win_path = _wsl_to_windows(repo_path)
    if win_path:
        # Kill any WSL gitnexus serve first (port 4747 is shared in WSL2)
        subprocess.Popen(
            ["pkill", "-f", "gitnexus serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        import time as _time; _time.sleep(0.8)
        # Launch via PowerShell so it runs as a Windows-native process
        ps_cmd = f"Set-Location '{win_path}'; gitnexus serve"
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-WindowStyle", "Hidden", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        except FileNotFoundError:
            pass  # fall through to WSL attempt
    # WSL-native path (or PowerShell unavailable) — launch directly
    for cmd in [["gitnexus", "serve"], ["npx", "gitnexus", "serve"]]:
        try:
            subprocess.Popen(
                cmd, cwd=repo_path,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        except FileNotFoundError:
            continue


def _start_workshop() -> None:
    """Start raindrop workshop (port 5899). Workshop runs globally, not per-repo."""
    for cmd in [["raindrop", "workshop"], ["raindrop", "workshop", "--port", "5899"]]:
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        except FileNotFoundError:
            continue


def _run_workshop_eval(repo_path: str) -> None:
    """Spawn a claude --print session to run ~/.claude/skill-library/raindrop-eval-loop/SKILL.md for repo_path."""
    repo_name = Path(repo_path).name
    prompt = (
        f"Read ~/.claude/skill-library/raindrop-eval-loop/SKILL.md and follow it to run the self-healing eval loop "
        f"for the repository '{repo_name}' at {repo_path}. "
        f"Workshop is running at http://localhost:5899."
    )
    env = {**os.environ, "RAINDROP_LOCAL_DEBUGGER": "http://localhost:5899/v1/"}
    for cmd in [["claude", "--print", prompt], ["claude-code", "--print", prompt]]:
        try:
            subprocess.Popen(
                cmd, cwd=repo_path,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=env,
            )
            return
        except FileNotFoundError:
            continue


def _run_skill_curator_propose() -> None:
    """Spawn a headless claude session to run skill-curator Phases 1-4 and write a
    terse proposal to SKILL_PROPOSAL_PATH instead of printing it to chat."""
    prompt = (
        "Use the skill-curator skill's Phase 1 (Load & Orient) through Phase 4 "
        "(Propose Actions) logic to analyze reports/skill-curation-report.md. "
        "Cap each proposed item to 1-2 sentences of rationale — shorter than the "
        "skill's default Phase 4 format. Do NOT execute anything and do NOT print "
        f"the proposal to chat — instead write the numbered proposal as markdown to "
        f"{SKILL_PROPOSAL_PATH}, creating parent directories if needed."
    )
    env = {**os.environ, "SDD_HEADLESS": "1"}
    log_path = HARNESS_DIR / "logs" / "skill-curator-propose.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logfile = open(log_path, "w")
    logfile.write(
        f"[{datetime.now(timezone.utc).isoformat()}] skill-curator propose run\n"
        f"prompt: {prompt}\n\n"
    )
    logfile.flush()
    subprocess.Popen(
        ["claude", "--print", "--permission-mode", "bypassPermissions", prompt],
        cwd=str(HARNESS_DIR),
        stdout=logfile, stderr=subprocess.STDOUT,
        env=env,
    )


def _backup_skills_dir() -> str:
    """Tar ~/.claude/skills/ before an apply step touches anything. Returns the
    backup path (relative label, not used for anything but the caller's own note)."""
    skills_dir = Path.home() / ".claude" / "skills"
    backup_dir = HARNESS_DIR / ".dashboard" / "skill-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"skills-{ts}.tar.gz"
    if skills_dir.is_dir():
        subprocess.run(
            ["tar", "-czf", str(backup_path), "-C", str(skills_dir.parent), skills_dir.name],
            check=False,
        )
    return str(backup_path)


def _run_skill_curator_apply(instruction: str) -> None:
    """Back up ~/.claude/skills/ then spawn a headless claude session to execute the
    approved subset of the pending proposal per skill-curator Phases 5-6."""
    backup_path = _backup_skills_dir()
    proposal = SKILL_PROPOSAL_PATH.read_text() if SKILL_PROPOSAL_PATH.exists() else ""
    prompt = (
        f"A backup of ~/.claude/skills/ was just taken at {backup_path}. "
        "Read the pending proposal below and the user's approval instruction, then "
        "use the skill-curator skill's Phase 5 (Execute Approved Changes) and "
        "Phase 6 (Update Source Log) rules to execute only the approved subset and "
        "append the curation log entry to reports/skill-curation-report.md. "
        f"Finally, delete {SKILL_PROPOSAL_PATH} so it can't be re-applied.\n\n"
        f"## Pending Proposal\n{proposal}\n\n"
        f"## User Approval Instruction\n{instruction}"
    )
    env = {**os.environ, "SDD_HEADLESS": "1"}
    log_path = HARNESS_DIR / "logs" / "skill-curator-apply.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logfile = open(log_path, "w")
    logfile.write(
        f"[{datetime.now(timezone.utc).isoformat()}] skill-curator apply run\n"
        f"backup: {backup_path}\n"
        f"instruction: {instruction}\n\n"
    )
    logfile.flush()
    subprocess.Popen(
        ["claude", "--print", "--permission-mode", "bypassPermissions", prompt],
        cwd=str(HARNESS_DIR),
        stdout=logfile, stderr=subprocess.STDOUT,
        env=env,
    )


class _DashboardHandler(BaseHTTPRequestHandler):
    _html: bytes = b""

    WS_PORT = 5899  # raindrop workshop default port

    # ── Herder request guard ─────────────────────────────────────────────────
    def _herder_authorized(self) -> bool:
        """Reject cross-origin and untokened calls to the herder API.

        Two independent checks, because either alone is bypassable:
          - the per-process token, which only a page served by this process has;
          - an Origin allowlist, so a page that somehow learned the token still
            cannot drive it from another site.
        A same-origin fetch from the dashboard page sends no Origin header on
        same-origin GET/POST in some browsers, so an ABSENT Origin is allowed and
        a PRESENT-but-foreign one is not.
        """
        if self.headers.get("X-Herder-Token") != HERDER_TOKEN:
            return False
        origin = self.headers.get("Origin")
        allowed = (
            f"http://localhost:{COMPANION_PORT}",
            f"http://127.0.0.1:{COMPANION_PORT}",
        )
        return not origin or origin in allowed

    def _send_json(self, obj, code=200):
        """JSON reply with no CORS header.

        Deliberately omits Access-Control-Allow-Origin: these endpoints act on the
        machine, so no other site should be able to read their replies.
        """
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _herder_guarded(self):
        """Return the herder module if the request may proceed, else None.

        Returns the module rather than a bool so callers get a non-Optional
        reference — a plain `if not guard(): return` leaves a type checker unable
        to prove `herder` is not None at the call site below.
        Replies 503 (not installed) or 403 (unauthorized) before returning None.
        """
        if herder is None:
            self._send_json({"error": "herder-unavailable",
                             "detail": HERDER_IMPORT_ERROR}, 503)
            return None
        if not self._herder_authorized():
            self._send_json({"error": "forbidden"}, 403)
            return None
        return herder

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 1_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(self._html)))
            self.end_headers()
            self.wfile.write(self._html)
        elif parsed.path == "/api/headroom-stats":
            try:
                body = HEADROOM_SAVINGS.read_bytes() if HEADROOM_SAVINGS.exists() else b'{}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"read-failed"}')
        elif parsed.path == "/api/skill-curator-proposal":
            content, age = read_skill_proposal()
            body = json.dumps({"content": content, "age": age}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/workshop-runs":
            qs         = parse_qs(parsed.query)
            event_name = qs.get("event_name", [""])[0]
            try:
                req = UrlRequest(f"http://localhost:{self.WS_PORT}/api/runs")
                with urlopen(req, timeout=3) as resp:
                    all_runs = json.loads(resp.read())
                if event_name:
                    all_runs = [r for r in all_runs if r.get("event_name") == event_name]
                body = json.dumps(all_runs).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"workshop-offline"}')
        elif parsed.path == "/api/herder-status":
            hd = self._herder_guarded()
            if hd is None:
                return
            try:
                self._send_json(hd.status())
            except Exception as exc:                      # noqa: BLE001
                self._send_json({"error": str(exc)}, 500)
        elif parsed.path == "/api/herder-list":
            hd = self._herder_guarded()
            if hd is None:
                return
            try:
                self._send_json({"agents": hd.list_agents()})
            except Exception as exc:                      # noqa: BLE001
                self._send_json({"error": str(exc)}, 500)
        elif parsed.path == "/api/herder-options":
            hd = self._herder_guarded()
            if hd is None:
                return
            kind = parse_qs(parsed.query).get("kind", ["claude"])[0]
            try:
                self._send_json(hd.agent_options(kind))
            except Exception as exc:                      # noqa: BLE001
                self._send_json({"error": str(exc)}, 500)
        elif parsed.path == "/api/herder-stream":
            hd = self._herder_guarded()
            if hd is None:
                return
            qs = parse_qs(parsed.query)
            name = qs.get("name", [""])[0]
            try:
                after = int(qs.get("after", ["0"])[0])
            except ValueError:
                after = 0
            try:
                data = hd.agent_stream(name, after=after)
                if data is None:
                    self._send_json({"name": name, "events": [], "cursor": after,
                                     "pending": True})
                else:
                    self._send_json({"name": name, **data})
            except Exception as exc:                      # noqa: BLE001
                self._send_json({"error": str(exc)}, 500)
        elif parsed.path == "/api/herder-read":
            hd = self._herder_guarded()
            if hd is None:
                return
            name = parse_qs(parsed.query).get("name", [""])[0]
            try:
                self._send_json({"name": name, "output": hd.read_agent(name)})
            except Exception as exc:                      # noqa: BLE001
                self._send_json({"error": str(exc)}, 500)
        elif parsed.path == "/api/herder-complete":
            # Guarded like the rest: a file listing of a repo is not something
            # another site's page should be able to read.
            hd = self._herder_guarded()
            if hd is None:
                return
            qs = parse_qs(parsed.query)
            try:
                self._send_json(hd.complete(qs.get("repo", [""])[0],
                                            qs.get("kind", [""])[0],
                                            qs.get("q", [""])[0]))
            except Exception as exc:                      # noqa: BLE001
                self._send_json({"error": str(exc)}, 500)
        elif parsed.path.startswith("/workshop/") or parsed.path == "/workshop":
            self._proxy_workshop(parsed)
        else:
            self.send_error(404)

    def _proxy_workshop(self, parsed):
        """Proxy to raindrop workshop UI at port 5899."""
        ws_path = parsed.path[9:] or "/"  # strip /workshop prefix
        target_url = f"http://localhost:{self.WS_PORT}{ws_path}"
        if parsed.query:
            target_url += f"?{parsed.query}"

        try:
            req = UrlRequest(target_url, headers={"Accept": self.headers.get("Accept", "*/*")})
            with urlopen(req, timeout=8) as resp:
                content      = resp.read()
                content_type = resp.headers.get("Content-Type", "application/octet-stream")
                status       = resp.status
        except URLError:
            self.send_error(502, "Raindrop Workshop not reachable at port 5899")
            return
        except Exception as e:
            self.send_error(502, str(e))
            return

        if b"text/html" in content_type.encode() and b"</head>" in content:
            ws_origin = f"http://localhost:{self.WS_PORT}"
            content = content.replace(b'href="/', f'href="{ws_origin}/'.encode())
            content = content.replace(b'src="/',  f'src="{ws_origin}/'.encode())

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/gitnexus-serve":
            qs = parse_qs(parsed.query)
            repo_path = qs.get("repo", [""])[0]
            if repo_path and Path(repo_path).is_dir():
                _start_gitnexus_serve(repo_path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif parsed.path == "/api/workshop-start":
            _start_workshop()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif parsed.path == "/api/workshop-eval":
            qs = parse_qs(parsed.query)
            repo_path = qs.get("repo", [""])[0]
            if repo_path and Path(repo_path).is_dir():
                _run_workshop_eval(repo_path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif parsed.path == "/api/skill-curator-propose":
            _run_skill_curator_propose()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif parsed.path == "/api/skill-curator-apply":
            qs = parse_qs(parsed.query)
            instruction = qs.get("instruction", ["apply all"])[0] or "apply all"
            _run_skill_curator_apply(instruction)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif parsed.path == "/api/herder-spawn":
            hd = self._herder_guarded()
            if hd is None:
                return
            body = self._read_json_body()
            try:
                result = hd.spawn(
                    body.get("repo", ""),
                    body.get("label", "agent"),
                    kind=body.get("kind", "claude"),
                    prompt=body.get("prompt", ""),
                    permission_mode=body.get("permission_mode", "acceptEdits"),
                    model=body.get("model", ""),
                )
                self._send_json({"ok": True, "agent": result})
            except Exception as exc:                      # noqa: BLE001
                self._send_json({"ok": False, "error": str(exc),
                                 "blocked_on": getattr(exc, "screen", "")}, 500)
        elif parsed.path == "/api/herder-stop":
            hd = self._herder_guarded()
            if hd is None:
                return
            ws = parse_qs(parsed.query).get("workspace", [""])[0]
            try:
                hd.stop(ws)
                self._send_json({"ok": True})
            except Exception as exc:                      # noqa: BLE001
                self._send_json({"ok": False, "error": str(exc)}, 500)
        elif parsed.path == "/api/herder-prompt":
            hd = self._herder_guarded()
            if hd is None:
                return
            body = self._read_json_body()
            try:
                hd.prompt_agent(body.get("name", ""), body.get("prompt", ""))
                self._send_json({"ok": True})
            except Exception as exc:                      # noqa: BLE001
                self._send_json({"ok": False, "error": str(exc)}, 500)
        else:
            self.send_error(404)

    def log_message(self, *_args):
        """Suppress default request logging."""

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


def serve_companion(html_content: str, port: int, open_browser_fn):
    _DashboardHandler._html = html_content.encode("utf-8")
    httpd = HTTPServer(("127.0.0.1", port), _DashboardHandler)
    url = f"http://localhost:{port}"
    print(f"   Dashboard: {url}  (Ctrl+C to stop)")
    open_browser_fn(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n   Stopped.")


# ── Browser Open ──────────────────────────────────────────────────────────────

def open_browser(url: str):
    try:
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                for cmd in [["wslview", url], ["explorer.exe", url]]:
                    try:
                        subprocess.Popen(cmd,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
                    except FileNotFoundError:
                        continue
    except Exception:
        pass
    for cmd in [["xdg-open", url], ["sensible-browser", url]]:
        try:
            subprocess.Popen(cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue
    print(f"   Open manually: {url}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # The module docstring has advertised --port since this file was written, but
    # argparse never defined it, so passing it was an error and the port was
    # effectively hardcoded. It is a real flag now. The herder Origin allowlist
    # reads this same module global, so the two cannot drift apart.
    global COMPANION_PORT

    ap = argparse.ArgumentParser(description="SDD Harness Dashboard")
    ap.add_argument("--repo",    help="Pre-select a repo path or name")
    ap.add_argument("--no-open", action="store_true",
                    help="Start server without opening browser")
    ap.add_argument("--static",  action="store_true",
                    help="Write static file to .dashboard/index.html instead of starting server")
    ap.add_argument("--port", type=int, default=COMPANION_PORT,
                    help=f"HTTP port for the local server (default {COMPANION_PORT})")
    args = ap.parse_args()
    COMPANION_PORT = args.port

    print("⬡  SDD Harness Dashboard")
    print(f"   Harness: {HARNESS_DIR}")

    repos = discover_repos()
    if not repos:
        print(f"   No repos found in {PROJECTS_FILE}")
        sys.exit(1)

    print(f"   Repos:   {', '.join(r.name for r in repos)}")

    initial_idx = 0
    if args.repo:
        for i, r in enumerate(repos):
            if str(r) == args.repo or r.name == args.repo:
                initial_idx = i
                break

    print("   Collecting data...", end="", flush=True)

    repos_data = []
    for repo in repos:
        repos_data.append({
            "path":             str(repo),
            "trust_scores":     parse_trust_scores(repo),
            "observations":     parse_observations(repo),
            "metrics":          parse_metrics(repo),
            "hooks":            list_hooks(repo),
            "last_routine_run": read_last_routine_run(repo),
            "gitnexus":         gitnexus_stats(repo),
            "workshop":         workshop_stats(repo),
            "memory_changes":   git_log_memory(repo),
            "memory_cards":     read_memory_file_cards(repo),
            "session_history":  parse_session_history(repo),
            "debt_markers":     count_debt_markers(repo),
        })

    skill_content, skill_age = read_skill_report()
    harness_data = {
        "scheduled_tasks":      parse_scheduled_tasks(),
        "scheduler":            _detect_os_scheduler(),
        "orchestrator_runs":    parse_orchestrator_log(),
        "skill_report_content": skill_content,
        "skill_report_age":     skill_age,
        "harness_memory":       git_log_harness_memory(),
    }

    pricing_snapshots = load_or_refresh_pricing_history()
    usage_sessions    = gather_usage_data()

    print(" done.")
    print("   Rendering...", end="", flush=True)

    companion = not args.static
    html_content = build_html(
        repos_data, harness_data, usage_sessions, pricing_snapshots,
        initial_idx, companion=companion,
    )
    print(" done.")

    if args.static:
        DASHBOARD_DIR.mkdir(exist_ok=True)
        OUTPUT_FILE.write_text(html_content, encoding="utf-8")
        size_kb = OUTPUT_FILE.stat().st_size // 1024
        print(f"   Output:  {OUTPUT_FILE} ({size_kb} KB)")
        if not args.no_open:
            open_browser(OUTPUT_FILE.as_uri())
    else:
        serve_companion(
            html_content,
            COMPANION_PORT,
            open_browser_fn=(lambda url: None) if args.no_open else open_browser,
        )
        print("   Opening in browser...")

if __name__ == "__main__":
    main()
