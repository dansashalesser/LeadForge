"""Every shipped fixture validates against its adapter's declared raw schema (17.2)."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    LeadContribution,
    RawBatch,
)
from leadforge.lead_ingestion.errors import FixtureSchemaError
from leadforge.lead_ingestion.fixture_metadata import FixtureRecord, load_manifest
from leadforge.lead_ingestion.fixture_schema import (
    MAX_FIXTURE_BYTES,
    validate_fixture_file,
    validate_fixture_schemas,
    validate_provider_fixtures,
)
from leadforge.lead_ingestion.registry import SourceRegistry

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"
CANARY = "PAYLOAD-CANARY-4417"
_REGISTRY = SourceRegistry.discover()
SOURCES = {name: _REGISTRY.source_class(name) for name in _REGISTRY.names()}
SHIPPED = [
    (provider, record)
    for provider in sorted(SOURCES)
    for record in load_manifest(FIXTURES_ROOT / provider).fixtures
]


FIRST_JSON = next((p, r.file) for p, r in SHIPPED if r.file.endswith(".json"))
SHIPPED_IDS = [f"{provider}/{record.file}" for provider, record in SHIPPED]


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_ROOT, root)
    return root


def _edit(root: Path, provider: str, file: str, change: Callable[[Any], None]) -> None:
    path = root / provider / file
    body = json.loads(path.read_text(encoding="utf-8"))
    change(body)
    path.write_text(json.dumps(body), encoding="utf-8")


def _fails(root: Path, provider: str, file: str) -> FixtureSchemaError:
    record = next(r for r in load_manifest(root / provider).fixtures if r.file == file)
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_file(root / provider, SOURCES[provider], record)
    assert exc.value.provider == provider
    return exc.value


# Verifies: specs/lead-source-adapters/requirements.md#5.3
@pytest.mark.parametrize(("provider", "record"), SHIPPED, ids=SHIPPED_IDS)
def test_every_shipped_fixture_validates_against_its_declared_raw_schema(
    provider: str, record: FixtureRecord
) -> None:
    validate_fixture_file(FIXTURES_ROOT / provider, SOURCES[provider], record)


# Verifies: specs/lead-source-adapters/requirements.md#5.3
def test_the_guard_covers_every_registered_source() -> None:
    assert {provider for provider, _ in SHIPPED} == set(SOURCES)
    assert len(SHIPPED) >= 9


# Verifies: specs/lead-source-adapters/requirements.md#5.3
def test_the_whole_tree_validates_as_a_callable() -> None:
    validate_fixture_schemas(FIXTURES_ROOT, SOURCES)


# Verifies: specs/lead-source-adapters/requirements.md#5.3
@pytest.mark.parametrize("provider", sorted(SOURCES))
def test_each_provider_validates_all_of_its_fixtures(provider: str) -> None:
    validate_provider_fixtures(FIXTURES_ROOT / provider, SOURCES[provider])


# Verifies: specs/lead-source-adapters/requirements.md#5.4
@pytest.mark.parametrize(
    ("provider", "file"),
    [(p, r.file) for p, r in SHIPPED if r.file.endswith(".json")],
)
@pytest.mark.parametrize(
    "content",
    [b"", b"   \n", b"{not json " + CANARY.encode(), b"\xff\xfe\x00", b"null", b"[]"],
    ids=["empty", "blank", "not-json", "not-utf8", "null", "list"],
)
def test_a_fixture_that_is_not_a_json_object_fails_by_file_name(
    tmp_path: Path, provider: str, file: str, content: bytes
) -> None:
    root = _copy(tmp_path)
    (root / provider / file).write_bytes(content)
    error = _fails(root, provider, file)
    assert error.field == file
    assert CANARY not in f"{error} {error.field}"


# Verifies: specs/lead-source-adapters/requirements.md#5.4
def test_a_deeply_nested_payload_fails_instead_of_crashing(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    provider, file = FIRST_JSON
    depth = 200_000
    text = '{"data": ' + "[" * depth + "]" * depth + "}"
    (root / provider / file).write_text(text, encoding="utf-8")
    assert _fails(root, provider, file).field == file


# Verifies: specs/lead-source-adapters/requirements.md#5.4
def test_a_fixture_over_the_size_cap_fails_unread(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    provider, file = FIRST_JSON
    (root / provider / file).write_bytes(b" " * (MAX_FIXTURE_BYTES + 1))
    assert _fails(root, provider, file).field == file


# Verifies: specs/lead-source-adapters/requirements.md#5.4
def test_a_missing_fixture_file_is_a_named_error(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    provider, file = FIRST_JSON
    (root / provider / file).unlink()
    assert _fails(root, provider, file).field == file


class _NoSchema(BaseLeadSource):
    """A source that ships fixtures but declares no raw schema for them."""

    name = "noschema"

    async def fetch_raw(self, request: Any) -> RawBatch:
        raise NotImplementedError

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        raise NotImplementedError


# Verifies: specs/lead-source-adapters/requirements.md#5.3
def test_a_source_without_a_declared_raw_schema_fails_its_fixtures(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "noschema"
    directory.mkdir()
    (directory / "search.json").write_text("{}", encoding="utf-8")
    manifest = {
        "provider": "noschema",
        "fixtures": [
            {
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
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(FixtureSchemaError) as exc:
        validate_provider_fixtures(directory, _NoSchema)
    assert exc.value.provider == "noschema"
    assert exc.value.field == "search: no raw schema declared"


# Verifies: specs/lead-source-adapters/requirements.md#5.4
def test_a_provider_without_a_source_class_is_rejected(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    kept = sorted(SOURCES)[0]
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_schemas(root, {kept: SOURCES[kept]})
    assert exc.value.provider != kept


def _chain(error: BaseException) -> list[BaseException]:
    links: list[BaseException] = []
    current: BaseException | None = error
    while current is not None:
        links.append(current)
        current = current.__cause__ or current.__context__
    return links


# Verifies: specs/lead-source-adapters/requirements.md#5.4
@pytest.mark.parametrize(
    "content",
    [
        b'{"a": ' + CANARY.encode(),
        b'{"a": "\xff' + CANARY.encode() + b'"}',
        b'{"data": {"domain": 5, "emails": "' + CANARY.encode() + b'"}}',
        b'{"data": 1, "data": "' + CANARY.encode() + b'"}',
    ],
    ids=["not-json", "not-utf8", "wrong-type", "duplicate-key"],
)
def test_a_failure_carries_no_chained_exception_that_holds_fixture_content(
    tmp_path: Path, content: bytes
) -> None:
    root = _copy(tmp_path)
    provider, file = FIRST_JSON
    (root / provider / file).write_bytes(content)
    error = _fails(root, provider, file)
    assert _chain(error) == [error]


# Verifies: specs/lead-source-adapters/requirements.md#5.4
def test_a_duplicate_key_fails_by_file_name(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    provider, file = FIRST_JSON
    (root / provider / file).write_text('{"a": {}, "a": {}}', encoding="utf-8")
    assert _fails(root, provider, file).field == file


# Verifies: specs/lead-source-adapters/requirements.md#5.4
def test_a_symlinked_fixture_is_not_followed(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    provider, file = FIRST_JSON
    target = tmp_path / "outside.json"
    target.write_text((root / provider / file).read_text(encoding="utf-8"))
    (root / provider / file).unlink()
    (root / provider / file).symlink_to(target)
    assert _fails(root, provider, file).field == file


# Verifies: specs/lead-source-adapters/requirements.md#5.4
def test_an_unlisted_or_absent_fixture_fails_the_whole_tree(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    provider, file = FIRST_JSON
    (root / provider / "extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FixtureSchemaError) as unlisted:
        validate_fixture_schemas(root, SOURCES)
    assert (unlisted.value.provider, unlisted.value.field) == (
        provider,
        "file extra.json",
    )
    (root / provider / "extra.json").unlink()
    (root / provider / file).unlink()
    with pytest.raises(FixtureSchemaError) as absent:
        validate_fixture_schemas(root, SOURCES)
    assert (absent.value.provider, absent.value.field) == (provider, f"file {file}")
