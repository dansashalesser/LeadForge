#!/usr/bin/env bash
# headroom-setup.sh — install headroom-ai and wire automatic context compression
#
# Run automatically by install.sh and update.sh. Safe to run manually anytime.
# Idempotent — running multiple times has no side effects.
#
# What this does:
#   1. Installs headroom-ai globally (uv tool, pipx, or pip --user — first that works)
#   2. Installs headroom-ai in each registered repo's detected virtualenv (for Python API use)
#   3. Wires Claude Code to route through the proxy (durable, all shells + GUI)
#   4. Installs headroom as a persistent service (launchd on macOS, systemd on Linux)
#
# Why: headroom compresses prompts/messages at the process level (60-95% savings).
# Complementary to RTK (shell output compression) — operates on a different layer.
# Persistent service keeps the proxy warm so `claude` opens in ~1s instead of 10s+.

set -e

__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/../lib/resolve-harness-dir.sh"
# No lib/repo-venv.sh and no projects.txt: headroom is machine-level only (see § 3).
# tool-paths.sh resolves the headroom binary without depending on the caller's PATH.
. "$__here/../lib/tool-paths.sh"
ensure_tool_bin_on_path
BASHRC="$HOME/.bashrc"
HEADROOM_PKG="headroom-ai"
MARKER="# headroom wrap (added by sdd-harness headroom-setup.sh)"

# ── 1. Install headroom globally so `headroom` is on PATH ──────────────────
echo "  Installing headroom-ai globally..."
INSTALLED_GLOBALLY=0

if command -v uv >/dev/null 2>&1; then
    # headroom-ai is a Rust extension (maturin). No pre-built wheel for Python 3.14+,
    # so try 3.12 first where binary wheels exist, then fall back to default.
    # Install with headroom-ai's own `[proxy]` extra rather than the hand-maintained
    # headroom-extras.txt requirements list this used to pass via --with-requirements:
    # that list drifted behind the actual proxy dependency tree (missing `mcp`, then
    # `magika`), each gap producing a silent launchd crash-loop — exit 1, zero
    # stdout/stderr — that took hours to trace to "No module named 'X'" buried in
    # the deployment's own runner.log. `[proxy]` is declared by the package itself,
    # so it tracks whatever the proxy actually needs release over release.
    if uv tool install "$HEADROOM_PKG[proxy]" --python 3.12 -q 2>/dev/null \
       || uv tool install "$HEADROOM_PKG[proxy]" -q 2>/dev/null; then
        echo "    Installed via uv tool."
        INSTALLED_GLOBALLY=1
    fi
fi

if [ "$INSTALLED_GLOBALLY" -eq 0 ] && command -v pipx >/dev/null 2>&1; then
    if pipx install "$HEADROOM_PKG[proxy]" -q 2>/dev/null || pipx upgrade "$HEADROOM_PKG[proxy]" -q 2>/dev/null; then
        echo "    Installed via pipx."
        INSTALLED_GLOBALLY=1
    fi
fi

if [ "$INSTALLED_GLOBALLY" -eq 0 ]; then
    if pip install --user "$HEADROOM_PKG[proxy]" -q 2>/dev/null; then
        echo "    Installed via pip --user."
        INSTALLED_GLOBALLY=1
    fi
fi

# ── 1b. Resolve the headroom binary WITHOUT relying on PATH ────────────────
# `uv tool install` drops its shim in the directory uv reports as its tool-bin dir,
# which is routinely absent from PATH in non-login shells and in the environment
# install.sh/update.sh run under. Section 4 below used to gate the persistent service
# AND the Claude routing on a bare `command -v headroom`, so on such a machine the
# whole block was skipped in silence — headroom stayed unrouted and nothing said so.
#
# lib/tool-paths.sh asks uv/pipx where they put things instead of naming directories,
# so this resolves correctly on any clone regardless of how the user configured
# UV_TOOL_DIR / XDG_BIN_HOME / PIPX_HOME.
HEADROOM_BIN="$(find_tool headroom || true)"
if [ -n "$HEADROOM_BIN" ] && ! command -v headroom >/dev/null 2>&1; then
    # Reported, not fixed here: install.sh/update.sh own the one durable PATH repair
    # (persist_tool_bin_on_path), so it happens once per run instead of as a side
    # effect buried in a headroom-specific script.
    echo "  headroom resolved at $HEADROOM_BIN (its directory is not on PATH)."
fi

if [ "$INSTALLED_GLOBALLY" -eq 0 ] && [ -n "$HEADROOM_BIN" ]; then
    # Reinstall failed but a working binary is already there — not a problem.
    INSTALLED_GLOBALLY=1
fi

if [ "$INSTALLED_GLOBALLY" -eq 0 ]; then
    echo "  WARNING: could not install headroom-ai globally. Install manually:"
    echo "    uv tool install headroom-ai   OR   pip install --user headroom-ai"
fi

# ── 2. Retire the legacy wrap alias (routing is now durable — see section 4) ─
# Older installs added `alias claude='headroom wrap claude'` to ~/.bashrc. That is
# fragile (bash-only; macOS default shell is zsh, so it never loaded) and it fights
# the persistent service for the same port. Durable settings.json routing (wired in
# section 4, only after the proxy is confirmed healthy) replaces it on every shell.
for RC in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [ -f "$RC" ] && grep -q "headroom wrap" "$RC" 2>/dev/null; then
        sed -i.bak -e "/headroom wrap (added by sdd-harness/d" -e "/alias claude=.*headroom wrap/d" "$RC" && rm -f "$RC.bak"
        echo "  Removed legacy headroom wrap alias from $(basename "$RC") (durable routing supersedes it)."
    fi
done

# ── 3. Per-repo venv install — removed deliberately ────────────────────────
# This script used to pip-install headroom-ai into every registered repo's venv.
# Two measurements retired it:
#
#   1. It never once succeeded. Across all four registered repos, zero had
#      headroom-ai in site-packages — the install was hidden behind
#      `-q 2>/dev/null` and had been failing silently since it was written.
#   2. Nothing needed it to. headroom works entirely at machine level:
#      ANTHROPIC_BASE_URL in the user's shell rc plus the launchd/systemd proxy
#      service. The harness's only Python consumer, utils/sync-memories-to-headroom.py,
#      runs under headroom's OWN interpreter, never a repo's.
#
# So the feature was dead code that happened to be harmless — until the venv
# discovery in lib/repo-venv.sh started working, at which point it would have begun
# installing a maturin-built package plus its dependency tree into repos that never
# import it, undeclared and therefore prunable. Bloat with no consumer.
#
# If a repo ever genuinely needs `import headroom` in its own source, add headroom-ai
# to scripts/setup/repo-requirements.txt — that path installs AND declares it.
# Machine-level health (binary resolvable, proxy listening, routing wired) is
# reported by scripts/setup/check-harness-deps.sh.

# ── 4. Install headroom as a persistent service ─────────────────────────────
# Keeps the proxy warm between sessions — eliminates cold-start delay (~10s → ~1s).
# Only runs when headroom is installed and a service supervisor is available.
# Idempotent: skips if service already installed and healthy.
if [ -n "$HEADROOM_BIN" ]; then
    if "$HEADROOM_BIN" install status >/dev/null 2>&1; then
        echo "  Headroom persistent service already installed."
    else
        # `headroom install apply` auto-detects the supervisor:
        #   macOS -> launchd LaunchAgent, Linux -> systemd user service.
        # The first start loads the compression model and can miss the readiness
        # window, so retry once before giving up.
        case "$(uname -s)" in
            Darwin|Linux)
                echo "  Installing headroom persistent service ($(uname -s))..."
                if "$HEADROOM_BIN" install apply --preset persistent-service --memory \
                   || { echo "  First start missed readiness (model warm-up) — retrying..."; sleep 3; "$HEADROOM_BIN" install apply --preset persistent-service --memory; }; then
                    echo "  Headroom persistent service installed and started (auto-starts on login)."
                    # Route Claude Code through the proxy ONLY after it is confirmed
                    # healthy, so ANTHROPIC_BASE_URL never points at a dead proxy.
                    # Generous limit plus one retry: /readyz answers instantly when
                    # warm but can exceed 8s while the compression model loads, and a
                    # false negative here silently leaves routing unwired.
                    if curl -fsS --connect-timeout 3 -m 25 "http://127.0.0.1:${HEADROOM_PORT:-8787}/readyz" >/dev/null 2>&1 \
                       || { sleep 3; curl -fsS --connect-timeout 3 -m 25 "http://127.0.0.1:${HEADROOM_PORT:-8787}/readyz" >/dev/null 2>&1; }; then
                        if "$HEADROOM_BIN" init --global --memory claude >/dev/null 2>&1; then
                            echo "  Claude Code routed through headroom (durable, all shells + GUI)."
                        else
                            echo "  WARNING: routing not wired. Run manually: headroom init --global --memory claude"
                        fi
                    else
                        echo "  WARNING: proxy not healthy yet — skipped routing to avoid breaking Claude API calls."
                        echo "    Once healthy, run: headroom init --global --memory claude"
                    fi
                else
                    echo "  WARNING: persistent service install failed after retry — routing NOT wired, cold starts ~10s."
                    echo "    Fix manually: headroom install apply --preset persistent-service --memory"
                fi ;;
            *)
                echo "  Skipping persistent service (unsupported OS: $(uname -s))." ;;
        esac
    fi
else
    echo "  Skipping persistent service (headroom binary not found — see WARNING above)."
fi

echo ""
echo "Headroom setup complete."
echo "  Reload shell or run: source ~/.bashrc"
echo "  Verify: headroom verify"
