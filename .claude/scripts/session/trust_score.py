#!/usr/bin/env python3
"""
Trust Score helper for the SDD harness.

Single-user cumulative score over a rolling window. Reads/writes a JSONL
history and rewrites the "Harness Trust Score" line at the top of
`.claude/memory/hot-memory.md`. The score is observability only — it never
gates harness behavior (see docs/harness-documentation/SDD-USAGE.md).

Usage:
    # Apply today's delta (from session-judge). Repeat --delta once per judge
    # run; the median is applied and the spread gates it:
    trust_score.py apply --delta -1.0 --delta -2.0 --delta -1.0 --summary "..."
    # Single sample still works, and skips the spread gate:
    trust_score.py apply --delta -1.0 --summary "..."
    # Print current score + 7-day delta as JSON:
    trust_score.py show
"""

from __future__ import annotations  # PEP 604 (`X | None`) on Python 3.9 hosts

import argparse
import json
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_START = 20.0
DAILY_CAP = 4.5

# Spread gate for multi-sample judge verdicts (pass^k, not pass@k).
#
# The judge is an LLM at temperature > 0. A single draw from it was, until
# 2026-09-03, committed straight into a cumulative score that nothing ever
# revisits — so "score fell 4 points" and "the judge sampled differently"
# were the same observation. Running the judge k times and taking the median
# removes the outlier draw; this threshold decides when the samples disagree
# so much that no median is worth trusting.
#
# 2.0 on a [-4.5, +4.5] scale: three runs landing within 2 points of each
# other are reading the same day. A wider spread means the reading depends on
# which draw you looked at, and the honest delta for that day is 0.0, not
# whichever number the middle sample happened to be.
#
# Sources: Perrone, "What is Agentic Testing?" (pass^k over pass@k — a gate
# that greens on 1-of-3 is not a gate) and Visa's VVAH README (majority-vote
# FP filtering at temperature > 0). See docs/sources/articles/README.md.
JUDGE_SPREAD_LIMIT = 2.0
HISTORY = Path(".claude/memory/trust-score.jsonl")
HOT_MEMORY = Path(".claude/memory/hot-memory.md")
OBS_FILE = Path(".claude/memory/observations.md")
METRICS_FILE = Path(".claude/memory/metrics.jsonl")
SCORE_HEADER_PREFIX = "## Harness Trust Score:"


def load_history() -> list[dict]:
    if not HISTORY.is_file():
        return []
    records: list[dict] = []
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def current_score(history: list[dict]) -> float:
    return history[-1]["score"] if history else DEFAULT_START


def seven_day_delta(history: list[dict]) -> float | None:
    if not history:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    older = [r for r in history if datetime.fromisoformat(r["ts"]) < cutoff]
    if not older:
        return None
    return history[-1]["score"] - older[-1]["score"]


def clamp_delta(delta: float) -> float:
    return max(-DAILY_CAP, min(DAILY_CAP, delta))


def clamp_score(score: float) -> float:
    return max(0.0, min(100.0, score))


def arrow(n: float | None) -> str:
    if n is None:
        return "—"
    if n > 0.05:
        return f"▲ +{n:.1f}"
    if n < -0.05:
        return f"▼ {n:.1f}"
    return f"▬ {n:+.1f}"


def format_header(score: float, today_delta: float, seven: float | None) -> str:
    return (
        f"{SCORE_HEADER_PREFIX} {score:.1f}% "
        f"({arrow(today_delta)} today, 7d: {arrow(seven)})"
    )


def rewrite_hot_memory_header(new_line: str) -> bool:
    if not HOT_MEMORY.is_file():
        print(
            f"WARN: {HOT_MEMORY} not found — skipping header rewrite.",
            file=sys.stderr,
        )
        return False
    lines = HOT_MEMORY.read_text(encoding="utf-8").splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(SCORE_HEADER_PREFIX):
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        # Insert after the first `# ` heading or at the top.
        insert_at = next(
            (i + 1 for i, line in enumerate(lines) if line.startswith("# ")),
            0,
        )
        lines.insert(insert_at, new_line)
        lines.insert(insert_at + 1, "")
    HOT_MEMORY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def consensus_delta(deltas: list[float]) -> tuple[float, float | None, bool]:
    """Reduce k judge samples to one delta.

    Returns (delta, spread, inconclusive).

    A single sample carries no spread and is passed through unchanged — the
    gate cannot fire on evidence that was never collected, and saying so is
    more honest than inventing a spread of 0.0 for a sample size of one.
    """
    if not deltas:
        raise ValueError("consensus_delta requires at least one sample")
    if len(deltas) == 1:
        return deltas[0], None, False
    spread = max(deltas) - min(deltas)
    if spread > JUDGE_SPREAD_LIMIT:
        return 0.0, spread, True
    return statistics.median(deltas), spread, False


def cmd_apply(deltas: list[float], summary: str) -> int:
    history = load_history()
    base = current_score(history)
    delta, spread, inconclusive = consensus_delta(deltas)
    capped = clamp_delta(delta)
    new_score = clamp_score(base + capped)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Idempotent same-day guard: if today already has a record, do not double-apply.
    today = date.today().isoformat()
    if history and history[-1]["ts"].startswith(today):
        print(
            json.dumps({
                "status": "skipped",
                "reason": "already applied today",
                "score": base,
            })
        )
        return 0

    record = {
        "ts": ts,
        "delta_raw": delta,
        "delta_applied": capped,
        "score": new_score,
        "summary": summary,
        "samples": deltas,
        "spread": spread,
        "inconclusive": inconclusive,
    }
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    history.append(record)
    seven = seven_day_delta(history)
    header = format_header(new_score, capped, seven)
    rewrite_hot_memory_header(header)

    print(json.dumps({
        "status": "inconclusive" if inconclusive else "applied",
        "score": new_score,
        "delta_applied": capped,
        "delta_raw": delta,
        "samples": deltas,
        "spread": spread,
        "seven_day_delta": seven,
        "header": header,
    }))
    return 0


def _tally_tags(tags: str, counts: dict) -> None:
    """Count tag occurrences. Scores are NOT read from here — see _tally_metrics_since."""
    if "session-charge" in tags:
        counts["session_charges"] += 1
    if "memory-gap" in tags:
        counts["memory_gaps"] += 1
    if "loop-debt" in tags:
        counts["loop_debts"] += 1


def _split_observation(line: str) -> tuple[str, str] | None:
    """Split `- YYYY-MM-DD [tag,tag]: text` into (date, tags) by structure.

    Returns None for any line that is not an observation entry.
    """
    if not line.startswith("- ") or "[" not in line or "]:" not in line:
        return None
    date_str, sep, remainder = line[2:].partition(" [")
    if not sep:
        return None
    tags, sep, _ = remainder.partition("]:")
    if not sep:
        return None
    return date_str, tags


def _tally_metrics_since(since: date, counts: dict) -> None:
    """Read session-quality and keep-rate from metrics.jsonl, never from prose.

    These used to be pattern-matched out of the observation text, where
    `(\\d+)%` read "84.5%" as 5 and silently mis-scored the trust battery.
    Idle-routine windows carry no judgement and are skipped.
    """
    if not METRICS_FILE.is_file():
        return
    for line in METRICS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"WARN: {METRICS_FILE}: {e}", file=sys.stderr)
            continue
        try:
            if date.fromisoformat(rec["date"]) < since:
                continue
        except (KeyError, ValueError):
            continue
        if rec.get("idle"):
            continue
        value = rec.get("value")
        if rec.get("metric") == "session-quality" and isinstance(value, (int, float)):
            if value >= 4:
                counts["sq_high"] += 1
            elif value <= 2:
                counts["sq_low"] += 1
        elif rec.get("metric") == "keep-rate" and isinstance(value, (int, float)) and value >= 80:
            counts["kr_high"] += 1


def _parse_observations_since(since: date) -> dict:
    counts = {"session_charges": 0, "memory_gaps": 0,
              "sq_high": 0, "sq_low": 0, "kr_high": 0, "loop_debts": 0}
    if OBS_FILE.is_file():
        for line in OBS_FILE.read_text(encoding="utf-8").splitlines():
            parsed = _split_observation(line)
            if parsed is None:
                continue
            date_str, tags = parsed
            try:
                entry_date = date.fromisoformat(date_str)
            except ValueError:
                continue
            if entry_date >= since:
                _tally_tags(tags, counts)
    _tally_metrics_since(since, counts)
    return counts


def cmd_auto_score() -> int:
    """Score deterministically from observation tags; no LLM Judge needed."""
    history = load_history()
    today = date.today()

    if history and history[-1]["ts"].startswith(today.isoformat()):
        print(json.dumps({"status": "skipped", "reason": "already applied today",
                          "score": history[-1]["score"]}))
        return 0

    if history:
        since = date.fromisoformat(history[-1]["ts"][:10]) + timedelta(days=1)
    else:
        since = today

    c = _parse_observations_since(since)
    charges = min(c["session_charges"], 3)
    gaps = min(c["memory_gaps"], 3)
    sq_hi = min(c["sq_high"], 1)
    sq_lo = min(c["sq_low"], 1)
    kr_hi = min(c["kr_high"], 1)
    loop_debts = min(c.get("loop_debts", 0), 2)

    delta = float(charges + sq_hi + kr_hi - gaps * 2 - sq_lo - loop_debts)

    parts = []
    if charges:
        parts.append(f"{charges} approval(s)")
    if sq_hi:
        parts.append("session-quality≥4/5")
    if kr_hi:
        parts.append("keep-rate≥80%")
    if gaps:
        parts.append(f"{gaps} re-explanation(s)")
    if sq_lo:
        parts.append("session-quality≤2/5")
    if loop_debts:
        parts.append(f"{loop_debts} loop-debt(s)")
    summary = "auto-score: " + (", ".join(parts) if parts else "no signals detected")

    # One sample by construction, and correctly so: auto-score counts tags in
    # observations.md and metrics.jsonl. It is deterministic — re-running it on
    # the same inputs cannot produce a different number, so there is nothing for
    # the spread gate to measure. Only the LLM-judged `apply` path samples.
    return cmd_apply([delta], summary)


def cmd_show() -> int:
    history = load_history()
    score = current_score(history)
    seven = seven_day_delta(history)
    last_delta = history[-1]["delta_applied"] if history else 0.0
    print(json.dumps({
        "score": score,
        "last_delta_applied": last_delta,
        "seven_day_delta": seven,
        "header": format_header(score, last_delta, seven),
        "records": len(history),
    }, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_apply = sub.add_parser("apply", help="Apply a daily delta")
    p_apply.add_argument(
        "--delta",
        type=float,
        required=True,
        action="append",
        dest="deltas",
        help=f"Judge score_delta. Repeat once per judge run; the median is "
             f"applied and a spread above {JUDGE_SPREAD_LIMIT:.1f} is recorded "
             f"as inconclusive (delta 0.0).",
    )
    p_apply.add_argument("--summary", type=str, default="")

    sub.add_parser("show", help="Show current score")
    sub.add_parser("auto-score", help="Score deterministically from observations.md tags")

    args = ap.parse_args()
    if args.cmd == "apply":
        return cmd_apply(args.deltas, args.summary)
    if args.cmd == "show":
        return cmd_show()
    if args.cmd == "auto-score":
        return cmd_auto_score()
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
