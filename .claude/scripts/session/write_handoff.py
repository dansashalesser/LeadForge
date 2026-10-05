#!/usr/bin/env python3
"""Deterministic session handoff writer for the SDD harness.

Fires from PreCompact, PreToolUse(Agent), and Stop (cache-cost trigger) hooks
— never invoked manually.
Parses the live transcript (no LLM call: this runs far more often than the
daily signal-detection scripts, so it stays fast and dependency-free) and
writes a structured brief to .claude/memory/handoff/latest.md so the next
session, or a spawned subagent, can pick up state without the human
re-explaining it.

Transcript resolution mirrors detect_reexplanation.py's --auto-transcript
fallback, but prefers the transcript_path Claude Code passes on hook stdin.

Exit codes: 0 always (best-effort; a broken handoff write must never block
the triggering tool call or compaction).
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOL_PATHS = {"Read", "Write", "Edit", "MultiEdit"}
MAX_TOUCHED = 25
MAX_TEXT_CHARS = 1200


def find_latest_transcript() -> Path | None:
    base = Path.home() / ".claude" / "projects"
    if not base.is_dir():
        return None
    cwd = Path.cwd().resolve()
    encoded = "-" + str(cwd).replace("/", "-")
    project_dir = base / encoded
    if not project_dir.is_dir():
        candidates = [p for p in base.iterdir() if p.is_dir()]
        if not candidates:
            return None
        project_dir = max(candidates, key=lambda p: p.stat().st_mtime)
    transcripts = list(project_dir.glob("*.jsonl"))
    if not transcripts:
        return None
    return max(transcripts, key=lambda p: p.stat().st_mtime)


def resolve_transcript(explicit: Path | None) -> Path | None:
    if explicit and explicit.is_file():
        return explicit
    stdin_path = _transcript_path_from_stdin()
    if stdin_path and stdin_path.is_file():
        return stdin_path
    return find_latest_transcript()


def _transcript_path_from_stdin() -> Path | None:
    if sys.stdin.isatty():
        return None
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return None
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    path = payload.get("transcript_path")
    return Path(path) if path else None


def _iter_records(path: Path):
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def build_brief(path: Path, trigger: str) -> str:
    last_user_text = ""
    last_assistant_text = ""
    touched: list[str] = []
    last_todos: list[dict] | None = None
    git_branch = ""
    cwd = ""

    for rec in _iter_records(path):
        git_branch = rec.get("gitBranch") or git_branch
        cwd = rec.get("cwd") or cwd
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and role == "user":
                last_user_text = block.get("text", "") or last_user_text
            elif btype == "text" and role == "assistant":
                last_assistant_text = block.get("text", "") or last_assistant_text
            elif btype == "tool_use":
                name = block.get("name", "")
                tool_input = block.get("input") or {}
                if name in TOOL_PATHS:
                    p = tool_input.get("file_path") or tool_input.get("path")
                    if p and p not in touched:
                        touched.append(p)
                elif name == "Bash":
                    cmd = tool_input.get("command", "")
                    label = f"$ {cmd}"[:120]
                    if label not in touched:
                        touched.append(label)
                elif name == "TodoWrite":
                    todos = tool_input.get("todos")
                    if isinstance(todos, list):
                        last_todos = todos

    touched = touched[-MAX_TOUCHED:]
    last_user_text = last_user_text.strip()[:MAX_TEXT_CHARS]
    last_assistant_text = last_assistant_text.strip()[:MAX_TEXT_CHARS]

    lines = [
        "# Session Handoff",
        "",
        f"- Written: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Trigger: {trigger}",
        f"- Source transcript: {path}",
        f"- cwd: {cwd or 'unknown'}",
        f"- git branch: {git_branch or 'unknown'}",
        "",
        "## Last user message",
        last_user_text or "_(none captured)_",
        "",
        "## Last assistant text",
        last_assistant_text or "_(none captured)_",
        "",
        "## In-flight todos",
    ]
    if last_todos:
        for t in last_todos:
            mark = {"completed": "x", "in_progress": "~"}.get(t.get("status"), " ")
            lines.append(f"- [{mark}] {t.get('content', '')}")
    else:
        lines.append("_(none captured)_")

    lines += ["", "## Files / commands touched (most recent last)"]
    if touched:
        lines += [f"- {t}" for t in touched]
    else:
        lines.append("_(none captured)_")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--trigger",
        choices=["precompact", "agent-spawn", "cache-cost"],
        required=True,
    )
    ap.add_argument("--transcript-path", type=Path, default=None)
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(".claude/memory/handoff/latest.md"),
    )
    args = ap.parse_args()

    try:
        transcript = resolve_transcript(args.transcript_path)
        if transcript is None:
            return 0
        brief = build_brief(transcript, args.trigger)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(brief, encoding="utf-8")
    except Exception as e:  # best-effort — never block the caller
        print(f"WARN: handoff write failed ({e})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
