"""No module, test, or requirement-derived file names a vendor as a built-in (23.1).

Scope (explicit, so later tasks are not broken by accident):

* Scanned: every text file under ``src/`` whose suffix is in ``SCANNED_SUFFIXES``.
* Exempt directories: ``adapters`` (tasks 12-15 add one module per provider, and a
  module name is the provider name) and ``fixtures`` (synthetic provider payloads).
  The shipped example profile and ``config/`` live outside ``src/`` (task 9.2).
* This file is exempt: it holds the denylist.
* ``BASELINE`` lists files written before 9.1 that already name a vendor. It is a
  ratchet: a file not listed may never name one, and a listed file that no longer
  does must be removed from the list. Shrinking it is the cleanup the baseline
  records; growing it needs a conscious edit here.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]

# Lead-data vendors and the technologies a profile might target. Backend and driver
# names are covered by the 6.1 structure scan, not this one.
DENYLIST = (
    "hubspot",
    "salesforce",
    "apollo",
    "zoominfo",
    "hunter",
    "leadfeeder",
    "uplead",
    "clay",
    "datastax",
    "cassandra",
    "linkedin",
    "clearbit",
    "lusha",
    "pipedrive",
)
SCANNED_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".toml", ".md", ".txt", ".csv"}
EXEMPT_DIRS = {"adapters", "fixtures", "__pycache__"}
THIS_FILE = Path(__file__).resolve()

# A name counts even inside an identifier (``apollo_client``) but not inside a longer
# word (``clayton``).
PATTERN = re.compile(r"(?<![a-z])(" + "|".join(DENYLIST) + r")(?![a-z])", re.IGNORECASE)

BASELINE = frozenset(
    {
        "leadforge/lead_ingestion/models.py",
        "leadforge/lead_ingestion/store/migrations/versions/0001_initial_lead_store_schema.py",
        "leadforge/lead_ingestion/store/models.py",
        "leadforge/lead_ingestion/structure_guard.py",
        "leadforge/lead_ingestion/tests/test_base_source.py",
        "leadforge/lead_ingestion/tests/test_canonical_entities.py",
        "leadforge/lead_ingestion/tests/test_env_example.py",
        "leadforge/lead_ingestion/tests/test_errors.py",
        "leadforge/lead_ingestion/tests/test_log_redaction.py",
        "leadforge/lead_ingestion/tests/test_normalizer.py",
        "leadforge/lead_ingestion/tests/test_provenance.py",
        "leadforge/lead_ingestion/tests/test_signal_strength.py",
        "leadforge/lead_ingestion/tests/test_slice_structure.py",
        "leadforge/lead_ingestion/tests/test_source_absence.py",
        "leadforge/lead_ingestion/tests/test_source_registry.py",
        "leadforge/lead_ingestion/tests/test_store_schema.py",
        "leadforge/lead_ingestion/tests/test_untrusted_text.py",
    }
)


def _scanned_files() -> list[Path]:
    return sorted(
        p
        for p in SRC.rglob("*")
        if p.is_file()
        and p.suffix in SCANNED_SUFFIXES
        and p.resolve() != THIS_FILE
        and not EXEMPT_DIRS & set(p.relative_to(SRC).parts)
    )


def _offenders() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8")
        names = {m.group(1).lower() for m in PATTERN.finditer(text)}
        if names:
            found[path.relative_to(SRC).as_posix()] = names
    return found


def test_scan_actually_covers_the_tree() -> None:
    files = {p.relative_to(SRC).as_posix() for p in _scanned_files()}

    assert "leadforge/lead_ingestion/target_profile.py" in files
    assert "leadforge/lead_ingestion/tests/test_target_profile.py" in files
    assert len(files) > 40
    assert not any("/adapters/" in f or "/fixtures/" in f for f in files)


def test_pattern_matches_names_in_identifiers_but_not_inside_longer_words() -> None:
    assert PATTERN.search("my_apollo_client")
    assert PATTERN.search("Apollo2")
    assert PATTERN.search("HUBSPOT")
    assert not PATTERN.search("clayton")
    assert not PATTERN.search("hunters_of_nothing")
    assert not PATTERN.search("tech_alpha provider_one")


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_no_new_file_names_a_vendor_or_technology_as_a_built_in() -> None:
    new = {f: n for f, n in _offenders().items() if f not in BASELINE}

    assert new == {}, "vendor names belong in config or fixtures, not code or tests"


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_baseline_only_lists_files_that_still_name_a_vendor() -> None:
    stale = sorted(BASELINE - set(_offenders()))

    assert stale == [], "remove cleaned files from BASELINE so the ratchet tightens"


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_target_profile_modules_name_no_vendor() -> None:
    lead_ingestion = SRC / "leadforge" / "lead_ingestion"
    mine = [
        lead_ingestion / "target_profile.py",
        lead_ingestion / "source_settings.py",
        lead_ingestion / "config_file.py",
        lead_ingestion / "tests" / "test_target_profile.py",
        lead_ingestion / "tests" / "test_source_settings.py",
    ]

    for path in mine:
        assert not PATTERN.search(path.read_text(encoding="utf-8")), path.name
