#!/usr/bin/env python3
"""Guard: a Library-tier skill must never be invoked by bare name.

scripts/setup/sync-skills.sh installs every skill named in
scripts/setup/skill-library.txt to ~/.claude/skill-library/, which is NOT listed
in the session's skill index. The Skill tool cannot resolve those names, so a
command, agent, hook, routine prompt or another skill that says "invoke the
`foo` skill" fails in every repo the moment `foo` is moved to the library — the
agent correctly reports it as "not installed". That is how all three spec
approval gates lost `proof-collaborative-review` (2026-10-04).

Rule: on any line that mentions a skill, a Library-tier skill name must appear
with its installed path (`~/.claude/skill-library/<name>/SKILL.md`) on the same
line. Two valid fixes for a violation:
  1. Reference it by path — the agent reads the SKILL.md directly.
  2. Remove the name from skill-library.txt — it becomes Listed (costs
     per-prompt context; reserve for skills users invoke by hand).

Scope: hard-wired surfaces (commands, agents, hooks, kiro rules, scripts,
templates) are checked for every reference form. Inside skills/ only explicit
`Skill("name")` calls are checked — "related skill" mentions there are soft
pointers, and the runtime backstops (hooks/claude/skill-library-resolver.sh,
the [SKILL-LIBRARY] session-start line) resolve them. A skill mentioning its
own name inside its own directory is exempt. `#` comment lines in .sh/.py are
developer notes, never prompts, and are exempt. Other human-facing mentions
go in scripts/utils/skill-tier-allow.txt as "<path> <name>" pairs.

No regex (see no-regex memory): names are matched only in the explicit forms a
reference takes — `name`, "name", 'name', /name/, Skill(name, "name skill",
"invoke name".

Usage: python3 scripts/utils/check-skill-tiers.py   (from the harness repo root)
Exit 0 clean, 1 on violations, 2 on a missing manifest.
"""

import os
import sys

MANIFEST = "scripts/setup/skill-library.txt"
ALLOWLIST = "scripts/utils/skill-tier-allow.txt"
SURFACES = ("agents", "commands", "hooks", "kiro", "rules", "scripts", "skills", "templates")
# Files that mention skill names as data or human-facing docs, never as prompts.
SKIP_NAMES = ("README.md", "skill-library.txt", "no-regex-debt.txt", "check-skill-tiers.py",
              "skill-tier-allow.txt")
SKIP_SUFFIXES = (".test.sh", ".bak")
TEXT_SUFFIXES = (".md", ".sh", ".py", ".txt", ".json", ".yaml", ".yml", ".template", ".toml")
CODE_SUFFIXES = (".sh", ".py")


class ManifestMissing(Exception):
    pass


def load_library(root):
    path = os.path.join(root, MANIFEST)
    if not os.path.isfile(path):
        raise ManifestMissing(path)
    with open(path, encoding="utf-8") as fh:
        return {
            line.strip()
            for line in fh
            if line.strip() and not line.strip().startswith("#")
        }


def load_allowlist(root):
    path = os.path.join(root, ALLOWLIST)
    if not os.path.isfile(path):
        return set()
    pairs = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            fields = line.split()
            if len(fields) == 2 and not fields[0].startswith("#"):
                pairs.add((fields[0], fields[1]))
    return pairs


def reference_forms(name):
    return (
        "`" + name + "`",
        '"' + name + '"',
        "'" + name + "'",
        "/" + name + "/",
        "Skill(" + name,
        name + " skill",
        "nvoke " + name,
    )


def skill_call_forms(name):
    return ('Skill("' + name + '")', "Skill('" + name + "')", "Skill(" + name + ")")


def own_skill(rel_path):
    parts = rel_path.split(os.sep)
    if len(parts) > 1 and parts[0] == "skills":
        return parts[1]
    return None


def candidate_files(root):
    for surface in SURFACES:
        top = os.path.join(root, surface)
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git", "__pycache__")]
            for fname in filenames:
                if fname in SKIP_NAMES or fname.endswith(SKIP_SUFFIXES):
                    continue
                if not fname.endswith(TEXT_SUFFIXES):
                    continue
                yield os.path.join(dirpath, fname)


def scan_file(root, path, library, forms, allow):
    rel = os.path.relpath(path, root)
    own = own_skill(rel)
    # skills/ without a SKILL.md (vendored docs) is not a skill; inside a real
    # skill only explicit Skill(...) calls count — see module docstring.
    if own is not None and not os.path.isfile(os.path.join(root, "skills", own, "SKILL.md")):
        return []
    form_set = forms["call"] if own is not None else forms["ref"]
    is_code = path.endswith(CODE_SUFFIXES)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except UnicodeDecodeError:
        return []
    found = []
    for lineno, line in enumerate(lines, 1):
        if "skill" not in line and "Skill" not in line:
            continue
        if is_code and line.lstrip().startswith("#"):
            continue
        for name in library:
            if name == own or (rel, name) in allow:
                continue
            if "skill-library/" + name in line:
                continue
            if any(form in line for form in form_set[name]):
                found.append((rel, lineno, name, line.strip()))
    return found


def main():
    root = os.getcwd()
    try:
        library = load_library(root)
    except ManifestMissing as exc:
        print(f"check-skill-tiers: manifest not found: {exc}", file=sys.stderr)
        return 2
    allow = load_allowlist(root)
    forms = {
        "ref": {name: reference_forms(name) for name in library},
        "call": {name: skill_call_forms(name) for name in library},
    }
    violations = []
    for path in candidate_files(root):
        violations.extend(scan_file(root, path, library, forms, allow))
    if not violations:
        return 0
    print("check-skill-tiers: Library-tier skill(s) referenced by bare name — the")
    print("Skill tool cannot resolve these (they are not listed in any session):")
    for rel, lineno, name, text in sorted(violations):
        print(f"  {rel}:{lineno}: [{name}] {text[:140]}")
    print()
    print("Fix each: reference ~/.claude/skill-library/<name>/SKILL.md on that line,")
    print(f"or remove <name> from {MANIFEST} to make it a Listed skill.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
