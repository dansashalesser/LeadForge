#!/bin/bash
# test-integrity-guard — PostToolUse soft gate (Write/Edit/MultiEdit).
# Pattern from Addy Osmani, "Agentic Code Review": agents take the cheapest path
# to green — weakening tests or CI instead of fixing the code ("gradient descent
# to green"). This watches the gate itself: when a test file or CI/coverage config
# is edited, it flags weakening signals (added skips, stubbed assertions, lowered
# thresholds, removed asserts) and asks Claude to confirm it's a real spec change.
# Does NOT block — outputs a reminder; Claude/user decides. Exits 0 always.
#
# NO REGEX (2026-09-03). This hook used `import re` for every check below, which
# put it in violation of the repo-wide ban in ruff.toml — a ban TID251 could not
# actually enforce here, because ruff only reads `.py` files and this Python lives
# in a shell heredoc. It is now literal token membership plus pathlib, which is
# what the ban asks for: no pattern can silently match the wrong span of text.
#
# The tradeoff is deliberate and worth stating. Literal matching cannot express
# "an added skip marker at a word boundary", so `describe.skipBecause(` would
# match the `.skip(` probe. For a soft advisory that prints a reminder and always
# exits 0, a rare extra prompt costs a line of output; a regex that silently
# matches the wrong span costs a missed weakened test. Erring loud is correct here.
set -euo pipefail

EVENT_FILE="$(mktemp)"
cat > "$EVENT_FILE"
trap 'rm -f "$EVENT_FILE"' EXIT

EVENT_FILE="$EVENT_FILE" python3 - <<'PY' 2>/dev/null || true
import json, os
from pathlib import PurePosixPath

try:
    with open(os.environ["EVENT_FILE"], encoding="utf-8") as fh:
        e = json.load(fh)
except Exception:
    raise SystemExit(0)

tool = e.get("tool_name", "")
if tool not in ("Write", "Edit", "MultiEdit"):
    raise SystemExit(0)

inp = e.get("tool_input", {}) or {}
path = inp.get("file_path", inp.get("path", "")) or ""
if not path:
    raise SystemExit(0)

p = PurePosixPath(path.replace("\\", "/"))
base = p.name
low_name = base.lower()
parts_low = {seg.lower() for seg in p.parts}

# --- Is this a test file or a CI/coverage config? ---
# Structure, not pattern: a stem/suffix decision plus a directory-membership test.
stem = low_name
for _ in range(len(p.suffixes)):
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem

TEST_DIRS = {"test", "tests", "__tests__", "spec"}
TEST_STEM_PREFIXES = ("test_",)
TEST_STEM_SUFFIXES = ("_test", ".test", ".spec", "_spec")

TEST_FILE = (
    bool(parts_low & TEST_DIRS)
    or stem.startswith(TEST_STEM_PREFIXES)
    or stem.endswith(TEST_STEM_SUFFIXES)
    # `foo.test.ts` / `foo.spec.js` — the marker is the second-to-last suffix.
    or (len(p.suffixes) >= 2 and p.suffixes[-2].lower() in (".test", ".spec"))
)

CONFIG_NAMES = {
    "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml",
    ".coveragerc", "jest.config.js", "jest.config.ts",
    "vitest.config.js", "vitest.config.ts", ".gitlab-ci.yml",
}
IN_WORKFLOWS = ".github" in parts_low and "workflows" in parts_low
CONFIG_FILE = (
    low_name in CONFIG_NAMES
    or (IN_WORKFLOWS and low_name.endswith((".yml", ".yaml")))
)

if not (TEST_FILE or CONFIG_FILE):
    raise SystemExit(0)

# --- Gather the NEW text introduced by this edit, plus OLD text when available ---
new_text, old_text = "", ""
if tool == "Write":
    new_text = inp.get("content", "") or ""
elif tool == "Edit":
    new_text = inp.get("new_string", "") or ""
    old_text = inp.get("old_string", "") or ""
elif tool == "MultiEdit":
    for ed in (inp.get("edits", []) or []):
        new_text += (ed.get("new_string", "") or "") + "\n"
        old_text += (ed.get("old_string", "") or "") + "\n"

if not new_text.strip():
    raise SystemExit(0)

signals = []

# 1. Added skip / xfail / disabled markers (python, js/ts, go, junit).
#    Each probe is a literal that only appears in the construct it names.
SKIP_TOKENS = [
    ("@pytest.mark.skip",  "pytest skip marker"),
    ("@pytest.mark.xfail", "pytest xfail marker"),
    ("pytest.skip(",       "pytest.skip() call"),
    ("@unittest.skip",     "unittest skip decorator"),
    ("xit(",               "disabled JS test (xit)"),
    ("xdescribe(",         "disabled JS suite (xdescribe)"),
    ("it.skip(",           "JS it.skip"),
    ("test.skip(",         "JS test.skip"),
    ("describe.skip(",     "JS describe.skip"),
    ("it.todo(",           "JS it.todo"),
    ("test.todo(",         "JS test.todo"),
    ("t.Skip(",            "Go t.Skip()"),
    ("@Disabled",          "JUnit @Disabled"),
    ("@Ignore",            "JUnit @Ignore"),
]
for token, label in SKIP_TOKENS:
    if token in new_text and token not in old_text:
        signals.append("added " + label)

# 2. Stubbed / tautological assertions.
#    Whitespace variants are enumerated rather than expressed as a pattern —
#    the set is small, closed, and reads as exactly what it matches.
STUB_TOKENS = [
    ("assert True",                  "assert True"),
    ("assert  True",                 "assert True"),
    ("assert 1 == 1",                "assert 1 == 1"),
    ("assert 1==1",                  "assert 1==1"),
    ("expect(true).toBe(true)",      "expect(true).toBe(true)"),
    ("expect(true).toBeTruthy()",    "expect(true).toBeTruthy()"),
    ("assert.ok(true)",              "assert.ok(true)"),
    ("pass  # TODO",                 "pass # TODO stub"),
    ("pass # TODO",                  "pass # TODO stub"),
]
seen_stub = set()
for token, label in STUB_TOKENS:
    if token in new_text and label not in seen_stub:
        seen_stub.add(label)
        signals.append("tautological/stub assertion (" + label + ")")

# 3. Touched coverage / threshold knobs (flag any touch — reviewer confirms direction).
COV_TOKENS = [
    ("--cov-fail-under",   "--cov-fail-under"),
    ("fail_under",         "fail_under"),
    ("coverageThreshold",  "jest coverageThreshold"),
    ("minimum_coverage",   "minimum_coverage"),
]
for token, label in COV_TOKENS:
    if token in new_text:
        signals.append("touched coverage threshold (" + label + ")")

# 4. Removed assertions (Edit/MultiEdit only — compare counts old vs new).
#    str.count over a fixed token list; no capture groups, nothing to mis-span.
ASSERT_TOKENS = ("assert", "expect", "assert_that", "require.")


def assert_count(text):
    return sum(text.count(tok) for tok in ASSERT_TOKENS)


if old_text:
    removed = assert_count(old_text) - assert_count(new_text)
    if removed > 0:
        signals.append("removed %d assertion(s)" % removed)

if not signals:
    raise SystemExit(0)

Y = "\033[1;33m"; B = "\033[1m"; R = "\033[0m"
print(B + Y + "⚠  test-integrity-guard — " + base + R)
for s in signals:
    print(Y + "   • " + s + R)
print("")
print(B + "Gradient-descent-to-green check:" + R + " the cheapest path to a passing")
print("build is often to weaken the test, not fix the code. Confirm each change above is")
print("a deliberate spec change — not a shortcut to make a failing test pass. If the code")
print("is wrong, fix the code itself. Never soften the assertion or skip the test.")
PY

exit 0

# REGISTRATION (settings.json — PostToolUse, matcher "Write|Edit|MultiEdit"):
# {
#   "matcher": "Write|Edit|MultiEdit",
#   "hooks": [
#     { "type": "command", "command": "bash .claude/hooks/test-integrity-guard.sh" }
#   ]
# }
