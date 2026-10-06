"""Fixture provenance metadata (task 17.1): one manifest.json per fixture directory."""

import json
import re
import shutil
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from leadforge.lead_ingestion.base_source import Endpoint
from leadforge.lead_ingestion.errors import FixtureSchemaError
from leadforge.lead_ingestion.fixture_metadata import (
    FixtureOrigin,
    FixtureRecord,
    SchemaStatus,
    load_manifest,
    validate_fixture_tree,
)
from leadforge.lead_ingestion.registry import SourceRegistry

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"
SEARCH = Endpoint(method="POST", path="/v1/search", bucket="default")
LOOKUP = Endpoint(method="GET", path="/v1/lookup", bucket="default")
ENDPOINTS: Mapping[str, Mapping[str, Endpoint]] = {
    "prov": {"search": SEARCH, "lookup": LOOKUP}
}
SECRET_BODY = "PAYLOAD-MARKER-9137"


def record(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "file": "search.json",
        "endpoint": "search",
        "origin": "hand_made",
        "recorded_on": "2026-10-05",
        "doc_url": "https://docs.example.test/search",
        "schema_status": "verified",
        "schema_verified_on": "2026-10-04",
        "api_version": None,
        "redaction": None,
        "note": "stand-in",
    }
    base.update(over)
    return base


def write_tree(
    root: Path, records: list[dict[str, Any]], provider: str = "prov"
) -> Path:
    d = root / provider
    d.mkdir(parents=True, exist_ok=True)
    for r in records:
        (d / r["file"]).parent.mkdir(parents=True, exist_ok=True)
        (d / r["file"]).write_text(json.dumps({"body": SECRET_BODY}))
    (d / "manifest.json").write_text(
        json.dumps({"provider": provider, "fixtures": records})
    )
    return root


def both(root: Path) -> Path:
    return write_tree(
        root,
        [record(), record(file="lookup.json", endpoint="lookup")],
    )


def field_of(exc: pytest.ExceptionInfo[FixtureSchemaError]) -> str:
    return exc.value.field


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_a_complete_tree_loads_with_typed_records(tmp_path: Path) -> None:
    both(tmp_path)
    manifests = validate_fixture_tree(tmp_path, ENDPOINTS)
    rec = manifests["prov"].fixtures[0]
    assert isinstance(rec, FixtureRecord)
    assert rec.origin is FixtureOrigin.HAND_MADE
    assert rec.schema_verified_on == date(2026, 10, 4)
    assert rec.doc_url == "https://docs.example.test/search"


# Verifies: specs/lead-source-adapters/requirements.md#5.6
@pytest.mark.parametrize("missing", ["doc_url", "schema_verified_on", "origin"])
def test_a_missing_required_field_names_provider_and_field(
    tmp_path: Path, missing: str
) -> None:
    r = record()
    del r[missing]
    write_tree(tmp_path, [r])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert exc.value.provider == "prov"
    assert missing in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
@pytest.mark.parametrize("bad", ["", "not a url", "ftp://x.test/a", "http://x.test"])
def test_doc_url_must_be_an_https_url(tmp_path: Path, bad: str) -> None:
    write_tree(tmp_path, [record(doc_url=bad)])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "doc_url" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_verification_date_must_be_a_real_date(tmp_path: Path) -> None:
    write_tree(tmp_path, [record(schema_verified_on="2026-13-45")])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "schema_verified_on" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_unknown_fields_are_rejected(tmp_path: Path) -> None:
    write_tree(tmp_path, [record(extra_field="x")])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "extra_field" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_origin_must_be_a_known_value(tmp_path: Path) -> None:
    write_tree(tmp_path, [record(origin="recorded")])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "origin" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_records_are_frozen(tmp_path: Path) -> None:
    both(tmp_path)
    rec = load_manifest(tmp_path / "prov").fixtures[0]
    with pytest.raises(ValueError, match="frozen"):
        rec.origin = FixtureOrigin.CAPTURED


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_a_captured_fixture_needs_a_redaction_statement(tmp_path: Path) -> None:
    write_tree(tmp_path, [record(origin="captured", redaction=None)])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert exc.value.provider == "prov"
    assert "redaction" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_a_captured_fixture_with_a_redaction_statement_loads(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        [record(origin="captured", redaction="emails and names replaced")],
    )
    rec = load_manifest(tmp_path / "prov").fixtures[0]
    assert rec.origin is FixtureOrigin.CAPTURED


# Verifies: specs/lead-source-adapters/requirements.md#5.6
@pytest.mark.parametrize("origin", ["hand_made", "doc_example"])
def test_a_non_captured_fixture_must_not_claim_a_redaction(
    tmp_path: Path, origin: str
) -> None:
    write_tree(tmp_path, [record(origin=origin, redaction="scrubbed")])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "redaction" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_a_json_fixture_names_the_endpoint_matching_its_file(tmp_path: Path) -> None:
    write_tree(tmp_path, [record(file="search.json", endpoint="lookup")])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "endpoint" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_a_json_fixture_cannot_omit_its_endpoint(tmp_path: Path) -> None:
    write_tree(tmp_path, [record(endpoint=None)])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "endpoint" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_a_reference_file_is_not_an_endpoint(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        [record(), record(file="lookup.json", endpoint="lookup")],
    )
    (tmp_path / "prov" / "ref.csv").write_text("a,b\n")
    manifest = json.loads((tmp_path / "prov" / "manifest.json").read_text())
    manifest["fixtures"].append(record(file="ref.csv", endpoint=None))
    (tmp_path / "prov" / "manifest.json").write_text(json.dumps(manifest))
    assert len(validate_fixture_tree(tmp_path, ENDPOINTS)["prov"].fixtures) == 3
    manifest["fixtures"][-1]["endpoint"] = "search"
    (tmp_path / "prov" / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "endpoint" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_duplicate_file_records_are_rejected(tmp_path: Path) -> None:
    write_tree(tmp_path, [record(), record()])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "file" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_the_manifest_provider_must_match_its_directory(tmp_path: Path) -> None:
    write_tree(tmp_path, [record()])
    manifest = json.loads((tmp_path / "prov" / "manifest.json").read_text())
    manifest["provider"] = "other"
    (tmp_path / "prov" / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert exc.value.provider == "prov"
    assert "provider" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_a_missing_or_unreadable_manifest_is_a_named_error(tmp_path: Path) -> None:
    (tmp_path / "prov").mkdir()
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert exc.value.provider == "prov"
    assert "manifest.json" in field_of(exc)
    (tmp_path / "prov" / "manifest.json").write_text("{not json")
    with pytest.raises(FixtureSchemaError):
        load_manifest(tmp_path / "prov")


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_errors_never_echo_fixture_or_metadata_content(tmp_path: Path) -> None:
    write_tree(tmp_path, [record(note="SECRET-NOTE-7", doc_url="SECRET-URL-8")])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    text = f"{exc.value} {exc.value.field}"
    assert "SECRET" not in text
    assert SECRET_BODY not in text


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_a_declared_endpoint_without_metadata_fails(tmp_path: Path) -> None:
    write_tree(tmp_path, [record()])
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_tree(tmp_path, ENDPOINTS)
    assert exc.value.provider == "prov"
    assert "lookup" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_metadata_for_an_undeclared_endpoint_is_rejected(tmp_path: Path) -> None:
    both(tmp_path)
    (tmp_path / "prov" / "ghost.json").write_text("{}")
    manifest = json.loads((tmp_path / "prov" / "manifest.json").read_text())
    manifest["fixtures"].append(record(file="ghost.json", endpoint="ghost"))
    (tmp_path / "prov" / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_tree(tmp_path, ENDPOINTS)
    assert "ghost" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_a_record_whose_file_is_missing_is_an_orphan(tmp_path: Path) -> None:
    both(tmp_path)
    (tmp_path / "prov" / "lookup.json").unlink()
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_tree(tmp_path, ENDPOINTS)
    assert "lookup.json" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_a_fixture_file_without_a_record_is_an_orphan(tmp_path: Path) -> None:
    both(tmp_path)
    (tmp_path / "prov" / "stray.json").write_text("{}")
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_tree(tmp_path, ENDPOINTS)
    assert "stray.json" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_a_fixture_directory_without_a_source_is_rejected(tmp_path: Path) -> None:
    both(tmp_path)
    write_tree(tmp_path, [record()], provider="unknown")
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_tree(tmp_path, ENDPOINTS)
    assert exc.value.provider == "unknown"


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_a_source_with_endpoints_but_no_directory_fails(tmp_path: Path) -> None:
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_tree(tmp_path, ENDPOINTS)
    assert exc.value.provider == "prov"


# Verifies: specs/lead-source-adapters/requirements.md#5.1
# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_every_shipped_fixture_has_one_record_and_every_endpoint_is_covered() -> None:
    registry = SourceRegistry.discover()
    declared = {
        name: registry.source_class(name).endpoints for name in registry.names()
    }
    manifests = validate_fixture_tree(FIXTURES_ROOT, declared)
    shipped = {
        p.relative_to(FIXTURES_ROOT).as_posix()
        for p in FIXTURES_ROOT.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    recorded = [
        f"{provider}/{r.file}" for provider, m in manifests.items() for r in m.fixtures
    ]
    assert sorted(recorded) == sorted(shipped)
    assert set(manifests) == set(declared)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_shipped_fixtures_do_not_claim_a_capture_that_never_happened() -> None:
    registry = SourceRegistry.discover()
    declared = {
        name: registry.source_class(name).endpoints for name in registry.names()
    }
    manifests = validate_fixture_tree(FIXTURES_ROOT, declared)
    origins = {r.origin for m in manifests.values() for r in m.fixtures}
    assert origins == {FixtureOrigin.HAND_MADE}


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_an_unverified_schema_carries_no_date(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        [record(schema_status="unverified", schema_verified_on=None)],
    )
    rec = load_manifest(tmp_path / "prov").fixtures[0]
    assert rec.schema_verified_on is None
    write_tree(tmp_path, [record(schema_status="unverified")])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "schema_verified_on" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_a_verified_schema_needs_a_date_and_status_is_required(tmp_path: Path) -> None:
    write_tree(tmp_path, [record(schema_verified_on=None)])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "schema_verified_on" in field_of(exc)
    r = record()
    del r["schema_status"]
    write_tree(tmp_path, [r])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "schema_status" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
@pytest.mark.parametrize("name", ["schema_verified_on", "recorded_on"])
def test_a_future_date_is_rejected(tmp_path: Path, name: str) -> None:
    write_tree(tmp_path, [record(**{name: "2999-01-01"})])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert name in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
@pytest.mark.parametrize("bad", [1790000000, "2026-10-04T00:00:00", "20261004"])
def test_dates_are_not_coerced(tmp_path: Path, bad: object) -> None:
    write_tree(tmp_path, [record(schema_verified_on=bad)])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "schema_verified_on" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_notes_are_bounded(tmp_path: Path) -> None:
    write_tree(tmp_path, [record(note="x" * 501)])
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "note" in field_of(exc)
    write_tree(tmp_path, [record(note="")])
    with pytest.raises(FixtureSchemaError):
        load_manifest(tmp_path / "prov")


# Verifies: specs/lead-source-adapters/requirements.md#5.1
@pytest.mark.parametrize(
    "bad",
    ["../../etc/passwd", "/etc/passwd.json", "a\\b.json", "sub/../x.json", "./x.json"],
)
def test_a_file_path_cannot_leave_the_fixture_directory(
    tmp_path: Path, bad: str
) -> None:
    write_tree(tmp_path, [record()])
    manifest = json.loads((tmp_path / "prov" / "manifest.json").read_text())
    manifest["fixtures"].append(record(file=bad, endpoint=None))
    (tmp_path / "prov" / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "file" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_a_symlinked_fixture_is_rejected(tmp_path: Path) -> None:
    both(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (tmp_path / "prov" / "lookup.json").unlink()
    (tmp_path / "prov" / "lookup.json").symlink_to(outside)
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_tree(tmp_path, ENDPOINTS)
    assert "lookup.json" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_loading_twice_gives_equal_manifests() -> None:
    registry = SourceRegistry.discover()
    declared = {
        name: registry.source_class(name).endpoints for name in registry.names()
    }
    assert validate_fixture_tree(FIXTURES_ROOT, declared) == validate_fixture_tree(
        FIXTURES_ROOT, declared
    )


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SECRET = re.compile(
    r"api_key|apikey|bearer\s|authorization|secret|password|[A-Za-z0-9_\-]{32,}",
    re.IGNORECASE,
)
_ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "example.test"}


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_no_fixture_or_manifest_carries_a_secret_or_a_real_address() -> None:
    files = [p for p in FIXTURES_ROOT.rglob("*") if p.is_file()]
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not _SECRET.search(text), path.name
        for address in _EMAIL.findall(text):
            domain = address.rsplit("@", 1)[1].lower()
            assert domain in _ALLOWED_EMAIL_DOMAINS, path.name


# Verifies: specs/lead-source-adapters/requirements.md#5.6
def test_shipped_records_claim_no_verification_nobody_made() -> None:
    # Every shipped fixture is a hand-made stand-in. The one documentation check made
    # so far is the provider-facts review of 2026-10-06 (official OpenAPI/SDK
    # repositories and search extracts of network-blocked pages); a record may claim
    # ``verified`` only with that date. Which records it covers is pinned per provider
    # in adapters/test_provider_plan_limits.py.
    registry = SourceRegistry.discover()
    declared = {
        name: registry.source_class(name).endpoints for name in registry.names()
    }
    for manifest in validate_fixture_tree(FIXTURES_ROOT, declared).values():
        for rec in manifest.fixtures:
            if rec.schema_status is SchemaStatus.VERIFIED:
                assert rec.schema_verified_on == date(2026, 10, 6)
            else:
                assert rec.schema_verified_on is None


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_an_outcome_variant_in_a_subdirectory_names_its_endpoint_by_file_name(
    tmp_path: Path,
) -> None:
    write_tree(
        tmp_path,
        [
            record(),
            record(file="lookup.json", endpoint="lookup"),
            record(file="empty/search.json", endpoint="search"),
        ],
    )
    manifest = validate_fixture_tree(tmp_path, ENDPOINTS)["prov"]
    assert [r.file for r in manifest.fixtures] == [
        "search.json",
        "lookup.json",
        "empty/search.json",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_an_outcome_variant_must_still_match_its_file_name(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        [record(), record(file="empty/search.json", endpoint="lookup")],
    )
    with pytest.raises(FixtureSchemaError) as exc:
        load_manifest(tmp_path / "prov")
    assert "endpoint" in field_of(exc)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_a_variant_does_not_stand_in_for_the_default_fixture(tmp_path: Path) -> None:
    # FixtureTransport serves only <endpoint>.json at the provider root, so an
    # endpoint whose only record is a variant would fail at run time.
    write_tree(
        tmp_path,
        [
            record(file="empty/search.json", endpoint="search"),
            record(file="lookup.json", endpoint="lookup"),
        ],
    )
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_tree(tmp_path, ENDPOINTS)
    assert exc.value.provider == "prov"
    assert "search.json" in field_of(exc)


ONLY_SEARCH: Mapping[str, Mapping[str, Endpoint]] = {"prov": {"search": SEARCH}}


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_a_symlinked_variant_file_or_variant_directory_is_rejected(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "search.json").write_text("{}")
    write_tree(tmp_path / "file", [record(), record(file="empty/search.json")])
    (tmp_path / "file" / "prov" / "empty" / "search.json").unlink()
    (tmp_path / "file" / "prov" / "empty" / "search.json").symlink_to(
        outside / "search.json"
    )
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_tree(tmp_path / "file", ONLY_SEARCH)
    assert "empty/search.json" in field_of(exc)
    write_tree(tmp_path / "dir", [record(), record(file="empty/search.json")])
    shutil.rmtree(tmp_path / "dir" / "prov" / "empty")
    (tmp_path / "dir" / "prov" / "empty").symlink_to(outside, target_is_directory=True)
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_tree(tmp_path / "dir", ONLY_SEARCH)
    assert exc.value.provider == "prov"
