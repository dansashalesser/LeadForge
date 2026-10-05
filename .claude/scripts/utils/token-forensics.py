#!/usr/bin/env python3
"""token-forensics.py — where the tokens actually went, from the transcripts.

Every skill in this harness that touches tokens (`context-optimization`,
`context-window-management`, `rtk-token-reduction`, `cost-optimization`) is
*prescriptive*: advice about reducing context. None of them measure what was
actually spent, so there is no way to tell which advice would have paid.

This measures. Five analyses, all read-only, all from `~/.claude/projects/**/*.jsonl`:

1. **Deduplicated totals.** One API response is written as one JSONL line per
   content block, each repeating the identical `message.usage`. Summing naively
   overstates by ~80% here. Collapse on `requestId` first.

2. **Amplification.** Context injected at request *i* is re-sent on every later
   request in that session, so a tool result's true cost is its size times the
   number of requests that followed it. A 20k-char read on turn 3 of a 200-turn
   session is not 20k, it is ~4M cache-read tokens. Ranking tools by what they
   *returned* hides this completely; ranking by what they *caused* is the point.

3. **Rolling 5h window.** Two-pointer sum over time-ordered requests, giving the
   peak — what a usage limit actually measures, rather than a daily total.

4. **Session shape.** Peak concurrency and the count of short sessions. Many
   short parallel sessions means scripted `claude -p` invocations, not a human,
   and every fresh session pays full cache-creation cost on its first turn.

5. **Automation split.** For a harness that spawns agents and runs nightly
   routines, the share of spend that is *not* interactive is the number that
   matters. Two ways to get it, and the distinction is load-bearing:

   - `isSidechain` would tag subagent turns directly. **Measured 2026-08-30: it
     is present on every assistant line and `True` on none of them** — 0 of 6,423
     across 150 transcripts, in sessions that demonstrably spawned agents. So
     subagent turns are not tagged in this transcript format; each agent appears
     to get its own session file, indistinguishable from a short human session.
   - Therefore the reported split uses a **short-session proxy** (spend in
     sessions under 5 minutes), which is explicitly labelled as a proxy. If a
     future Claude Code version starts populating `isSidechain`, the script
     detects that and switches to the exact split automatically.

   This is deliberately not reported as "subagents used 0 tokens." A field that
   is never populated means *unknown*, not zero, and printing 0% there would be
   a confident false negative on the exact question the analysis exists to answer.

6. **Hook injection.** Hooks inject text on every prompt, session start and edit,
   and that text is amplified exactly like a tool result. Read from transcript
   `attachment` lines, split into two classes that are never summed:
   `hook_additional_context` is confirmed to reach the model; `hook_success`
   stdout reaches it only for some events. Mixing them would overstate the cost.

Usage:
    python3 scripts/utils/token-forensics.py                  # last 30 days
    python3 scripts/utils/token-forensics.py --days 7
    python3 scripts/utils/token-forensics.py --project sdd-harness
    python3 scripts/utils/token-forensics.py --json
    python3 scripts/utils/token-forensics.py --limit 200      # cap files scanned

Token figures are estimates (chars/4 for tool output). The *ranking* is the
product, not the absolute — read it to find which tool is worth bounding.
"""

from __future__ import annotations

import argparse
import json
import shlex
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
USAGE_FIELDS = (
    ("input", "input_tokens"),
    ("output", "output_tokens"),
    ("cache_read", "cache_read_input_tokens"),
    ("cache_create", "cache_creation_input_tokens"),
)
WINDOW = timedelta(hours=5)
SHORT_SESSION = timedelta(minutes=5)
CHARS_PER_TOKEN = 4

# Price of each token class relative to one input token. A raw token count ranks
# the wrong sessions: a million cache reads cost a tenth of a million fresh input
# tokens, and output costs five times as much as either.
#   cache read  0.1x   — the recurring win; paid every turn the prefix survives
#   cache write 2.0x   — paid once, to buy those 0.1x reads
#   output      5.0x
# Source: claude.com/blog/maximizing-the-value-of-your-claude-code-sessions
COST_WEIGHTS = {
    "input": 1.0,
    "output": 5.0,
    "cache_read": 0.1,
    "cache_create": 2.0,
}

# A re-prefill after a cache bust shows up as an unusually large cache_creation on
# the turn right after the switch. Below this it is ordinary incremental caching.
CACHE_BUST_MIN_CREATE = 20_000

# Transcripts carry placeholder model names for assistant turns the model did not
# generate — `<synthetic>` marks injected/error messages. These are not model
# switches: counting them produced 4 spurious busts out of 5 on the first run, all
# with 0 re-prefill. Anything in angle brackets is a placeholder, not a model.
def is_real_model(name: str) -> bool:
    return bool(name) and not name.startswith("<")


# Attachment classes that carry hook output. Only the first is confirmed to
# reach the model on every event; the second is stdout that reaches it for some
# events (UserPromptSubmit, SessionStart) and not others.
HOOK_CLASSES = ("hook_additional_context", "hook_success")
SCRIPT_SUFFIXES = (".sh", ".py", ".js", ".cjs", ".mjs", ".ts")


def hook_label(att: dict) -> str:
    """Event plus the script that ran, so hooks sharing an event stay separate.

    `hookName` alone ("SessionStart:startup") lumps every hook on that event
    together, which cannot tell you which one to trim.
    """
    name = att.get("hookName") or att.get("hookEvent") or "?"
    command = att.get("command")
    if not isinstance(command, str) or not command:
        return name
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    scripts = [t for t in tokens if t.endswith(SCRIPT_SUFFIXES)]
    pick = scripts[-1] if scripts else (tokens[0] if tokens else "")
    return f"{name} {Path(pick).name}" if pick else name


def hook_injection(obj: dict, cutoff) -> tuple[tuple[str, str], int] | None:
    """((class, label), chars) for a hook attachment line in the window, else None."""
    att = obj.get("attachment")
    if not isinstance(att, dict) or att.get("type") not in HOOK_CLASSES:
        return None
    ts = parse_ts(obj.get("timestamp"))
    if cutoff and ts and ts < cutoff:
        return None
    chars = block_size(att.get("content"))
    if not chars:
        return None
    return (att["type"], hook_label(att)), chars


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def block_size(content) -> int:
    """Character size of a tool_result payload, whatever shape it arrived in."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for blk in content:
            if isinstance(blk, dict):
                total += len(blk.get("text") or "")
                if blk.get("type") == "image":
                    total += 4000  # rough, images are not free
            elif isinstance(blk, str):
                total += len(blk)
        return total
    if isinstance(content, dict):
        return len(json.dumps(content))
    return 0


@dataclass
class SessionSpan:
    """First and last request time for one session, plus its request count.

    A dataclass rather than a dict: with a plain dict literal the value type is
    inferred as `int | None` across all three keys, so `n += 1` and
    `end - start` are both type errors even though they are correct at runtime.
    """

    start: datetime | None = None
    end: datetime | None = None
    n: int = 0

    def observe(self, ts: datetime | None) -> None:
        self.n += 1
        if ts is None:
            return
        self.start = ts if self.start is None else min(self.start, ts)
        self.end = ts if self.end is None else max(self.end, ts)

    def duration(self) -> timedelta | None:
        if self.start is None or self.end is None:
            return None
        return self.end - self.start

    def is_short(self) -> bool:
        d = self.duration()
        return d is not None and d < SHORT_SESSION


class Analysis:
    def __init__(self):
        self.requests = []          # (timestamp, tokens, is_sidechain)
        self.sessions: defaultdict[str, SessionSpan] = defaultdict(SessionSpan)
        self.tool_returned = defaultdict(int)   # tool -> chars returned
        self.tool_amplified = defaultdict(int)  # tool -> chars * requests_after
        self.tool_calls = defaultdict(int)
        self.totals = defaultdict(int)
        self.naive_total = 0
        self.collapsed = 0
        self.files = 0
        self.sidechain_seen = False   # did isSidechain=True EVER appear?
        self.session_tokens = defaultdict(int)
        self.cache_busts = []         # model switches mid-session, with re-prefill cost
        self.models_seen = defaultdict(int)   # model -> requests
        # (hook class, hook label) -> chars / chars * requests_after / events
        self.hook_injected = defaultdict(int)
        self.hook_amplified = defaultdict(int)
        self.hook_events = defaultdict(int)

    # ── ingest ────────────────────────────────────────────────────────────────

    def add_file(self, path: Path, cutoff):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return

        by_request = {}
        order = []          # request keys in timestamp order
        pending_results = []  # (tool_name, chars, position_at_time_of_result)
        pending_tool_names = {}  # tool_use_id -> tool name
        pending_hooks = []    # (hook key, chars, position_at_time_of_injection)

        for lineno, line in enumerate(lines):
            try:
                obj = json.loads(line)
            except ValueError:
                continue

            if obj.get("type") == "attachment":
                hook = hook_injection(obj, cutoff)
                if hook:
                    pending_hooks.append((*hook, len(order)))
                continue

            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            ts = parse_ts(obj.get("timestamp"))
            if cutoff and ts and ts < cutoff:
                continue

            content = msg.get("content")
            if isinstance(content, list):
                for blk in content:
                    if not isinstance(blk, dict):
                        continue
                    if blk.get("type") == "tool_use":
                        pending_tool_names[blk.get("id")] = blk.get("name") or "?"
                    elif blk.get("type") == "tool_result":
                        name = pending_tool_names.get(blk.get("tool_use_id"), "?")
                        chars = block_size(blk.get("content"))
                        self.tool_returned[name] += chars
                        self.tool_calls[name] += 1
                        pending_results.append((name, chars, len(order)))

            if obj.get("type") != "assistant":
                continue

            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue

            for _field, raw in USAGE_FIELDS:
                self.naive_total += usage.get(raw, 0) or 0

            key = obj.get("requestId") or msg.get("id") or f"{path}:{lineno}"
            if obj.get("isSidechain") is True:
                self.sidechain_seen = True

            prev = by_request.get(key)
            if prev is None:
                by_request[key] = {
                    "ts": ts,
                    "sidechain": obj.get("isSidechain") is True,
                    "session": obj.get("sessionId") or path.stem,
                    "model": msg.get("model") or "",
                    **{f: (usage.get(r, 0) or 0) for f, r in USAGE_FIELDS},
                }
                order.append(key)
            else:
                self.collapsed += 1
                for f, r in USAGE_FIELDS:
                    prev[f] = max(prev[f], usage.get(r, 0) or 0)

        if not by_request:
            return
        self.files += 1

        total_requests = len(order)
        for name, chars, position in pending_results:
            requests_after = max(total_requests - position, 0)
            self.tool_amplified[name] += chars * requests_after
        for key, chars, position in pending_hooks:
            self.hook_injected[key] += chars
            self.hook_events[key] += 1
            self.hook_amplified[key] += chars * max(total_requests - position, 0)

        last_model = {}   # session -> model on the previous request
        for key in order:
            rec = by_request[key]
            tokens = sum(rec[f] for f, _ in USAGE_FIELDS)
            for f, _ in USAGE_FIELDS:
                self.totals[f] += rec[f]
            self.totals["sidechain" if rec["sidechain"] else "main"] += tokens
            self.session_tokens[rec["session"]] += tokens
            if rec["ts"]:
                self.requests.append((rec["ts"], tokens, rec["sidechain"]))
            self.sessions[rec["session"]].observe(rec["ts"])

            # Cache-bust detection. The prompt cache is keyed on model (and on
            # effort/mode, which transcripts do not record). Switching model
            # mid-session invalidates the prefix, so the next turn re-prefills the
            # whole conversation at cache-WRITE price instead of reading it at 0.1x.
            # Only the model half is observable here — say so rather than implying
            # this is the complete picture.
            model = rec["model"]
            if is_real_model(model):
                self.models_seen[model] += 1
                prev_model = last_model.get(rec["session"])
                if prev_model and prev_model != model:
                    self.cache_busts.append({
                        "session": rec["session"],
                        "at": rec["ts"].isoformat() if rec["ts"] else None,
                        "from_model": prev_model,
                        "to_model": model,
                        "reprefill_tokens": rec["cache_create"],
                        "large_reprefill": rec["cache_create"] >= CACHE_BUST_MIN_CREATE,
                    })
                last_model[rec["session"]] = model

    # ── derived ───────────────────────────────────────────────────────────────

    def peak_window(self):
        """Peak token load in any rolling 5h window (two-pointer over sorted time)."""
        pts = sorted((t, n) for t, n, _ in self.requests)
        if not pts:
            return 0, None
        left = 0
        running = 0
        best = 0
        best_at = None
        for right, (ts, tokens) in enumerate(pts):
            running += tokens
            while pts[left][0] < ts - WINDOW:
                running -= pts[left][1]
                left += 1
            if running > best:
                best, best_at = running, ts
        return best, best_at

    def shape(self):
        """Peak concurrent sessions, plus how many were short."""
        events = []
        durations = []
        counts = []
        for s in self.sessions.values():
            span = s.duration()
            if span is not None and s.start is not None and s.end is not None:
                events.append((s.start, 1))
                events.append((s.end, -1))
                durations.append(span.total_seconds())
            counts.append(s.n)
        events.sort(key=lambda e: (e[0], -e[1]))
        cur = peak = 0
        for _ts, delta in events:
            cur += delta
            peak = max(peak, cur)
        short = sum(1 for s in self.sessions.values() if s.is_short())
        return {
            "sessions": len(self.sessions),
            "peak_concurrent": peak,
            "short_sessions": short,
            "median_requests": statistics.median(counts) if counts else 0,
            "median_duration_min": round(statistics.median(durations) / 60, 1) if durations else 0,
        }

    def automation_split(self):
        """Non-interactive share of spend.

        Exact when `isSidechain` is populated. When it is not — which is the
        current reality, measured — fall back to a short-session proxy and SAY
        that it is a proxy. Reporting an unpopulated field as 0% would be a
        confident false negative, not a measurement.
        """
        total = self.total()
        if self.sidechain_seen:
            return {
                "method": "exact (isSidechain)",
                "automated": self.totals["sidechain"],
                "interactive": self.totals["main"],
                "pct": round(self.totals["sidechain"] / total * 100, 1) if total else 0.0,
                "measurable": True,
            }
        short_spend = sum(
            self.session_tokens[sid]
            for sid, s in self.sessions.items()
            if s.is_short()
        )
        return {
            "method": "proxy (sessions under 5min)",
            "automated": short_spend,
            "interactive": total - short_spend,
            "pct": round(short_spend / total * 100, 1) if total else 0.0,
            "measurable": False,
        }

    def total(self):
        return sum(self.totals[f] for f, _ in USAGE_FIELDS)

    def hooks_by_amplified(self, cls: str, top: int):
        """Hooks of one class, ranked by what they caused. Classes are never mixed."""
        rows = [
            {"hook": label, "events": self.hook_events[(c, label)],
             "injected_chars": self.hook_injected[(c, label)],
             "amplified_chars": amp}
            for (c, label), amp in self.hook_amplified.items() if c == cls
        ]
        rows.sort(key=lambda r: (-r["amplified_chars"], -r["injected_chars"]))
        return rows[:top]

    def weighted_cost(self):
        """Spend in input-token-equivalents, using the real per-class price ratios.

        Raw token count treats a cache read and an output token as the same thing;
        they differ by 50x. This is the number to rank on. It is a cost *ratio*,
        not currency — the harness runs on a subscription and has no per-token bill.
        """
        return sum(self.totals[f] * COST_WEIGHTS[f] for f, _ in USAGE_FIELDS)

    def cost_breakdown(self):
        weighted = self.weighted_cost()
        return {
            f: {
                "tokens": self.totals[f],
                "weight": COST_WEIGHTS[f],
                "weighted": round(self.totals[f] * COST_WEIGHTS[f], 1),
                "pct_of_cost": (
                    round(self.totals[f] * COST_WEIGHTS[f] / weighted * 100, 1)
                    if weighted else 0.0
                ),
            }
            for f, _ in USAGE_FIELDS
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30, help="lookback window (default 30, 0 = all)")
    ap.add_argument("--project", help="only scan project dirs whose name contains this")
    ap.add_argument("--limit", type=int, default=0, help="cap files scanned (newest first)")
    ap.add_argument("--top", type=int, default=12, help="tools to list (default 12)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not CLAUDE_PROJECTS.is_dir():
        print(f"no transcripts at {CLAUDE_PROJECTS}", file=sys.stderr)
        return 2

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)) if args.days else None
    files = [
        p for p in CLAUDE_PROJECTS.glob("*/*.jsonl")
        if not args.project or args.project in p.parent.name
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if args.limit:
        files = files[:args.limit]

    a = Analysis()
    for path in files:
        a.add_file(path, cutoff)

    total = a.total()
    if total == 0:
        print("No usage found in the selected window.", file=sys.stderr)
        return 2

    peak, peak_at = a.peak_window()
    shape = a.shape()
    inflation = ((a.naive_total / total) - 1) * 100 if total else 0
    split = a.automation_split()

    amplified = sorted(a.tool_amplified.items(), key=lambda kv: -kv[1])[:args.top]
    hooks = {cls: a.hooks_by_amplified(cls, args.top) for cls in HOOK_CLASSES}

    if args.json:
        print(json.dumps({
            "files": a.files,
            "window_days": args.days,
            "totals": {f: a.totals[f] for f, _ in USAGE_FIELDS},
            "total_tokens": total,
            "weighted_cost": round(a.weighted_cost(), 1),
            "cost_weights": COST_WEIGHTS,
            "cost_breakdown": a.cost_breakdown(),
            "models_seen": dict(a.models_seen),
            "cache_busts": a.cache_busts,
            "cache_bust_count": len(a.cache_busts),
            "naive_total": a.naive_total,
            "duplicate_inflation_pct": round(inflation, 1),
            "collapsed_lines": a.collapsed,
            "peak_5h_tokens": peak,
            "peak_5h_at": peak_at.isoformat() if peak_at else None,
            "automation_split": split,
            "sidechain_field_populated": a.sidechain_seen,
            "shape": shape,
            "tools_by_amplified_chars": [
                {"tool": t, "amplified_chars": c,
                 "returned_chars": a.tool_returned[t], "calls": a.tool_calls[t]}
                for t, c in amplified
            ],
            "hooks_by_amplified_chars": hooks,
        }, indent=2))
        return 0

    span = f"last {args.days}d" if args.days else "all time"
    print(f"## Token Forensics — {span}, {a.files} transcripts\n")
    print(f"Total (deduplicated):  {total:>15,} tokens")
    print(f"  input                {a.totals['input']:>15,}")
    print(f"  output               {a.totals['output']:>15,}")
    print(f"  cache read           {a.totals['cache_read']:>15,}")
    print(f"  cache create         {a.totals['cache_create']:>15,}")
    print(f"Naive (undeduplicated) {a.naive_total:>15,}  → +{inflation:.0f}% "
          f"({a.collapsed:,} repeated blocks collapsed)")
    print()

    # ── Weighted cost ─────────────────────────────────────────────────────────
    weighted = a.weighted_cost()
    breakdown = a.cost_breakdown()
    print(f"Cost-weighted:         {weighted:>15,.0f} input-token-equivalents")
    print("  Raw counts rank the wrong sessions — a cache read and an output token")
    print("  differ 50x in price. Weights: output 5x, cache write 2x, cache read 0.1x.")
    for f, _ in USAGE_FIELDS:
        b = breakdown[f]
        print(f"  {f:<20} {b['weighted']:>15,.0f}  ({b['pct_of_cost']:>4.1f}% of cost, "
              f"{b['weight']}x)")
    dominant = max(breakdown.items(), key=lambda kv: kv[1]["weighted"])[0]
    if dominant == "cache_create":
        print("  ⚠️  Cache CREATION dominates cost. You are paying 2x to build prefixes")
        print("     you are not reading back — sessions are too short to amortize, or")
        print("     something is busting the cache (see below).")
    elif dominant == "output":
        print("  Output dominates — expected for generation-heavy work, not a problem.")
    print()

    # ── Cache busts ───────────────────────────────────────────────────────────
    print(f"Cache busts (model switched mid-session): {len(a.cache_busts)}")
    print("  The prompt cache is keyed on model, effort, and mode. Switching any of")
    print("  them re-prefills the whole conversation at 2x write price instead of")
    print("  reading it back at 0.1x. Only the MODEL half is recorded in transcripts —")
    print("  effort and fast-mode switches are invisible here, so this is a floor,")
    print("  not a total.")
    if a.models_seen:
        for m, n in sorted(a.models_seen.items(), key=lambda kv: -kv[1]):
            print(f"    {m:<34} {n:>7,} requests")
    if a.cache_busts:
        big = [b for b in a.cache_busts if b["large_reprefill"]]
        print(f"  {len(big)} of {len(a.cache_busts)} were followed by a re-prefill "
              f"over {CACHE_BUST_MIN_CREATE:,} tokens:")
        for b in sorted(a.cache_busts, key=lambda x: -x["reprefill_tokens"])[:5]:
            when = b["at"][:16].replace("T", " ") if b["at"] else "unknown time"
            print(f"    {when}  {b['from_model']} → {b['to_model']}  "
                  f"re-prefill {b['reprefill_tokens']:,}")
        print("  Fix: pick model and effort at session start or right after /clear,")
        print("  not mid-task. Use /rewind rather than /compact to drop recent turns —")
        print("  rewinding keeps the earlier prefix cached.")
    else:
        print("  None found — no session changed model mid-run in this window.")
    print()
    print(f"Peak 5h window:        {peak:>15,} tokens"
          + (f"   at {peak_at:%Y-%m-%d %H:%M UTC}" if peak_at else ""))
    print("  This is what a usage limit measures. A large daily total spread evenly")
    print("  is fine; a small one concentrated in five hours is what locks you out.")
    print()
    print(f"Automated / non-interactive spend  ({split['method']})")
    print(f"  automated            {split['automated']:>15,} tokens  ({split['pct']:.0f}%)")
    print(f"  interactive          {split['interactive']:>15,} tokens")
    if not split["measurable"]:
        print("  NOTE: `isSidechain` is present on assistant lines but never True in")
        print("  this transcript format, so subagent turns cannot be identified")
        print("  directly — each agent gets its own session file. The figure above is")
        print("  a PROXY (spend in sessions under 5 minutes), not a measurement of")
        print("  subagent cost. It is not 0% and it is not exact; it is unknown, and")
        print("  this is the closest available stand-in.")
    if split["pct"] > 50:
        print("  ⚠️  Most spend is non-interactive. Every fresh session pays full")
        print("     cache-creation cost on its first turn, so spawn COUNT and routine")
        print("     cadence matter more than the length of any individual run.")
    print()
    print(f"Sessions: {shape['sessions']}  |  peak concurrent: {shape['peak_concurrent']}"
          f"  |  short (<5min): {shape['short_sessions']}"
          f"  |  median {shape['median_requests']:.0f} req / {shape['median_duration_min']}min")
    if shape["peak_concurrent"] >= 3 or shape["short_sessions"] > shape["sessions"] * 0.4:
        print("  ⚠️  Session shape looks scripted (many short and/or parallel sessions).")
        print("     Headless runners pay full cache-creation on every invocation —")
        print("     check routine cadence before blaming interactive use.")
    print()
    print(f"### Tools by AMPLIFIED cost (top {len(amplified)})")
    print()
    print("Amplified = chars returned x requests that followed it in the same session.")
    print("This ranks what a tool CAUSED, not what it returned — an early large read")
    print("is re-sent on every later turn, so its true cost is orders of magnitude")
    print("above its size. Bound the top rows first.")
    print()
    print("| Tool | Calls | Returned (chars) | Amplified (~tokens) |")
    print("|---|---:|---:|---:|")
    for tool, chars in amplified:
        print(f"| {tool} | {a.tool_calls[tool]:,} | {a.tool_returned[tool]:,} "
              f"| {chars // CHARS_PER_TOKEN:,} |")
    print()
    print("### Hooks by AMPLIFIED cost")
    print()
    print("Same amplification as tools: text a hook injects is re-sent on every later")
    print("turn. Two classes, reported separately and never summed —")
    print("`hook_additional_context` is confirmed to reach the model; `hook_success`")
    print("stdout reaches it only for some events, so treat that table as an upper bound.")
    for cls in HOOK_CLASSES:
        print()
        print(f"#### {cls}")
        if not hooks[cls]:
            print("None in this window.")
            continue
        print()
        print("| Hook | Events | Injected (chars) | Amplified (~tokens) |")
        print("|---|---:|---:|---:|")
        for row in hooks[cls]:
            print(f"| {row['hook']} | {row['events']:,} | {row['injected_chars']:,} "
                  f"| {row['amplified_chars'] // CHARS_PER_TOKEN:,} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
