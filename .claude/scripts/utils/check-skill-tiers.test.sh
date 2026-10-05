#!/bin/bash
# Functional tests for check-skill-tiers.py. Builds a throwaway harness tree.
#   bash scripts/utils/check-skill-tiers.test.sh
set -u
SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
GUARD="$SCRIPT_DIR/check-skill-tiers.py"
ROOT="$(mktemp -d)"
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "PASS: $1"; }
bad(){ FAIL=$((FAIL+1)); echo "FAIL: $1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want=$2 got=$3)"; fi }

mkdir -p "$ROOT/scripts/setup" "$ROOT/scripts/utils" "$ROOT/commands" "$ROOT/hooks" \
         "$ROOT/skills/lib-one" "$ROOT/skills/listed-one" "$ROOT/skills/docs"
printf '# comment\nlib-one\n' > "$ROOT/scripts/setup/skill-library.txt"
echo body > "$ROOT/skills/lib-one/SKILL.md"
echo body > "$ROOT/skills/listed-one/SKILL.md"

run(){ (cd "$ROOT" && python3 "$GUARD" >/dev/null 2>&1; echo $?); }
reset(){ rm -f "$ROOT/commands/"* "$ROOT/hooks/"* "$ROOT/scripts/utils/skill-tier-allow.txt" "$ROOT/skills/docs/"*; echo body > "$ROOT/skills/listed-one/SKILL.md"; }

check "clean tree passes"                     "0" "$(run)"

printf 'Invoke the `lib-one` skill with:\n' > "$ROOT/commands/a.md"
check "bare backticked invocation fails"      "1" "$(run)"; reset

printf 'echo "Use the lib-one skill to sweep"\n' > "$ROOT/hooks/a.sh"
check "bare 'name skill' in a hook fails"     "1" "$(run)"; reset

printf 'skill gate: invoke lib-one and verify\n' > "$ROOT/hooks/b.sh"
check "bare 'invoke name' fails"              "1" "$(run)"; reset

printf 'Read `~/.claude/skill-library/lib-one/SKILL.md` and follow the skill.\n' > "$ROOT/commands/a.md"
check "library path reference passes"         "0" "$(run)"; reset

printf '# see the lib-one skill for details\n' > "$ROOT/hooks/c.sh"
check "shell comment line is exempt"          "0" "$(run)"; reset

printf 'Related skill: `lib-one` covers this.\n' >> "$ROOT/skills/listed-one/SKILL.md"
check "soft mention inside a skill passes"    "0" "$(run)"; reset

printf 'Invoke `Skill("lib-one")` now.\n' >> "$ROOT/skills/listed-one/SKILL.md"
check "Skill(\"name\") inside a skill fails"   "1" "$(run)"; reset

printf 'Invoke `Skill("lib-one")` on yourself.\n' >> "$ROOT/skills/lib-one/SKILL.md"
check "own-name mention is exempt"            "0" "$(run)"
echo body > "$ROOT/skills/lib-one/SKILL.md"

printf 'see the `lib-one` skill\n' > "$ROOT/skills/docs/BUNDLES.md"
check "skills/ dir without SKILL.md skipped"  "0" "$(run)"; reset

printf 'Invoke the `lib-one` skill.\n' > "$ROOT/commands/a.md"
printf 'commands/a.md lib-one\n' > "$ROOT/scripts/utils/skill-tier-allow.txt"
check "allowlisted pair passes"               "0" "$(run)"; reset

printf 'Invoke the `listed-one` skill.\n' > "$ROOT/commands/a.md"
check "listed skill by name passes"           "0" "$(run)"; reset

rm "$ROOT/scripts/setup/skill-library.txt"
check "missing manifest exits 2"              "2" "$(run)"

echo ""; echo "PASS=$PASS FAIL=$FAIL"
rm -rf "$ROOT"
[ "$FAIL" -eq 0 ]
