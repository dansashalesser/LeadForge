#!/usr/bin/env python3
"""Extend the repo-wide regex ban to Python embedded in shell files.

`ruff.toml` bans `re` and `regex` via TID251, but ruff only reads `.py` files.
A large share of this harness's Python lives inside shell heredocs in
`hooks/claude/*.sh` and `scripts/**/*.sh`, where the ban was unenforceable and
therefore not enforced — 16 files were using `re` at the time this guard was
written, including `stop-hook.sh`, which regex-parsed the free text in
`observations.md`: exactly the failure the ban exists to prevent.

This closes that gap without pretending the debt does not exist. Files already
violating are listed in `no-regex-debt.txt` and reported as DEBT, not failure,
so the guard can be wired into pre-commit today. Two things fail the run:

  * a violation in a file NOT on the ledger  — the ban is a ratchet, not a wish
  * a ledger entry that no longer violates   — stale entries hide regressions

That second rule is what makes the ledger shrink. Fixing a file and leaving it
listed would let the next `re` slip back in unnoticed.

No regex is used to detect regex. Detection is literal-token membership with an
explicit identifier-boundary check, on comment-stripped lines.

Usage:
    check-no-regex.py                 # scan the whole repo
    check-no-regex.py FILE [FILE...]  # scan specific files (pre-commit path)
    check-no-regex.py --list          # print current violators, no verdict
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LEDGER = Path(__file__).resolve().parent / "no-regex-debt.txt"

# A shell file only matters here if it actually runs Python.
PYTHON_INVOCATIONS = ("python3", "python2", "python ", "uv run python", "uvx python")

# Tokens that mean "the regex engine is in use". Each is checked with an
# identifier-boundary test on the preceding character, because `require.sub(`
# contains the literal substring `re.sub(` and is not a regex call.
RE_CALL_TOKENS = (
    "re.search(", "re.match(", "re.fullmatch(", "re.findall(", "re.finditer(",
    "re.compile(", "re.sub(", "re.subn(", "re.split(", "re.escape(",
)
IMPORT_FORMS = ("import re", "import regex")

IDENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.")


def is_scannable(path: Path) -> bool:
    """`*.test.sh` is out of scope, and has to be.

    A test for this guard must contain `import re` and `re.search(` as fixture
    text, so scanning test files would make the guard fail on its own test suite
    — and the only ways out of that are worse: ledgering a file that is not debt,
    or obfuscating the fixtures until they no longer resemble what they test.

    The exclusion is narrow. `install.sh` already skips `*.test.sh` when copying
    hooks into projects, so nothing here ever runs as a hook, and no metric is
    computed from its output. Runtime shell — the code that actually parses text
    and feeds numbers into the harness — is fully in scope.
    """
    return path.name.endswith(".sh") and not path.name.endswith(".test.sh")


def strip_comment(line: str) -> str:
    """Drop whole-line comments. Both `#` in shell and `#` in Python mean the same.

    Only whole-line comments are removed. A trailing `#` cannot be stripped
    safely without knowing quoting, and over-stripping would hide real code.
    """
    return "" if line.lstrip().startswith("#") else line


def has_bounded_token(line: str, token: str) -> bool:
    """True if `token` appears in `line` not preceded by an identifier character.

    `require.sub(` contains `re.sub(` as a substring; the char before it is `i`,
    an identifier char, so it is correctly rejected.
    """
    start = 0
    while True:
        idx = line.find(token, start)
        if idx == -1:
            return False
        if idx == 0 or line[idx - 1] not in IDENT_CHARS:
            return True
        start = idx + 1


def import_hit(line: str) -> str | None:
    stripped = line.strip()
    for form in IMPORT_FORMS:
        if stripped == form:
            return form
        # `import re, os` or `import re` with a trailing comment — but never
        # `import requests`, whose next char is `q`.
        if stripped.startswith(form) and len(stripped) > len(form):
            nxt = stripped[len(form)]
            if nxt in (",", " ", "\t", ";"):
                return form
    return None


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return [(lineno, token, source_line)] for regex use in embedded Python."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    lines = text.splitlines()
    code = [strip_comment(ln) for ln in lines]

    # Cheap gate: if the file never invokes Python, embedded-Python regex is
    # not what we are looking at.
    if not any(inv in ln for ln in code for inv in PYTHON_INVOCATIONS):
        return []

    hits: list[tuple[int, str, str]] = []
    for i, ln in enumerate(code, start=1):
        if not ln.strip():
            continue
        form = import_hit(ln)
        if form:
            hits.append((i, form, lines[i - 1].strip()))
            continue
        for token in RE_CALL_TOKENS:
            if has_bounded_token(ln, token):
                hits.append((i, token, lines[i - 1].strip()))
                break
    return hits


def repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except OSError:
        pass
    return Path.cwd()


def all_shell_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for args in (
        ["git", "ls-files", "-z", "*.sh"],
        ["git", "ls-files", "-z", "-o", "--exclude-standard", "*.sh"],
    ):
        out = subprocess.run(args, cwd=root, capture_output=True, text=True, check=False)
        if out.returncode != 0:
            continue
        for name in out.stdout.split("\0"):
            if name.strip() and is_scannable(Path(name)):
                files.append(root / name)
    return sorted(set(files))


def load_ledger(root: Path) -> set[str]:
    if not LEDGER.is_file():
        return set()
    entries = set()
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.add(line)
    return entries


def main(argv: list[str]) -> int:
    root = repo_root()
    list_only = "--list" in argv
    args = [a for a in argv if not a.startswith("--")]

    if args:
        targets = [Path(a) if Path(a).is_absolute() else root / a for a in args]
        targets = [t for t in targets if is_scannable(t) and t.is_file()]
        full_scan = False
    else:
        targets = all_shell_files(root)
        full_scan = True

    violators: dict[str, list[tuple[int, str, str]]] = {}
    for path in targets:
        hits = scan_file(path)
        if hits:
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                rel = str(path)
            violators[rel] = hits

    if list_only:
        for rel in sorted(violators):
            print(rel)
        return 0

    ledger = load_ledger(root)
    new_violations = {k: v for k, v in violators.items() if k not in ledger}

    # A ledger entry that no longer violates must be removed, or it becomes a
    # free pass for the next regression. Only checkable on a full scan — a
    # pre-commit run sees a handful of files and cannot conclude anything about
    # the rest of the ledger.
    stale = sorted(ledger - set(violators)) if full_scan else []

    if not new_violations and not stale:
        if violators:
            print(f"no-regex: ok — {len(violators)} known debt file(s) on the ledger, "
                  f"0 new violations.")
        else:
            print("no-regex: ok — no embedded-Python regex found.")
        return 0

    print("=" * 74)
    print("  NO-REGEX GUARD — embedded Python in shell files")
    print("=" * 74)

    if new_violations:
        print("\nNEW violations (not on the debt ledger):\n")
        for rel in sorted(new_violations):
            print(f"  {rel}")
            for lineno, token, src in new_violations[rel][:5]:
                print(f"      {lineno}: {token}   |  {src[:80]}")
        print("\n  Regex fails by producing a plausible wrong answer instead of raising.")
        print("  Write an explicit parser, or emit structured data at the source.")
        print("  See the note at the top of ruff.toml, and hooks/claude/")
        print("  test-integrity-guard.sh for a worked example of the rewrite.")

    if stale:
        print("\nSTALE ledger entries (file no longer uses regex — remove the line):\n")
        for rel in stale:
            print(f"  {rel}")
        print(f"\n  Edit {LEDGER.name} and delete those lines. The ledger only")
        print("  shrinks; leaving a fixed file listed re-opens the hole.")

    print()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
