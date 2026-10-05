#!/bin/bash
# Functional tests for sync-skills.sh. No network; builds a throwaway harness + HOME.
#   bash scripts/setup/sync-skills.test.sh
set -u
SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SYNC="$SCRIPT_DIR/sync-skills.sh"
ROOT="$(mktemp -d)"
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "PASS: $1"; }
bad(){ FAIL=$((FAIL+1)); echo "FAIL: $1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want=$2 got=$3)"; fi }

H="$ROOT/harness"; mkdir -p "$H/skills" "$H/scripts/setup"
for s in master-a kept-skill lib-one lib-two; do
  mkdir -p "$H/skills/$s"; printf -- '---\nname: %s\n---\nbody\n' "$s" > "$H/skills/$s/SKILL.md"
done
printf '# comment\n\nlib-one\nlib-two\n' > "$H/scripts/setup/skill-library.txt"
cp "$SYNC" "$H/scripts/setup/sync-skills.sh"

export HOME="$ROOT/home"; mkdir -p "$HOME"
# pre-seed a stale listed copy of a library skill to prove eviction
mkdir -p "$HOME/.claude/skills/lib-one"; echo stale > "$HOME/.claude/skills/lib-one/SKILL.md"

bash "$H/scripts/setup/sync-skills.sh" "$H" >/dev/null

check "master listed"        "1" "$([ -f "$HOME/.claude/skills/master-a/SKILL.md" ] && echo 1 || echo 0)"
check "kept skill listed"    "1" "$([ -f "$HOME/.claude/skills/kept-skill/SKILL.md" ] && echo 1 || echo 0)"
check "lib-one in library"   "1" "$([ -f "$HOME/.claude/skill-library/lib-one/SKILL.md" ] && echo 1 || echo 0)"
check "lib-two in library"   "1" "$([ -f "$HOME/.claude/skill-library/lib-two/SKILL.md" ] && echo 1 || echo 0)"
check "stale listed evicted" "0" "$([ -e "$HOME/.claude/skills/lib-one" ] && echo 1 || echo 0)"
check "lib not in skills"    "0" "$([ -e "$HOME/.claude/skills/lib-two" ] && echo 1 || echo 0)"
check "listed count"         "2" "$(ls "$HOME/.claude/skills" | wc -l | tr -d ' ')"
check "library count"        "2" "$(ls "$HOME/.claude/skill-library" | wc -l | tr -d ' ')"

# no manifest -> everything listed (backward compat)
rm "$H/scripts/setup/skill-library.txt"; rm -rf "$HOME/.claude"
bash "$H/scripts/setup/sync-skills.sh" "$H" >/dev/null
check "no-manifest all listed" "4" "$(ls "$HOME/.claude/skills" | wc -l | tr -d ' ')"
check "no-manifest no library" "0" "$([ -d "$HOME/.claude/skill-library" ] && ls "$HOME/.claude/skill-library" | wc -l | tr -d ' ' || echo 0)"

echo ""; echo "PASS=$PASS FAIL=$FAIL"
rm -rf "$ROOT"
[ "$FAIL" -eq 0 ]
