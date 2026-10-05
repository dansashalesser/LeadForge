"""No fixture carries a field its adapter neither maps nor lists as ignored (17.3).

Every raw model tolerates unknown fields, so the schema check (17.2) cannot see one.
This guard walks every leaf path of every shipped fixture and fails, naming the
provider and the path, on one that the adapter's normalisation rules do not consume
and its declared ignore set does not list (design.md: a silent drop must fail).
"""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from leadforge.lead_ingestion.base_source import BaseLeadSource
from leadforge.lead_ingestion.errors import FixtureSchemaError
from leadforge.lead_ingestion.fixture_fields import (
    validate_fixture_field_coverage,
    validate_fixture_fields,
    validate_provider_fixture_fields,
)
from leadforge.lead_ingestion.fixture_metadata import FixtureRecord, load_manifest
from leadforge.lead_ingestion.registry import SourceRegistry

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"
CANARY = "PAYLOAD-CANARY-4417"
UNKNOWN = "zz_unknown_field"
_REGISTRY = SourceRegistry.discover()
SOURCES = {name: _REGISTRY.source_class(name) for name in _REGISTRY.names()}
SHIPPED = [
    (provider, record)
    for provider in sorted(SOURCES)
    for record in load_manifest(FIXTURES_ROOT / provider).fixtures
]
SHIPPED_JSON = [(p, r) for p, r in SHIPPED if r.endpoint is not None]
IDS = [f"{provider}/{record.file}" for provider, record in SHIPPED_JSON]


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_ROOT, root)
    return root


def _edit(root: Path, provider: str, file: str, change: Callable[[Any], None]) -> None:
    path = root / provider / file
    body = json.loads(path.read_text(encoding="utf-8"))
    change(body)
    path.write_text(json.dumps(body), encoding="utf-8")


def _fails(root: Path, provider: str, record: FixtureRecord) -> FixtureSchemaError:
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_field_coverage(root / provider, SOURCES[provider], record)
    assert exc.value.provider == provider
    return exc.value


# Verifies: specs/lead-source-adapters/requirements.md#5.5
@pytest.mark.parametrize(
    ("provider", "record"),
    SHIPPED,
    ids=[f"{provider}/{record.file}" for provider, record in SHIPPED],
)
def test_every_shipped_fixture_field_is_mapped_or_listed_as_ignored(
    provider: str, record: FixtureRecord
) -> None:
    validate_fixture_field_coverage(FIXTURES_ROOT / provider, SOURCES[provider], record)


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_the_guard_covers_every_registered_source_and_the_whole_tree() -> None:
    assert {provider for provider, _ in SHIPPED_JSON} == set(SOURCES)
    validate_fixture_fields(FIXTURES_ROOT, SOURCES)
    for provider in SOURCES:
        validate_provider_fixture_fields(FIXTURES_ROOT / provider, SOURCES[provider])


# Verifies: specs/lead-source-adapters/requirements.md#5.5
@pytest.mark.parametrize(("provider", "record"), SHIPPED_JSON, ids=IDS)
def test_an_unknown_top_level_field_fails_naming_provider_and_path(
    tmp_path: Path, provider: str, record: FixtureRecord
) -> None:
    root = _copy(tmp_path)
    _edit(root, provider, record.file, lambda b: b.update({UNKNOWN: CANARY}))
    error = _fails(root, provider, record)
    assert error.field == f"{record.file}:{UNKNOWN}"


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_the_first_unmapped_path_in_sorted_order_is_reported(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    provider, record = SHIPPED_JSON[0]
    _edit(root, provider, record.file, lambda b: b.update({"zz_b": 1, "zz_a": 2}))
    assert _fails(root, provider, record).field == f"{record.file}:zz_a"


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_a_failure_never_echoes_a_value_or_an_unbounded_key(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    provider, record = SHIPPED_JSON[0]
    hostile = "line\nbreak" + CANARY + "x" * 500
    _edit(root, provider, record.file, lambda b: b.update({hostile: CANARY}))
    error = _fails(root, provider, record)
    assert "\n" not in error.field
    assert len(error.field) <= len(record.file) + 1 + 120
    assert error.__cause__ is None
    assert error.__context__ is None
    _edit(root, provider, record.file, lambda b: b.pop(hostile))
    _edit(root, provider, record.file, lambda b: b.update({UNKNOWN: CANARY}))
    assert CANARY not in f"{_fails(root, provider, record)}"


# Verifies: specs/lead-source-adapters/requirements.md#5.4
def test_a_fixture_that_fails_its_schema_fails_by_the_schema_field_first(
    tmp_path: Path,
) -> None:
    root = _copy(tmp_path)
    provider, record = SHIPPED_JSON[0]
    (root / provider / record.file).write_bytes(b"[]")
    assert _fails(root, provider, record).field == record.file


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_a_reference_file_is_not_walked_as_an_endpoint_response() -> None:
    reference = [(p, r) for p, r in SHIPPED if r.endpoint is None]
    assert reference  # the guard must meet at least one and pass it
    for provider, record in reference:
        validate_fixture_field_coverage(
            FIXTURES_ROOT / provider, SOURCES[provider], record
        )


class _NoCoverage(BaseLeadSource):
    """A source that ships a fixture and declares no field coverage for it."""

    name = "nocoverage"

    async def fetch_raw(self, request: Any) -> Any:
        raise NotImplementedError

    def normalize(self, raw: Any) -> Any:
        raise NotImplementedError

    @classmethod
    def validate_fixture(cls, endpoint: str, body: object) -> None:
        return None


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_a_source_that_declares_no_field_coverage_fails_its_fixtures(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "nocoverage"
    directory.mkdir()
    (directory / "search.json").write_text("{}", encoding="utf-8")
    record = {
        "file": "search.json",
        "endpoint": "search",
        "origin": "hand_made",
        "recorded_on": "2026-10-05",
        "doc_url": "https://docs.example.test/search",
        "schema_status": "unverified",
        "schema_verified_on": None,
        "api_version": None,
        "redaction": None,
        "note": "stand-in",
    }
    (directory / "manifest.json").write_text(
        json.dumps({"provider": "nocoverage", "fixtures": [record]}), encoding="utf-8"
    )
    with pytest.raises(FixtureSchemaError) as exc:
        validate_provider_fixture_fields(directory, _NoCoverage)
    assert exc.value.provider == "nocoverage"
    assert exc.value.field == "search: no field coverage declared"


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_a_tree_without_a_source_for_a_provider_is_rejected(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    kept = sorted(SOURCES)[0]
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_fields(root, {kept: SOURCES[kept]})
    assert exc.value.provider != kept
