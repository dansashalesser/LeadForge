# shellcheck shell=bash
# =============================================================================
# tool-paths.sh — locate globally-installed CLI tools by ASKING, never guessing
# =============================================================================
# Source from any script or hook:
#   __here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
#   . "$__here/../lib/tool-paths.sh"
#
#   BIN="$(find_tool headroom)"            # absolute path, or empty
#   PY="$(find_tool_python headroom-ai)"   # that tool's own interpreter
#   ensure_tool_bin_on_path                # fix PATH for THIS process
#   persist_tool_bin_on_path               # fix PATH durably, via uv itself
#
# Why this file exists
# --------------------
# `command -v headroom` is not a location check — it is a PATH check, and PATH in
# the environment install.sh/update.sh and Claude Code hooks run under is not the
# PATH of an interactive login shell. `uv tool install` drops its shims in a
# directory that is routinely absent from it, so a tool that is installed, running,
# and healthy reads as "not installed" and whole features skip themselves in silence.
#
# The fix is not to hardcode the directory. uv and pipx both report their own
# layout, and both are relocatable (UV_TOOL_DIR, UV_TOOL_BIN_DIR, PIPX_HOME,
# XDG_DATA_HOME, XDG_BIN_HOME). Ask them. Baking in `~/.local/share/uv/tools` means
# the harness works on the machine it was written on and quietly stops working on a
# clone whose user configured any of those — exactly the failure this file prevents.
#
# Cost: ~7ms per `uv tool dir` call, measured — cheap enough for a session-start hook.
#
# LAST RESORT ONLY: if the tool cannot be asked (not installed / not on PATH), the
# XDG Base Directory *specification defaults* are used. Those are a published
# standard, not one machine's layout, and every path below is derived from $HOME or
# an environment variable at runtime. Nothing here is a literal absolute path.
# =============================================================================

# Windows/Git Bash keep venv executables in Scripts/ with an .exe suffix.
_tp_exe_subdir() {
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) echo "Scripts" ;;
    *)                    echo "bin" ;;
  esac
}

_tp_exe_name() {
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) echo "${1}.exe" ;;
    *)                    echo "$1" ;;
  esac
}

# uv_tool_dir — where uv keeps each tool's venv. Asks uv; falls back to the
# documented env var, then the XDG default.
uv_tool_dir() {
  local d
  if command -v uv >/dev/null 2>&1 && d="$(uv tool dir 2>/dev/null)" && [ -n "$d" ]; then
    echo "$d"
    return 0
  fi
  if [ -n "${UV_TOOL_DIR:-}" ]; then
    echo "$UV_TOOL_DIR"
    return 0
  fi
  echo "${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools"
}

# uv_tool_bin_dir — where uv puts tool shims (the directory so often off PATH).
uv_tool_bin_dir() {
  local d
  if command -v uv >/dev/null 2>&1 && d="$(uv tool dir --bin 2>/dev/null)" && [ -n "$d" ]; then
    echo "$d"
    return 0
  fi
  if [ -n "${UV_TOOL_BIN_DIR:-}" ]; then
    echo "$UV_TOOL_BIN_DIR"
    return 0
  fi
  echo "${XDG_BIN_HOME:-$HOME/.local/bin}"
}

# pipx_venv_dir / pipx_bin_dir — same idea, asking pipx. Empty when pipx is absent.
pipx_venv_dir() {
  local d
  if command -v pipx >/dev/null 2>&1 && d="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null)" && [ -n "$d" ]; then
    echo "$d"
    return 0
  fi
  [ -n "${PIPX_HOME:-}" ] && { echo "$PIPX_HOME/venvs"; return 0; }
  return 1
}

pipx_bin_dir() {
  local d
  if command -v pipx >/dev/null 2>&1 && d="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null)" && [ -n "$d" ]; then
    echo "$d"
    return 0
  fi
  [ -n "${PIPX_BIN_DIR:-}" ] && { echo "$PIPX_BIN_DIR"; return 0; }
  return 1
}

# raindrop_bin_dir — raindrop's installer (curl raindrop.sh/install) has no
# "where am I" command, so its own documented home is the only source of truth.
# Overridable via RAINDROP_HOME; $HOME-relative otherwise, never a literal.
raindrop_bin_dir() {
  echo "${RAINDROP_HOME:-$HOME/.raindrop}/bin"
}

# cargo_bin_dir — same for Rust tooling. CARGO_HOME is cargo's documented override.
cargo_bin_dir() {
  echo "${CARGO_HOME:-$HOME/.cargo}/bin"
}

# brew_bin_dir — ASK brew for its prefix. Hardcoding /opt/homebrew works only on
# Apple Silicon and hardcoding /usr/local only on Intel; `brew --prefix` is right
# on both, plus any custom prefix. Empty when brew is absent.
brew_bin_dir() {
  local p
  if command -v brew >/dev/null 2>&1 && p="$(brew --prefix 2>/dev/null)" && [ -n "$p" ]; then
    echo "$p/bin"
    return 0
  fi
  return 1
}

# tool_bin_dirs — every directory a globally-installed CLI might live in, deduped.
tool_bin_dirs() {
  { uv_tool_bin_dir
    pipx_bin_dir 2>/dev/null || true
    brew_bin_dir 2>/dev/null || true
    raindrop_bin_dir
    cargo_bin_dir
  } | awk 'NF && !seen[$0]++'
}

# find_tool <name> — absolute path to a CLI, or empty with status 1.
# PATH first (respects whatever the user deliberately put there), then the
# directories the package managers report.
find_tool() {
  local name="$1" exe dir cand
  exe="$(_tp_exe_name "$name")"

  if cand="$(command -v "$name" 2>/dev/null)" && [ -n "$cand" ]; then
    echo "$cand"
    return 0
  fi

  while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    cand="$dir/$exe"
    if [ -x "$cand" ]; then
      echo "$cand"
      return 0
    fi
  done <<EOF
$(tool_bin_dirs)
EOF
  return 1
}

# find_tool_python <package-name> — the interpreter inside that tool's own venv.
# Needed when harness code must `import` a package that only the tool's environment
# has (e.g. utils/sync-memories-to-headroom.py importing headroom).
find_tool_python() {
  local pkg="$1" sub exe base cand
  sub="$(_tp_exe_subdir)"
  exe="$(_tp_exe_name python)"

  base="$(uv_tool_dir)"
  cand="$base/$pkg/$sub/$exe"
  [ -x "$cand" ] && { echo "$cand"; return 0; }

  if base="$(pipx_venv_dir 2>/dev/null)" && [ -n "$base" ]; then
    cand="$base/$pkg/$sub/$exe"
    [ -x "$cand" ] && { echo "$cand"; return 0; }
  fi
  return 1
}

# ensure_tool_bin_on_path — prepend discovered bin dirs to PATH for this process.
# Makes a script's own `command -v <tool>` calls agree with reality without
# depending on how the caller's shell was configured.
ensure_tool_bin_on_path() {
  local dir
  while IFS= read -r dir; do
    [ -n "$dir" ] && [ -d "$dir" ] || continue
    case ":$PATH:" in
      *":$dir:"*) ;;
      *) PATH="$dir:$PATH" ;;
    esac
  done <<EOF
$(tool_bin_dirs)
EOF
  export PATH
}

# persist_tool_bin_on_path — make the tool bin dir available in FUTURE shells.
# Delegates to `uv tool update-shell`, which is uv's own supported command for
# exactly this and edits whichever profile the user's shell actually reads. The
# harness does not write to shell rc files itself: guessing between .zshrc,
# .zprofile, .bashrc and .bash_profile is the same guessing this file exists to
# avoid. Idempotent.
#
# Exit codes are distinct so callers do not announce work that did not happen:
#   0 = wired it just now (tell the user to open a new shell)
#   2 = already on PATH, nothing to do (stay silent)
#   1 = could not (uv unavailable, or update-shell failed)
persist_tool_bin_on_path() {
  local bindir
  bindir="$(uv_tool_bin_dir)"
  case ":$PATH:" in
    *":$bindir:"*) return 2 ;;
  esac
  command -v uv >/dev/null 2>&1 || return 1
  uv tool update-shell >/dev/null 2>&1 || return 1
  return 0
}
