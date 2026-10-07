#!/usr/bin/env python3
"""declare-repo-deps.py — declare harness-injected packages in a target repo's manifest.

Why this exists
---------------
raindrop-setup.sh and headroom-setup.sh used to `pip install <pkg>` straight into a
target repo's virtualenv and write nothing to that repo's manifest. The package was
therefore present but undeclared, so the repo's own tooling had no idea it was
wanted: one `uv sync`, lockfile regen, or dependency prune deleted it. The harness
reinstalled it on the next update, the next prune deleted it again, forever.

Declaring the package in the repo's manifest breaks that loop — a prune now sees a
declared dependency instead of an orphan.

What it writes
--------------
pyproject.toml  ->  [dependency-groups] harness = [...]   (PEP 735)
requirements.txt ->  requirements-harness.txt + a `-r` line referencing it
neither          ->  skipped (not a Python repo)

Idempotent: re-running unions the package list, never duplicates it. If the edit
would produce invalid TOML the original file is restored and the script fails
loudly rather than leaving a broken manifest behind.

Usage:  declare-repo-deps.py <repo-path> <pkg-spec> [<pkg-spec> ...]
Exit:   0 = declared or already present or skipped, 1 = failed to write
"""

from __future__ import annotations

import sys
from pathlib import Path

GROUP_NAME = "harness"
HARNESS_REQ_FILE = "requirements-harness.txt"
MARKER = "# managed by sdd-harness (scripts/setup/declare-repo-deps.py) — do not hand-prune"


_SPEC_DELIMITERS = frozenset("<>=!~[; ")


def _pkg_name(spec: str) -> str:
    """Bare distribution name from a pip spec ('raindrop-ai>=1.2' -> 'raindrop-ai')."""
    for i, ch in enumerate(spec):
        if ch in _SPEC_DELIMITERS:
            return spec[:i].strip()
    return spec.strip()


def _iter_lines(text: str, begin: int = 0):
    """Yield (start_offset, end_offset, line_without_newline) from `begin` onward."""
    pos = begin
    while pos <= len(text):
        nl = text.find("\n", pos)
        end = len(text) if nl == -1 else nl
        yield pos, end, text[pos:end]
        if nl == -1:
            return
        pos = nl + 1


def _section_header_end(text: str, header: str) -> int | None:
    """Offset just past the `[header]` line, or None when the section is absent."""
    for _, end, line in _iter_lines(text):
        if line.strip() == header:
            return end
    return None


def _quoted_strings(text: str) -> list[str]:
    """Contents of each non-empty '…' or \"…\" literal, in order."""
    found = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            close = text.find(ch, i + 1)
            if close == -1:
                break
            if close > i + 1:
                found.append(text[i + 1:close])
            i = close + 1
            continue
        i += 1
    return found


def _validate_toml(text: str) -> bool:
    try:
        import tomllib
    except ImportError:
        return True  # Python <3.11: cannot verify, trust the edit
    try:
        tomllib.loads(text)
        return True
    except Exception:
        return False


def _existing_group(text: str) -> tuple[int, int, list[str]] | None:
    """Locate `harness = [...]` inside [dependency-groups].

    Returns (start_offset, end_offset, current_entries) or None when absent.
    """
    section_end = _section_header_end(text, "[dependency-groups]")
    if section_end is None:
        return None

    # Section body ends at the next top-level table header, or EOF.
    body_end = len(text)
    for start, _, line in _iter_lines(text, min(section_end + 1, len(text))):
        if line.startswith("["):
            body_end = start
            break

    for start, _, line in _iter_lines(text, min(section_end + 1, len(text))):
        if start >= body_end:
            break
        if not line.startswith(GROUP_NAME):
            continue
        after_name = line[len(GROUP_NAME):].lstrip()
        if not after_name.startswith("="):
            continue
        after_eq = after_name[1:].lstrip()
        if not after_eq.startswith("["):
            continue
        bracket = start + len(line) - len(after_eq)
        close = text.find("]", bracket)
        if close == -1:
            raise ValueError("unterminated dependency-groups array in pyproject.toml")
        end = close + 1
        return start, end, _quoted_strings(text[start:end])
    return None


def declare_in_pyproject(path: Path, specs: list[str]) -> str:
    original = path.read_text(encoding="utf-8")
    found = _existing_group(original)
    current = found[2] if found else []

    have = {_pkg_name(s) for s in current}
    merged = list(current) + [s for s in specs if _pkg_name(s) not in have]
    if merged == current:
        return f"already declared in pyproject.toml [dependency-groups].{GROUP_NAME}"

    rendered = f"{GROUP_NAME} = [" + ", ".join(f'"{s}"' for s in merged) + "]"
    section_end = _section_header_end(original, "[dependency-groups]")
    if found:
        # Key exists — replace the array in place, preserving everything around it.
        start, end, _ = found
        updated = original[:start] + rendered + original[end:]
    elif section_end is not None:
        # Section exists but the key does not — insert right after the header.
        updated = original[:section_end] + f"\n{MARKER}\n{rendered}" + original[section_end:]
    else:
        sep = "" if original.endswith("\n\n") else ("\n" if original.endswith("\n") else "\n\n")
        updated = f"{original}{sep}[dependency-groups]\n{MARKER}\n{rendered}\n"

    if not _validate_toml(updated):
        raise ValueError("edit would produce invalid TOML — pyproject.toml left untouched")
    path.write_text(updated, encoding="utf-8")
    return f"declared in pyproject.toml [dependency-groups].{GROUP_NAME}"


def declare_in_requirements(req: Path, specs: list[str]) -> str:
    harness_req = req.parent / HARNESS_REQ_FILE

    current: list[str] = []
    if harness_req.exists():
        current = [
            ln.strip()
            for ln in harness_req.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
    have = {_pkg_name(s) for s in current}
    merged = current + [s for s in specs if _pkg_name(s) not in have]

    actions = []
    if merged != current:
        harness_req.write_text(MARKER + "\n" + "\n".join(merged) + "\n", encoding="utf-8")
        actions.append(f"wrote {HARNESS_REQ_FILE}")

    body = req.read_text(encoding="utf-8")
    if HARNESS_REQ_FILE not in body:
        sep = "" if body.endswith("\n") or not body else "\n"
        req.write_text(f"{body}{sep}-r {HARNESS_REQ_FILE}\n", encoding="utf-8")
        actions.append(f"referenced from {req.name}")

    return ", ".join(actions) if actions else f"already declared in {HARNESS_REQ_FILE}"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: declare-repo-deps.py <repo-path> <pkg-spec> [...]", file=sys.stderr)
        return 2

    repo = Path(argv[1])
    specs = argv[2:]
    if not repo.is_dir():
        print(f"  SKIP {repo}: not a directory")
        return 0

    pyproject = repo / "pyproject.toml"
    requirements = repo / "requirements.txt"
    try:
        if pyproject.is_file():
            print(f"  {repo.name}: {declare_in_pyproject(pyproject, specs)}")
        elif requirements.is_file():
            print(f"  {repo.name}: {declare_in_requirements(requirements, specs)}")
        else:
            print(f"  {repo.name}: no pyproject.toml or requirements.txt — nothing to declare")
    except (OSError, ValueError) as exc:
        print(f"  {repo.name}: FAILED to declare — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
