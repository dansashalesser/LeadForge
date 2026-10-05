#!/bin/bash
# setup-buffer-hook.sh — PostToolUse(Bash)
# Accumulates setup-like commands to a per-session buffer.
# The stop-hook reads this buffer and writes to .claude/memory/setup-knowledge.md.

EVENT=$(cat)

CMD=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    print(e.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

[[ -z "$CMD" ]] && exit 0

# Skip multi-line commands (not typical atomic setup steps)
LINE_COUNT=$(echo "$CMD" | wc -l)
[[ "$LINE_COUNT" -gt 3 ]] && exit 0

# Detect setup-pattern commands
is_setup() {
  echo "$CMD" | grep -qiE \
    "^(pip3? install|npm install|npm ci|yarn install|yarn add|pnpm install|pnpm add|brew install|apt-get install|apt install|cargo build|go mod download|bundle install|composer install|gem install|uv sync|uv pip install|poetry install|conda install)" \
  || echo "$CMD" | grep -qiE \
    "^(docker[ -]compose|docker compose|docker build|docker pull)" \
  || echo "$CMD" | grep -qiE \
    "^(createdb|dropdb|psql .*(CREATE|DROP)|mysql )" \
  || echo "$CMD" | grep -qiE \
    "^(python.*manage\.py migrate|rails db:|alembic upgrade|prisma migrate|prisma db push)" \
  || echo "$CMD" | grep -qiE \
    "^(source [^ ]+\.env|cp [^ ]*\.env[^ ]* |export [A-Z_]+=)" \
  || echo "$CMD" | grep -qiE \
    "^(git clone|git submodule update|make install|make setup|make init|make bootstrap)"
}

if is_setup; then
  BUFFER=".claude/memory/.setup-session-buffer.log"
  mkdir -p "$(dirname "$BUFFER")" 2>/dev/null || true
  echo "$CMD" >> "$BUFFER"
fi

exit 0

# REGISTRATION (add to .claude/settings.json PostToolUse Bash hooks array):
# {
#   "type": "command",
#   "command": "bash .claude/hooks/setup-buffer-hook.sh"
# }
