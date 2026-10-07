#!/bin/bash
# startup-payload-audit.sh — measure the fixed per-session "startup payload" token tax.
#
# RTK/lean-ctx/Headroom all reduce *runtime* token cost (shell output, file reads, API
# context). None measure the *startup* cost: the layered CLAUDE.md + @imports + .claude/rules
# + auto-loaded MEMORY.md that load before you do anything. This audit quantifies and guards
# that growth — directly enforcing CLAUDE.md's own "read on demand, not upfront" rule.
#
# Deterministic (no LLM call). Writes .claude/reports/context/startup-payload.json, which the
# dashboard's Context Health tab reads. Self-paces to daily via a state-file guard so calling
# it from the orchestrator every day is a cheap no-op between runs.
#
# Ratchet: the fixed budget only catches growth past 8000, so creep below it went
# unnoticed. Each repo also keeps a ceiling that can only go DOWN — a run at or under
# it lowers the ceiling to the new total; a run above it is flagged `over_ceiling`
# and the ceiling stays put. Growth you meant is accepted with --rebaseline, which
# resets the ceiling to the current total. The estimate is deterministic (chars/4),
# which is what makes a ratchet safe here — same input, same number. Warns rather
# than blocks: this harness has no CI to fail.
#
# Usage:
#   startup-payload-audit.sh              — run for the current repo (cwd), respecting cadence
#   startup-payload-audit.sh --force      — ignore the cadence guard and run now
#   startup-payload-audit.sh --rebaseline — accept the current total as the new ceiling (implies --force)
#
# Env:
#   SDD_SKIP_STARTUP_AUDIT=1            — opt out entirely
#   SDD_STARTUP_PAYLOAD_BUDGET=<tokens> — absolute over-budget threshold (default 8000)
#   SDD_STARTUP_STALE_DAYS=<days>       — flag files unchanged longer than this (default 45)

set -u

[ "${SDD_SKIP_STARTUP_AUDIT:-0}" = "1" ] && exit 0

REPO="$(pwd)"
FORCE=false
REBASELINE=false
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=true ;;
    --rebaseline) FORCE=true; REBASELINE=true ;;
    *) echo "startup-payload-audit: unknown argument '$arg'" >&2; exit 2 ;;
  esac
done

MIN_GAP_DAYS=1
STATE_FILE="$REPO/.claude/memory/.last-startup-payload-audit"
TODAY="$(date +%Y-%m-%d)"

if [ "$FORCE" = false ] && [ -f "$STATE_FILE" ]; then
  last="$(cut -dT -f1 "$STATE_FILE" 2>/dev/null | head -1)"
  if [ "$last" = "$TODAY" ]; then
    exit 0   # already ran today — cheap no-op
  fi
fi

# Only meaningful inside an installed repo
[ -d "$REPO/.claude" ] || exit 0

BUDGET="${SDD_STARTUP_PAYLOAD_BUDGET:-8000}"
STALE_DAYS="${SDD_STARTUP_STALE_DAYS:-45}"

python3 - "$REPO" "$BUDGET" "$STALE_DAYS" "$REBASELINE" <<'PYEOF'
import json, os, sys, time
from pathlib import Path
from datetime import datetime, timezone

repo = Path(sys.argv[1])
budget = int(sys.argv[2])
stale_days = int(sys.argv[3])
rebaseline = sys.argv[4] == "true"
now = time.time()

def est_tokens(text: str) -> int:
    # Rough but stable estimate: ~4 chars/token.
    return max(0, round(len(text) / 4))

def age_days(p: Path) -> float:
    try:
        return round((now - p.stat().st_mtime) / 86400.0, 1)
    except OSError:
        return 0.0

# ── Resolve the auto-memory MEMORY.md path (repo path with / → -) ──────────────
def auto_memory_file(repo: Path) -> Path:
    escaped = str(repo).replace("/", "-")
    return Path.home() / ".claude" / "projects" / escaped / "memory" / "MEMORY.md"

# ── Collect the startup file set ──────────────────────────────────────────────
candidates = []
seen = set()

def add(p: Path, label: str):
    rp = p.resolve()
    if rp in seen:
        return
    seen.add(rp)
    candidates.append((label, p))

# Project instruction files that load at session start
add(repo / "CLAUDE.md", "CLAUDE.md")
add(repo / "AGENTS.md", "AGENTS.md")
for rule in sorted((repo / ".claude" / "rules").glob("*.md")) if (repo / ".claude" / "rules").is_dir() else []:
    add(rule, f".claude/rules/{rule.name}")
add(repo / ".claude" / "memory" / "hot-memory.md", ".claude/memory/hot-memory.md")
mem = auto_memory_file(repo)
add(mem, "auto-memory/MEMORY.md")

# ── Resolve one level of @imports inside CLAUDE.md and collect ghost refs ──────
ghosts = []

def import_refs(text: str):
    """`@ref` at the start of a line (after indentation) — the CLAUDE.md import form."""
    for line in text.splitlines():
        rest = line.lstrip()
        if not rest.startswith("@"):
            continue
        rest = rest[1:]
        if rest and not rest[0].isspace():
            yield rest.split()[0]

claude_md = repo / "CLAUDE.md"
if claude_md.is_file():
    try:
        text = claude_md.read_text(errors="replace")
    except OSError:
        text = ""
    for ref in import_refs(text):
        target = Path(os.path.expanduser(ref))
        if not target.is_absolute():
            target = (repo / ref)
        if target.is_file():
            add(target, f"@{ref}")
        else:
            ghosts.append(f"@{ref}")

# ── Measure ───────────────────────────────────────────────────────────────────
files = []
total = 0
stale_count = 0
for label, p in candidates:
    if not p.is_file():
        continue
    try:
        content = p.read_text(errors="replace")
    except OSError:
        continue
    tok = est_tokens(content)
    a = age_days(p)
    is_stale = a > stale_days
    if is_stale:
        stale_count += 1
    total += tok
    files.append({
        "path": label,
        "tokens": tok,
        "age_days": a,
        "stale": is_stale,
    })

files.sort(key=lambda f: f["tokens"], reverse=True)

# ── Ratchet ───────────────────────────────────────────────────────────────────
report_dir = repo / ".claude" / "reports" / "context"
report_dir.mkdir(parents=True, exist_ok=True)
ceiling_file = report_dir / "startup-payload-ceiling.json"
prev_ceiling = None
if ceiling_file.is_file():
    try:
        prev_ceiling = int(json.loads(ceiling_file.read_text())["ceiling"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A corrupt ceiling is reported, not silently replaced: rebaselining here
        # would quietly accept whatever growth happened since the last good run.
        print(f"startup-payload: unreadable ceiling file {ceiling_file} ({exc}) — "
              f"run with --rebaseline to reset it", file=sys.stderr)
        sys.exit(1)

if prev_ceiling is None or rebaseline:
    ceiling, over_ceiling = total, False
    ceiling_event = "rebaselined" if rebaseline else "initialized"
elif total > prev_ceiling:
    ceiling, over_ceiling, ceiling_event = prev_ceiling, True, "exceeded"
else:
    ceiling, over_ceiling = total, False
    ceiling_event = "lowered" if total < prev_ceiling else "held"
delta = None if prev_ceiling is None else total - prev_ceiling

if not over_ceiling:
    ceiling_file.write_text(json.dumps({
        "ceiling": ceiling,
        "set": datetime.now(timezone.utc).isoformat(),
        "event": ceiling_event,
    }, indent=2))

out = {
    "generated": datetime.now(timezone.utc).isoformat(),
    "repo": str(repo),
    "total_tokens": total,
    "budget": budget,
    "over_budget": total > budget,
    "ceiling": ceiling,
    "previous_ceiling": prev_ceiling,
    "delta": delta,
    "over_ceiling": over_ceiling,
    "ceiling_event": ceiling_event,
    "file_count": len(files),
    "stale_count": stale_count,
    "stale_days": stale_days,
    "ghosts": ghosts,
    "files": files,
}

(report_dir / "startup-payload.json").write_text(json.dumps(out, indent=2))
delta_txt = "" if delta is None else f", {delta:+d} vs ceiling"
print(f"startup-payload: {total} tok across {len(files)} files "
      f"(budget {budget}, {'OVER' if total > budget else 'ok'}; "
      f"ceiling {ceiling} {ceiling_event}{delta_txt}), "
      f"{stale_count} stale, {len(ghosts)} ghost refs")
if over_ceiling:
    print(f"WARN: startup payload grew {delta:+d} tok past its ceiling ({prev_ceiling}). "
          f"Trim it, or accept the growth with --rebaseline.", file=sys.stderr)
PYEOF
rc=$?
# A failed audit must not consume today's cadence slot.
[ "$rc" -eq 0 ] || exit "$rc"

# Record run date for the cadence guard
date -Iseconds > "$STATE_FILE"
