#!/usr/bin/env python3
"""Structural validator for a .claude/behaviors/<name>/BEHAVIOR.md spec.

Usage:
  python3 validate-behavior-spec.py <path-to-behavior-dir-or-BEHAVIOR.md>

Checks only structure (this is not a judgment call about spec quality —
that's writing-behavior-specs' job):
  - BEHAVIOR.md exists at the given path
  - Frontmatter is present and parses as `key: value` pairs
  - `name` and `description` fields are present and non-empty
  - `name` matches the parent directory name

Exit 0 on pass, 1 on failure. Prints one diagnostic per line to stderr on failure.
No third-party dependencies (stdlib only), unlike the upstream agentbehavior
npm CLI this ports from.
"""
import sys
from pathlib import Path


def load_frontmatter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm


def validate(target: Path) -> list[str]:
    errors = []

    behavior_md = target / "BEHAVIOR.md" if target.is_dir() else target
    behavior_dir = behavior_md.parent

    if not behavior_md.exists():
        return [f"error: {behavior_md} does not exist"]

    text = behavior_md.read_text(encoding="utf-8")
    fm = load_frontmatter(text)

    if not fm:
        errors.append(f"error: {behavior_md}: missing or malformed YAML frontmatter")
        return errors

    name = fm.get("name", "")
    description = fm.get("description", "")

    if not name:
        errors.append(f"error: {behavior_md}: frontmatter missing `name`")
    if not description:
        errors.append(f"error: {behavior_md}: frontmatter missing `description`")

    if name and name != behavior_dir.name:
        errors.append(
            f"error: {behavior_md}: frontmatter name '{name}' does not match "
            f"parent directory '{behavior_dir.name}'"
        )

    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate-behavior-spec.py <path-to-behavior-dir-or-BEHAVIOR.md>", file=sys.stderr)
        return 1

    target = Path(argv[1])
    errors = validate(target)

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1

    print(f"ok: {target} is a valid behavior spec")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
