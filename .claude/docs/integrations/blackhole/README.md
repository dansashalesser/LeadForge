# Ghostty Blackhole — Context-Fill Cursor Gauge (opt-in)

> An ambient, terminal-native gauge for context-window fill: a ray-traced black
> hole in your Ghostty window that grows as the Claude Code context fills up.
> **Off by default — two explicit opt-in switches.**

## What It Is

Adapted from [s0xDk/ghostty-blackhole](https://github.com/s0xDk/ghostty-blackhole) (MIT).

A single Python script (`blackhole-cursor.py`) reads the session JSON Claude
pipes to it and smuggles the context-fill level into the **cursor color** via an
`OSC 12` escape. A companion fragment shader (`blackhole.glsl`, `SIZE_MODE
MODE_TOKENS`) decodes that color every frame and renders a black hole sized to
the fill. Because cursor color is per-surface, every Ghostty split/window gets
its own hole, scoped to the session running there.

It exists because the harness already treats context rot as a first-class
concern (the "AI coherence degrades past ~300k tokens" rule, `context-budget`,
`context-degradation`). This makes that fill **visible** without a dashboard
glance — it's the live gauge, ambient in the terminal you're already looking at.

## How It Works

One script, three roles, routed by `hook_event_name` in the stdin JSON:

| Role | Fires | Effect |
|---|---|---|
| `statusLine` | every assistant turn | set hole to live context fill, print a status readout |
| `SessionStart` hook | start / resume / `/clear` | reset to tiny corner seed (level 0.0) |
| `SessionEnd` hook | exit (`/exit`, `ctrl-d`) | `OSC 112` clears cursor → hole hidden |

```
Claude session JSON ──stdin──▶ blackhole-cursor.py
                                  │  context_window.used_percentage → level 0..1
                                  │  encode amber + 16-bit signature
                                  ▼
                        OSC 12 cursor color  ──▶  Ghostty pty (per surface)
                                                     │
                                  blackhole.glsl ◀───┘ decodes iCurrentCursorColor
                                  every frame → sizes/moves the hole
```

Claude spawns statusLine/hook commands with **no controlling terminal**, so the
script finds the session's pty by walking its process ancestors
(`ps -o ppid=,tty=`) when `/dev/tty` fails.

## Opt-in Design (why it's off by default)

Two independent gates, both required:

1. **Wiring** — the `statusLine`/`SessionStart`/`SessionEnd` entries are **not**
   in the harness `settings.json` template. Adding a `statusLine` would replace
   Claude's default status line for every repo, so the snippet is something you
   paste manually (see SETUP.md). No paste → script never runs.
2. **Env gate** — `apply()` (the only function that writes to the cursor)
   returns immediately unless `SDD_BLACKHOLE=1`. So even if wired, it's dormant:
   hooks no-op and the statusLine prints its readout without touching the cursor.
   This is the live on/off switch — `export SDD_BLACKHOLE=1` to enable,
   `unset` to disable.

This belt-and-suspenders design means: nothing in a default harness install
draws a hole, costs a cursor write, or alters anyone's status line.

## Files

| File | Role |
|---|---|
| `scripts/integrations/blackhole/blackhole-cursor.py` | encoder; statusLine + SessionStart/SessionEnd; env-gated |
| `scripts/integrations/blackhole/blackhole.glsl` | Ghostty fragment shader (`SIZE_MODE MODE_TOKENS`) |
| `scripts/integrations/blackhole/SETUP.md` | full install + opt-in steps |

## Setup

See [`scripts/integrations/blackhole/SETUP.md`](../../../scripts/integrations/blackhole/SETUP.md)
for the Ghostty config line, the exact `settings.json` snippet, and the
`SDD_BLACKHOLE=1` toggle.

## Requirements

Ghostty 1.3+ (cursor shader uniforms), Python 3, Linux/macOS. Not for
tmux/screen unless OSC passthrough is configured.

## Caveats

- `CURSOR_BASE` (Python) and `TOKEN_BASE_HI` (shader) are the shared amber
  signature — change one only if you change the other.
- Toggling `SDD_BLACKHOLE` off mid-session leaves the cursor amber until a fresh
  terminal (SessionEnd clear also no-ops when disabled). Open a new terminal or
  emit `OSC 112` manually to reset immediately.
