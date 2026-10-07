#!/usr/bin/env bash
# check-settings-json.sh <file> [<file> ...]
#
# Strict JSON validation for settings files and their templates.
# Claude Code parses .claude/settings.json as strict JSON: no comments, no
# trailing content after the closing brace. A malformed file is silently
# ignored — every permission rule and hook in it stops working — so the
# install/update paths validate before and after they write.
#
# Exit 0 = all files valid. Exit 1 = at least one file invalid.
set -uo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: check-settings-json.sh <file> [<file> ...]" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "  WARNING: python3 not found — skipping settings.json validation." >&2
  exit 0
fi

python3 - "$@" << 'PYEOF'
import json, sys

failed = False
for path in sys.argv[1:]:
    try:
        with open(path, 'r') as f:
            json.load(f)
    except FileNotFoundError:
        print(f"  INVALID: {path} — file not found")
        failed = True
    except json.JSONDecodeError as e:
        print(f"  INVALID: {path} — {e.msg} at line {e.lineno}, column {e.colno}")
        print("           JSON allows no comments and no content after the closing brace.")
        print("           Move notes to a sidecar settings.notes.md file.")
        failed = True

sys.exit(1 if failed else 0)
PYEOF
