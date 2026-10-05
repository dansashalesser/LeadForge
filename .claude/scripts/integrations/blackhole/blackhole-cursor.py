#!/usr/bin/env python3
"""Claude Code -> blackhole.glsl bridge: grow/move a black hole with context use.

Adapted for the SDD harness from https://github.com/s0xDk/ghostty-blackhole (MIT).

OPT-IN. Every cursor write is gated behind the environment variable
SDD_BLACKHOLE: unless `SDD_BLACKHOLE=1` is set, `apply()` is a no-op — hooks do
nothing and the statusLine prints its readout WITHOUT touching the cursor color.
Set `SDD_BLACKHOLE=1` (e.g. in your shell profile) to turn the hole on; unset it
to turn it off. This is the live on/off switch; wiring the script into
settings.json (see SETUP.md) is the install.

ONE script, wired three ways in settings.json (see SETUP.md). Each run reads the
JSON Claude pipes on stdin and smuggles a level into the shader through the
*cursor color*: an OSC 12 escape encodes the level into the low nibbles of an
amber cursor color, and blackhole.glsl decodes it back out of its
iCurrentCursorColor uniform every frame. No file rewrite, no SIGUSR2 reload, no
recompile hitch — and since the cursor color is per-surface, the hole lives only
in the Ghostty surface this session runs in. The role is decided by
`hook_event_name` in that JSON:

  * SessionStart hook -> reset to a fresh, tiny corner hole (level 0.0)
  * SessionEnd   hook -> OSC 112 resets the cursor color -> hole hidden
  * statusLine (no hook_event_name) -> set level to the context-window fill
                                        (0..1) and print a one-line readout

Requires `SIZE_MODE MODE_TOKENS` in blackhole.glsl and Ghostty 1.3+. Must never
raise: a crashing statusLine blanks Claude's status bar and a crashing hook
spams stderr, so every step is best-effort and the statusLine path always prints
something.
"""

import json
import os
import subprocess
import sys
import time

# Accretion-disk amber. The high nibbles are the shader's 12-bit signature —
# keep in sync with TOKEN_BASE_HI in blackhole.glsl.
CURSOR_BASE = (0xF0, 0xB0, 0x00)


def session_tty():
    """Device path of the terminal this session runs in. Claude Code spawns
    statusLine/hook commands without a controlling terminal (/dev/tty fails
    with ENXIO), but the `claude` process itself sits on the Ghostty pty —
    walk up the ancestor chain and take the first real tty."""
    pid = os.getppid()
    for _ in range(10):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,tty=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=1).stdout.split()
        except (OSError, subprocess.SubprocessError):
            return None
        if len(out) < 2:
            return None
        if out[1] != "??":
            return "/dev/" + out[1]
        if not out[0].isdigit() or int(out[0]) <= 1:
            return None
        pid = int(out[0])
    return None


def emit(seq):
    """Write an escape sequence straight to this session's terminal. stdout
    won't do — Claude Code captures it (statusLine) or discards it (hooks)."""
    try:
        with open("/dev/tty", "wb") as tty:
            tty.write(seq)
        return
    except OSError:
        pass
    path = session_tty()
    if path:
        try:
            with open(path, "wb") as tty:
                tty.write(seq)
        except OSError:
            pass  # headless / tty gone -> nothing to drive


def apply(level):
    """Encode the level into the cursor color via OSC 12. The shader trusts a
    color only when the base nibbles and a 4-bit checksum line up (16 bits),
    so a theme's own cursor color can't accidentally drive the hole.
    Negative level -> OSC 112 resets the cursor color to the theme's own,
    which the shader reads as "no session".

    OPT-IN GATE: no cursor write unless SDD_BLACKHOLE=1. When off this returns
    immediately so the statusLine still prints but the cursor is left alone."""
    if os.environ.get("SDD_BLACKHOLE") != "1":
        return
    if level < 0.0:
        emit(b"\033]112\007")
        return
    fill = max(0, min(250, int(round(level * 250.0))))
    hi, lo = fill >> 4, fill & 0xF
    rgb = (CURSOR_BASE[0] | (hi ^ lo ^ 0x5),
           CURSOR_BASE[1] | hi,
           CURSOR_BASE[2] | lo)
    emit(b"\033]12;#%02x%02x%02x\007" % rgb)


def context_fill(data):
    """Fraction of the context window in use, 0.0 .. 1.0."""
    cw = data.get("context_window") or {}
    pct = cw.get("used_percentage")
    if pct is None:
        used = cw.get("total_input_tokens") or 0
        size = cw.get("context_window_size") or 0
        pct = (100.0 * used / size) if size else 0.0
    return max(0.0, min(1.0, pct / 100.0))


def bar(level, width=10):
    filled = int(round(max(0.0, level) * width))
    return "█" * filled + "░" * (width - filled)


def home_rel(path):
    """~-abbreviated current directory, last two components."""
    if not path:
        return ""
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        path = "~" + path[len(home):]
    parts = path.split(os.sep)
    return os.sep.join(parts[-2:]) if len(parts) > 2 else path


def git_branch(cwd):
    """Current git branch, or '' if not a repo / git missing."""
    if not cwd:
        return ""
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=1)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def usage_limits(data):
    """'5h 24% · wk 41%' from rate_limits, '' when absent (API-key users, or
    before the first response of the session). Adds the reset time once a
    window passes 80%, when it becomes the thing you actually want to know."""
    limits = data.get("rate_limits") or {}
    parts = []
    for key, label in (("five_hour", "5h"), ("seven_day", "wk")):
        window = limits.get(key) or {}
        pct = window.get("used_percentage")
        if not isinstance(pct, (int, float)):
            continue
        text = f"{label} {pct:.0f}%"
        resets = window.get("resets_at")
        if pct >= 80 and isinstance(resets, (int, float)):
            text += time.strftime(" ↻%H:%M", time.localtime(resets))
        parts.append(text)
    return " · ".join(parts)


def status_line(data, level):
    """Built-in-style readout (model · dir · branch · cost · limits) plus the hole bar."""
    parts = [f"⚫️ {bar(level)} {level * 100:.0f}%"]
    model = (data.get("model") or {}).get("display_name")
    if model:
        parts.append(model)
    cwd = home_rel(data.get("cwd", ""))
    if cwd:
        parts.append(cwd)
    branch = git_branch(data.get("cwd", ""))
    if branch:
        parts.append(f"⎇ {branch}")
    cost = (data.get("cost") or {}).get("total_cost_usd")
    if isinstance(cost, (int, float)):
        parts.append(f"${cost:.2f}")
    limits = usage_limits(data)
    if limits:
        parts.append(limits)
    return "\033[2m" + "  ·  ".join(parts) + "\033[0m"


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}

    event = data.get("hook_event_name")
    if event == "SessionEnd":
        apply(-1.0)            # no session -> reset cursor color -> hidden
        return
    if event == "SessionStart":
        apply(0.0)             # fresh start / resume / clear -> tiny seed hole
        return

    # statusLine: track the live context-window fill, print a built-in-style
    # line. The OSC write is plain terminal state — cheap enough to re-emit
    # on every refresh, which also self-heals anything else touching the
    # cursor color (a config reload, vi-mode indicators, ...). The level is
    # quantized to 1% so the hole's size holds steady between percent ticks
    # instead of creeping every refresh (steadiness only — no perf effect,
    # the shader redraws every frame either way).
    level = round(context_fill(data) * 100.0) / 100.0
    apply(level)
    print(status_line(data, level))


if __name__ == "__main__":
    main()
