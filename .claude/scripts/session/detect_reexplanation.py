#!/usr/bin/env python3
"""Session signal detector for the SDD harness.

Uses Claude Haiku, invoked through the `claude` CLI so it runs on the user's
Claude subscription rather than a separately-billed API key, to identify two
signal types in a session transcript:
  DRAIN  — user had to re-explain context the AI should have remembered,
            or expressed frustration at a repeated mistake
  CHARGE — user clearly approved of the AI's output

LLM-based: catches implicit friction and satisfaction that regex misses.
("that works but..." is not a charge; "you're doing it again" is a drain.)

Inputs (one of):
    --file PATH            Scan a plain-text file
    --stdin                Scan stdin
    --auto-transcript      Find most recent Claude Code session transcript
                           for the current working directory

Output filter (--mode):
    drain   (default) — emit drain signals only
    charge            — emit charge signals only
    all               — emit both

Output format (--emit):
    json         JSON array of hits (default)
    observation  Single observation line for observations.md

Exit codes: 0 = ran (regardless of hits); 2 = usage error; 3 = no input found;
4 = detector could not run (emits a `[detector-down]` observation, never a silent zero).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

MODEL = "claude-haiku-4-5-20251001"
CLI_TIMEOUT_SECONDS = 120

DETECTION_PROMPT = """\
You are a session quality analyzer for a single-developer AI coding harness.

Analyze the user messages below and identify two signal types.

**DRAIN signals** — the user had to re-explain something the AI should already \
have known, OR expressed frustration at a repeated mistake. Catch both \
explicit ("I already told you", "stop doing that") and implicit signals \
("you're still doing that thing I asked you to stop", "this is wrong again", \
"no, that's not what I meant", "I keep having to explain this").

**CHARGE signals** — the user gave unambiguous approval of the AI's output. \
Clear satisfaction only — not ambiguous acknowledgment. Strong signals: \
"perfect", "exactly what I needed", "great work", "that works!", "yes exactly". \
Do NOT count: standalone "ok" / "sure" / "yes" / "got it", anything with a \
"but" immediately following it, or phrasing that implies partial approval.

Respond ONLY with valid JSON, no prose before or after:
{
  "charges": [
    {"context": "<verbatim excerpt, max 40 words>", "reason": "<one phrase>"}
  ],
  "drains": [
    {"context": "<verbatim excerpt, max 40 words>", "reason": "<one phrase>"}
  ]
}

USER MESSAGES:
---
{text}
---"""


def _parse_llm_response(raw: str) -> tuple[list[dict], list[dict]]:
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        return [], []
    data = json.loads(raw[start:end])

    def _normalize(items: list) -> list[dict]:
        return [
            {
                "phrase": h.get("context", ""),
                "context": h.get("context", ""),
                "suggested_memory_topic": h.get("reason", ""),
            }
            for h in (items or [])
            if isinstance(h, dict)
        ]

    return _normalize(data.get("charges", [])), _normalize(data.get("drains", []))


class DetectorUnavailable(RuntimeError):
    """The detector could not run. Callers must surface this, never treat it as zero hits."""


def _call_llm(text: str) -> tuple[list[dict], list[dict]]:
    """Classify the transcript with headless Claude Code.

    Runs through the `claude` CLI rather than the anthropic SDK on purpose: the CLI
    authenticates with the user's Claude subscription, while the SDK requires an
    ANTHROPIC_API_KEY that bills separately per token. With no key set, the SDK path
    raised on every session and the caller swallowed it, so "0 memory gaps" meant
    "detector dead" for as long as it was deployed.

    SDD_HEADLESS=1 marks the child session so the stop hook does not launch another
    detector when it ends — that is the recursion this used to avoid by not spawning
    at all.
    """
    prompt = DETECTION_PROMPT.replace("{text}", text[:6000])
    env = {**os.environ, "SDD_HEADLESS": "1"}

    try:
        proc = subprocess.run(
            ["claude", "--print", "--model", MODEL, "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except FileNotFoundError as e:
        raise DetectorUnavailable("`claude` CLI not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise DetectorUnavailable(f"`claude --print` timed out after {CLI_TIMEOUT_SECONDS}s") from e

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        raise DetectorUnavailable(f"`claude --print` exited {proc.returncode}: {detail}")

    raw = proc.stdout.strip()
    if not raw:
        raise DetectorUnavailable("`claude --print` returned empty output")

    try:
        return _parse_llm_response(raw)
    except (json.JSONDecodeError, KeyError) as e:
        raise DetectorUnavailable(f"could not parse classifier output: {e}") from e


def find_latest_transcript() -> Path | None:
    """Find the most recent Claude Code session transcript for cwd."""
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


def read_user_turns_from_jsonl(path: Path) -> str:
    """Extract user message text from a Claude Code JSONL transcript."""
    chunks: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = rec.get("role") or rec.get("type")
        if role != "user":
            continue
        content = rec.get("content") or rec.get("message", {}).get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(block.get("text", ""))
    return "\n".join(chunks)


def _load_text(args) -> tuple[str | None, int]:
    if args.stdin:
        return sys.stdin.read(), 0
    if args.file:
        if not args.file.is_file():
            print(f"ERROR: file not found: {args.file}", file=sys.stderr)
            return None, 3
        return args.file.read_text(encoding="utf-8", errors="replace"), 0
    path = find_latest_transcript()
    if path is None:
        return None, 0
    return read_user_turns_from_jsonl(path), 0


_MODE_META = {
    "drain":  ("memory-gap",      "re-explanation hit(s)"),
    "charge": ("session-charge",  "approval signal(s)"),
    "all":    ("session-signal",  "signal(s)"),
}


def _select_hits(mode: str, charges: list, drains: list) -> list:
    if mode == "drain":
        return drains
    if mode == "charge":
        return charges
    return charges + drains


def _emit(hits: list, mode: str, emit: str) -> None:
    if emit == "observation":
        if not hits:
            return
        obs_tag, hit_label = _MODE_META[mode]
        topics = ", ".join(dict.fromkeys(h["suggested_memory_topic"] for h in hits))
        if len(topics) > 80:
            topics = topics[:77] + "..."
        print(
            f"- {date.today().isoformat()} [{obs_tag}]: "
            f"{len(hits)} {hit_label} — {topics}"
        )
    else:
        print(json.dumps(hits, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", type=Path, help="Scan a plain-text file")
    src.add_argument("--stdin", action="store_true", help="Scan stdin")
    src.add_argument(
        "--auto-transcript",
        action="store_true",
        help="Scan most recent Claude Code transcript for cwd",
    )
    ap.add_argument(
        "--emit",
        choices=["json", "observation"],
        default="json",
        help="json = array; observation = single line for observations.md",
    )
    ap.add_argument(
        "--mode",
        choices=["drain", "charge", "all"],
        default="drain",
        help="Which signals to emit (default: drain)",
    )
    ap.add_argument(
        "--record-metric",
        action="store_true",
        help="Also write the hit count to .claude/memory/metrics.jsonl (drain mode only)",
    )
    args = ap.parse_args()

    text, code = _load_text(args)
    if text is None:
        print("[]")
        return code

    try:
        charges, drains = _call_llm(text)
    except DetectorUnavailable as e:
        # Loud, not silent. A swallowed failure here reads on the dashboard as
        # "0 memory gaps", which is indistinguishable from a healthy session.
        if args.emit == "observation":
            print(f"- {date.today().isoformat()} [detector-down]: "
                  f"session signal detection did not run — {e}")
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        return 4

    hits = _select_hits(args.mode, charges, drains)
    _emit(hits, args.mode, args.emit)

    if args.record_metric and args.mode == "drain":
        # Record even when there are no hits: a measured zero and an absent
        # measurement have to be tellable apart on the dashboard.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import record_metric

        record_metric.record(
            repo=Path.cwd(),
            metric="memory-gap",
            value=float(len(hits)),
            meta={"topics": [h["suggested_memory_topic"] for h in hits]},
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
