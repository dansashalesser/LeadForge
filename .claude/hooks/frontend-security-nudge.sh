#!/usr/bin/env bash
# frontend-security-nudge.sh — UserPromptSubmit hook
#
# Detects when the user is about to work on frontend, UI, or design tasks and
# injects a reminder to invoke the secure-agent-design skill for security guidance.
# Fires on every user prompt; exits immediately if no keywords match (<5ms typical).
#
# REGISTRATION (in settings.json under "UserPromptSubmit"):
# {
#   "matcher": "",
#   "hooks": [{"type": "command", "command": "bash .claude/hooks/frontend-security-nudge.sh"}]
# }

set -euo pipefail

PROMPT_TEXT=$(python3 - 2>/dev/null <<'PYEOF'
import json, sys
try:
    d = json.load(sys.stdin)
    # UserPromptSubmit input has the prompt under "prompt" key
    text = d.get("prompt", "") or ""
    print(text.lower())
except Exception:
    pass
PYEOF
) || PROMPT_TEXT=""

[ -z "$PROMPT_TEXT" ] && exit 0

# Keyword groups — match if ANY keyword from ANY group appears
FRONTEND_KEYWORDS="react|vue|angular|svelte|nextjs|nuxt|remix|astro|solidjs|preact|htmx|alpine"
DESIGN_KEYWORDS="frontend|front-end|ui |ux |design|component|stylesheet|css|sass|scss|tailwind|bootstrap|figma|wireframe|mockup|layout|template|form |input |button|modal|page |view |render|widget|navbar|sidebar|header|footer|landing page|responsive"
BUILD_KEYWORDS="build a|create a|add a|implement a|write a|scaffold|set up"

# Check if this is a BUILD prompt (not just a question about)
is_build=false
if echo "$PROMPT_TEXT" | grep -qiE "$BUILD_KEYWORDS"; then
  is_build=true
fi

# Check frontend/design keywords
has_frontend=false
if echo "$PROMPT_TEXT" | grep -qiE "$FRONTEND_KEYWORDS|$DESIGN_KEYWORDS"; then
  has_frontend=true
fi

# Only nudge when BOTH conditions met: building something AND it's frontend/design
if [ "$is_build" = true ] && [ "$has_frontend" = true ]; then
  cat << 'NUDGE'
╔══ Security Nudge ══════════════════════════════════════════════════╗
║  Frontend/design work detected.                                   ║
║  Before building: invoke the `secure-agent-design` skill via the  ║
║  Skill tool for security patterns (XSS, injection, input safety,  ║
║  prompt injection if this feeds an agent).                        ║
║  → Skill("secure-agent-design")                                   ║
╚═══════════════════════════════════════════════════════════════════╝
NUDGE
fi

exit 0
