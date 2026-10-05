# Ghostty Blackhole — Setup

Renders a ray-traced black hole in your Ghostty terminal, sized to the current
Claude Code **context-window fill**. The hole grows as context fills — a live,
ambient gauge for context rot (see the harness "context rot past ~300k" rule).

Adapted from [s0xDk/ghostty-blackhole](https://github.com/s0xDk/ghostty-blackhole) (MIT).

## Requirements

- **Ghostty 1.3+** (cursor shader uniforms). Linux/macOS. Not for tmux/screen
  unless OSC passthrough is configured.
- Python 3 (already required by the harness).

## How it works

`blackhole-cursor.py` reads the JSON Claude pipes on stdin and encodes the
context fill into the **cursor color** via an `OSC 12` escape. `blackhole.glsl`
decodes that color every frame and sizes the hole. Cursor color is per-surface,
so each Ghostty split/window gets its own hole. No file rewrite, no reload.

One script, three roles (routed by `hook_event_name`):

| Role | Fires | Effect |
|---|---|---|
| `statusLine` | every assistant turn | set hole to live context fill, print a status line |
| `SessionStart` hook | start / resume / `/clear` | reset to tiny corner seed |
| `SessionEnd` hook | exit (`/exit`, `ctrl-d`) | `OSC 112` clears cursor → hole hidden |

## Opt-in (two switches)

This is **off by default**. Nothing draws a hole until BOTH are true:

1. **Wire the script** into `settings.json` (the install — step 2 below).
2. **Set `SDD_BLACKHOLE=1`** (the live on/off — step 3). Without it,
   `apply()` is a no-op: hooks do nothing and the statusLine still prints its
   readout but never touches the cursor. Unset it to turn the hole off again.

### 1. Point Ghostty at the shader

Add to `~/.config/ghostty/config` (adjust the path to this folder):

```
custom-shader = ~/.claude/scripts/integrations/blackhole/blackhole.glsl
custom-shader-animation = true
```

`blackhole.glsl` ships with `SIZE_MODE MODE_TOKENS` already set (the
Claude-context-fill mode). No edit needed.

### 2. Wire the script into `~/.claude/settings.json`

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/scripts/integrations/blackhole/blackhole-cursor.py"
  },
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "~/.claude/scripts/integrations/blackhole/blackhole-cursor.py" }] }],
    "SessionEnd":   [{ "hooks": [{ "type": "command", "command": "~/.claude/scripts/integrations/blackhole/blackhole-cursor.py" }] }]
  }
}
```

> Adding a `statusLine` **replaces** Claude's default status line with this
> script's readout (`⚫️ ██████░░░░ 61% · Fable 5 · …`). That is why this is
> not wired into the harness template — it only exists if you paste it.
> If you already have hooks, merge the `SessionStart`/`SessionEnd` arrays
> rather than overwriting them.

### 3. Turn it on

```bash
export SDD_BLACKHOLE=1   # add to ~/.bashrc / ~/.zshrc to persist
```

Start a new Claude Code session in Ghostty. The hole appears and grows with
context fill. `unset SDD_BLACKHOLE` (new terminal) turns it off.

## Tuning

Constants near the top of `blackhole.glsl` (`TOKEN_AREA_MIN/MAX`, `TOKEN_HOME_X/Y`,
`TOKEN_EASE`, `TOKEN_CALM/RUSH`) control size, corner home, growth curve, and
drift speed. `CURSOR_BASE` in the Python must stay in sync with `TOKEN_BASE_HI`
in the shader (both are the amber signature) — don't change one without the other.
