#!/bin/bash
# lean-ctx nudge hook — dual-mode: PreToolUse(Read) hard gate + PostToolUse(Write|Edit) soft nudge.
#
# PreToolUse(Read): blocks a native Read on a large file before it spends
# context, naming the ctx_read mode to use instead (hard deny).
# PostToolUse(Write|Edit): unchanged soft suggestion after editing a large
# file — advisory only, since the write already happened.
#
# Threshold: ~16 KB on disk ≈ 4,000 tokens (@ ~4 chars/token).
# Exits silently for small files, non-existent paths, and data formats
# that lean-ctx intentionally skips (JSON, YAML, TOML).
#
# Escape hatch: LEAN_CTX_NUDGE_WARN_ONLY=1 downgrades the PreToolUse block to
# a warning (matches the LEAN_CTX_ALLOWLIST_WARN_ONLY convention used by the
# shell allowlist) — for the rare legitimate case where native Read+Edit is
# genuinely the right path instead of ctx_read(mode="anchored") → ctx_patch.

set -u

EVENT_FILE="$(mktemp)"
cat > "$EVENT_FILE"
trap 'rm -f "$EVENT_FILE"' EXIT

EVENT_FILE="$EVENT_FILE" python3 - <<'PY'
import json, os, sys

try:
    e = json.load(open(os.environ["EVENT_FILE"]))
except Exception:
    sys.exit(0)

HOOK_EVENT = e.get("hook_event_name", "")
TOOL_NAME = e.get("tool_name", "")
FILE_PATH = e.get("tool_input", {}).get("file_path", "")

if not FILE_PATH or not os.path.isfile(FILE_PATH):
    sys.exit(0)

FILE_BYTES = os.path.getsize(FILE_PATH)
THRESHOLD = 16000  # ~4,000 tokens

if FILE_BYTES < THRESHOLD:
    sys.exit(0)

EXT = FILE_PATH.rsplit(".", 1)[-1].lower() if "." in FILE_PATH else ""

# lean-ctx skips data formats — don't nudge/block for those
if EXT in ("json", "yaml", "yml", "toml", "env", "lock", "sum"):
    sys.exit(0)

TOKENS = FILE_BYTES // 4
BASENAME = os.path.basename(FILE_PATH)

CODE_EXT = {"py", "ts", "tsx", "js", "jsx", "go", "rs", "java", "cpp", "c", "h",
            "rb", "swift", "kt", "scala", "cs", "sh", "bash", "zsh"}
PROSE_EXT = {"md", "txt", "rst", "mdx", "wiki"}

if EXT in CODE_EXT:
    MODE = "signatures"
    NOTE = "exports + types only — typically 3-5% of full-file tokens"
elif EXT in PROSE_EXT:
    MODE = "reference"
    NOTE = "quote-ready excerpts — removes boilerplate, keeps key passages"
else:
    MODE = "aggressive"
    NOTE = "maximum compression for unknown type"

WARN_ONLY = os.environ.get("LEAN_CTX_NUDGE_WARN_ONLY", "") == "1"

if HOOK_EVENT == "PreToolUse" and TOOL_NAME == "Read" and not WARN_ONLY:
    reason = (
        f"lean-ctx gate: {BASENAME} is ~{TOKENS} tokens on disk — native Read blocked. "
        f'Use ctx_read("{FILE_PATH}", "{MODE}") instead ({NOTE}). '
        f'To edit this file: ctx_read("{FILE_PATH}", "anchored") then ctx_patch — '
        f"the primary edit path already avoids this cost. "
        f"Set LEAN_CTX_NUDGE_WARN_ONLY=1 to downgrade this to a warning."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)

# PostToolUse(Write|Edit), or WARN_ONLY escape hatch — soft banner, never blocks.
print(f"""╔══ lean-ctx Opportunity (~{TOKENS} tokens) ═════════════════════════╗
║  {BASENAME} is large. Consider:
║
║    ctx_read("{FILE_PATH}", "{MODE}")
║    # {NOTE}
║
║  Other modes:
║    "map"       — dependency list + exports (no implementation)
║    "entropy"   — high-signal fragments, attention-mimicking filter
║    "task"      — filter to active task context (best precision)
║    "diff"      — only changes since last read (re-checks after edits)
║    "lines:N-M" — specific range if you know where to look
║
║  Re-read after edits: ctx_delta("{FILE_PATH}") ≈ 13 tokens
╚═══════════════════════════════════════════════════════════════════╝""")
PY

exit 0

# REGISTRATION (settings.json) — add BOTH entries:
# "PreToolUse": [
#   { "matcher": "Read",
#     "hooks": [ { "type": "command", "command": "bash .claude/hooks/lean-ctx-nudge-hook.sh" } ] }
# ]
# "PostToolUse": [
#   { "matcher": "Write|Edit",
#     "hooks": [ { "type": "command", "command": "bash .claude/hooks/lean-ctx-nudge-hook.sh" } ] }
# ]
# (the PostToolUse|Write|Edit entry already exists in templates/settings.json.template —
# only the PreToolUse|Read entry is new)
