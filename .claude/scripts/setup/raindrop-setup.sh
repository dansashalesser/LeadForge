#!/usr/bin/env bash
# raindrop-setup.sh — wire Raindrop Workshop into all registered repos
#
# Run automatically by install.sh and update.sh. Safe to run manually anytime.
# Idempotent — running multiple times has no side effects.
#
# What this does:
#   1. Adds RAINDROP_LOCAL_DEBUGGER to ~/.claude/settings.json (Claude env)
#   2. Adds RAINDROP_LOCAL_DEBUGGER export to ~/.bashrc (user shell processes)
#   3. (raindrop-ai repo installs moved to scripts/setup/check-harness-deps.sh,
#       which also declares it in each repo's manifest so prunes stop deleting it)
#   4. Auto-instruments repos that have the SDK but no raindrop.begin calls
#
# Nothing in any repo's .env file is touched.

set -e

# Self-locate the harness root via the shared resolver — symlink/junction-safe,
# resolves to the real physical path, works on any machine/OS/clone location.
# Single source of truth: lib/resolve-harness-dir.sh. No hardcoded paths.
__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/../lib/resolve-harness-dir.sh"
# repo_has_module / _rv_timeout — the auto-instrument gate below depends on both.
. "$__here/../lib/repo-venv.sh"
# find_tool — locates the raindrop CLI without depending on the caller's PATH.
. "$__here/../lib/tool-paths.sh"
PROJECTS_FILE="$HARNESS_DIR/projects.txt"
GLOBAL_SETTINGS="$HOME/.claude/settings.json"
RAINDROP_URL="http://localhost:5899/v1/"

# ── 1. Inject env var into ~/.claude/settings.json ─────────────────────────
if [ -f "$GLOBAL_SETTINGS" ]; then
    python3 - "$GLOBAL_SETTINGS" "$RAINDROP_URL" <<'PYEOF'
import json, sys
path, url = sys.argv[1], sys.argv[2]
with open(path) as f:
    d = json.load(f)
d.setdefault("env", {})
if d["env"].get("RAINDROP_LOCAL_DEBUGGER") == url:
    print("  RAINDROP_LOCAL_DEBUGGER already set in ~/.claude/settings.json")
else:
    d["env"]["RAINDROP_LOCAL_DEBUGGER"] = url
    with open(path, "w") as f:
        json.dump(d, f, indent=2)
    print("  Added RAINDROP_LOCAL_DEBUGGER to ~/.claude/settings.json")
PYEOF
else
    echo "  WARNING: ~/.claude/settings.json not found — skipping global env injection"
fi

# ── 2. Inject env var into ~/.bashrc (for user-run processes) ───────────────
BASHRC="$HOME/.bashrc"
if [ -f "$BASHRC" ]; then
    if grep -q "RAINDROP_LOCAL_DEBUGGER" "$BASHRC" 2>/dev/null; then
        echo "  RAINDROP_LOCAL_DEBUGGER already in ~/.bashrc"
    else
        printf '\n# Raindrop Workshop (added by sdd-harness raindrop-setup.sh)\nexport RAINDROP_LOCAL_DEBUGGER=%s\n' "$RAINDROP_URL" >> "$BASHRC"
        echo "  Added RAINDROP_LOCAL_DEBUGGER to ~/.bashrc"
    fi
fi

# ── 3. raindrop-ai repo installs — owned by check-harness-deps.sh ───────────
# This script used to pip-install raindrop-ai into every registered repo's venv
# and write nothing to that repo's manifest. Undeclared, the package looked like
# an orphan to the repo's own tooling, so any `uv sync` / lockfile regen /
# dependency prune deleted it — and every install failure was hidden behind
# `-q 2>/dev/null`, so the script printed "Done." either way.
#
# raindrop-ai now lives in scripts/setup/repo-requirements.txt, and
# check-harness-deps.sh both installs AND declares it, reporting each outcome.
# install.sh and update.sh run that check *before* this script, so the SDK is
# already in place for the auto-instrument pass below.
if [ ! -s "$PROJECTS_FILE" ]; then
    echo "  No registered projects found — skipping venv checks."
    exit 0
fi
echo "  raindrop-ai repo installs handled by check-harness-deps.sh (installed + declared)."

# ── 4. Auto-instrument repos that have the SDK but no raindrop.begin calls ───
maybe_auto_instrument() {
    local repo="$1"

    if [ ! -d "$repo" ]; then return; fi

    # Skip if already instrumented (time limit avoids slow WSL2/Windows filesystem greps).
    # _rv_timeout, not bare `timeout`: macOS ships no `timeout` at all, so every probe
    # in this function used to exit 127 there — read as "not instrumented" by the grep
    # and as "SDK missing" by the import checks, which meant auto-instrument silently
    # never ran on a Mac. See lib/repo-venv.sh.
    local grep_rc=0
    _rv_timeout 20 grep -rq "raindrop.begin" "$repo" --include="*.py" --include="*.ts" 2>/dev/null || grep_rc=$?
    if [ "$grep_rc" -eq 0 ]; then
        echo "  $(basename "$repo"): already instrumented — skipping auto-instrument"
        return
    fi
    if [ "$grep_rc" -eq 124 ]; then
        echo "  $(basename "$repo"): grep timed out — skipping auto-instrument"
        return
    fi

    # Only proceed if the SDK is importable in the repo's own environment.
    if ! repo_has_module "$repo" "raindrop.analytics"; then
        echo "  $(basename "$repo"): SDK not importable — skipping auto-instrument"
        return
    fi

    local log_file="/tmp/raindrop-instrument-$(basename "$repo").log"
    local prompt="Read ~/.claude/skill-library/instrument-agent/SKILL.md and follow it to instrument this repository for Raindrop Workshop.
Repository path: $repo
Rules:
- Use your best judgment to identify the main agent entry point (the function/route that triggers an LLM call)
- Do NOT ask clarifying questions — make a decision and instrument it
- Set event name equal to the repo basename: $(basename "$repo")
- Set RAINDROP_LOCAL_DEBUGGER=$RAINDROP_URL via env var (already injected; do not touch .env files)
- Add raindrop.begin() at the entry point and interaction.finish() at all exit paths
- Add set_properties() with any useful per-request metadata you find (user_id, session_id, etc.)
- If you cannot find a clear entry point after exploring the repo, write a single line to $log_file explaining why and exit cleanly"

    echo "  $(basename "$repo"): SDK found but not instrumented — spawning auto-instrument agent..."
    echo "    Log: $log_file"
    RAINDROP_LOCAL_DEBUGGER="$RAINDROP_URL" nohup claude --print --permission-mode bypassPermissions "$prompt" > "$log_file" 2>&1 &
    echo "    Agent PID $! running in background"
}

echo ""
# This pass spawns background `claude --print --permission-mode bypassPermissions`
# agents that edit repo source unattended. It was dead on macOS until the timeout
# shim above; opt out with SDD_SKIP_AUTO_INSTRUMENT=1 if you would rather instrument
# by hand.
if [ "${SDD_SKIP_AUTO_INSTRUMENT:-0}" = "1" ]; then
    echo "  Auto-instrumentation skipped (SDD_SKIP_AUTO_INSTRUMENT=1)."
elif ! command -v claude >/dev/null 2>&1; then
    echo "  Auto-instrumentation skipped — claude CLI not on PATH."
else
    echo "  Checking repos for auto-instrumentation..."
    while IFS= read -r project || [ -n "$project" ]; do
        [ -n "$project" ] && maybe_auto_instrument "$project"
    done < "$PROJECTS_FILE"
fi

echo ""
echo "Raindrop setup complete."
echo "  Reload your shell or run: source ~/.bashrc"
if [ -z "$(find_tool raindrop || true)" ]; then
  echo "  Raindrop CLI not found — install via bootstrap.sh or:"
  echo "    curl -fsSL https://raindrop.sh/install | bash"
fi
echo "  Auto-instrument logs: /tmp/raindrop-instrument-<repo>.log"
