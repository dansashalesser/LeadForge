"""No module, test, or requirement-derived file names a vendor as a built-in (23.1).

Scope (explicit, so later tasks are not broken by accident):

* Scanned: every text file under ``src/`` whose suffix is in ``SCANNED_SUFFIXES``.
* Exempt directories: ``adapters`` (tasks 12-15 add one module per provider, and a
  module name is the provider name) and ``fixtures`` (synthetic provider payloads).
  The shipped example profile and ``config/`` live outside ``src/`` (task 9.2).
* This file is exempt: it holds the denylist.
* There is no baseline and no allowlist: outside the exempt directories the scan must
  find zero hits.
* ``linkedin`` is deliberately not in the denylist. Requirement 23.1 forbids naming a
  vendor as a built-in *targeting assumption* (a provider module, a hard-coded
  technology or competitor, a provider name baked into logic). ``linkedin_url`` is a
  canonical Lead data field, the profile link of a person, which is data and not a
  choice of vendor. Renaming it would need a schema migration and would ripple through
  the model, provenance paths and fixtures for no neutrality gain.
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


def _scanned_files(root: Path = SRC) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in SCANNED_SUFFIXES
        and p.resolve() != THIS_FILE
        and not EXEMPT_DIRS & set(p.relative_to(root).parts)
    )


def _offenders(root: Path = SRC) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in _scanned_files(root):
        text = path.read_text(encoding="utf-8")
        names = {m.group(1).lower() for m in PATTERN.finditer(text)}
        if names:
            found[path.relative_to(root).as_posix()] = names
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
def test_no_file_outside_adapters_and_fixtures_names_a_vendor_or_technology() -> None:
    offenders = _offenders()

    assert offenders == {}, (
        "vendor names belong in config or fixtures, not code or tests"
    )


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_scanner_flags_a_planted_vendor_name_in_scope(tmp_path: Path) -> None:
    planted = tmp_path / "leadforge" / "planted.py"
    planted.parent.mkdir(parents=True)
    planted.write_text("PROVIDER = 'Apollo'\n", encoding="utf-8")
    exempt = tmp_path / "leadforge" / "adapters" / "apollo.py"
    exempt.parent.mkdir()
    exempt.write_text("PROVIDER = 'apollo'\n", encoding="utf-8")

    assert _offenders(tmp_path) == {"leadforge/planted.py": {"apollo"}}


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
