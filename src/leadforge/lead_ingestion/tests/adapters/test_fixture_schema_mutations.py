"""A broken fixture fails each adapter's declared raw schema, by field (17.2)."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from leadforge.lead_ingestion.errors import FixtureSchemaError
from leadforge.lead_ingestion.fixture_metadata import load_manifest
from leadforge.lead_ingestion.fixture_schema import (
    validate_fixture_file,
    validate_fixture_schemas,
    validate_provider_fixtures,
)
from leadforge.lead_ingestion.registry import SourceRegistry

FIXTURES_ROOT = Path(__file__).resolve().parent.parent.parent / "fixtures"
CANARY = "PAYLOAD-CANARY-4417"
_REGISTRY = SourceRegistry.discover()
SOURCES = {name: _REGISTRY.source_class(name) for name in _REGISTRY.names()}


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


MUTATIONS: list[tuple[str, str, str, Callable[[Any], None], str]] = [
    ("apollo", "search.json", "missing id", lambda b: b["people"][0].pop("id"), "id"),
    (
        "apollo",
        "search.json",
        "wrong type",
        lambda b: b["people"][0].update(title=CANARY, first_name=5),
        "first_name",
    ),
    ("apollo", "search.json", "no people", lambda b: b.pop("people"), "people"),
    (
        "apollo",
        "match.json",
        "bad confidence",
        lambda b: b["person"].update(match_confidence=CANARY),
        "person.match_confidence",
    ),
    (
        "apollo",
        "match.json",
        "missing person id",
        lambda b: b["person"].pop("id"),
        "person",  # a hit without its id: the person-level check names ``person``
    ),
    (
        "hubspot",
        "contact_search.json",
        "bool opt-out",
        lambda b: b["results"][0]["properties"].update(hs_email_optout=True),
        "contact.properties.hs_email_optout",
    ),
    (
        "hubspot",
        "contact_search.json",
        "missing id",
        lambda b: b["results"][0].pop("id"),
        "contact.id",
    ),
    (
        "hubspot",
        "contact_search.json",
        "no results",
        lambda b: b.update(results=CANARY),
        "results",
    ),
    (
        "hubspot",
        "deal_search.json",
        "text total",
        lambda b: b.update(total=CANARY),
        "total",
    ),
    (
        "hubspot",
        "deal_search.json",
        "negative total",
        lambda b: b.update(total=-1),
        "total",
    ),
    (
        "hunter",
        "domain_search.json",
        "non-text domain",  # null is documented (no results); a number is not
        lambda b: b["data"].update(domain=7),
        "data.domain",
    ),
    (
        "hunter",
        "domain_search.json",
        "bad score",
        lambda b: b["data"]["emails"][0].update(confidence=101),
        "data.emails.0.confidence",
    ),
    (
        "hunter",
        "email_finder.json",
        "bad score",
        lambda b: b["data"].update(score=CANARY),
        "data.score",
    ),
    (
        "hunter",
        "email_verifier.json",
        "missing status",
        lambda b: b["data"].pop("status"),
        "data.status",
    ),
    (
        "google_search",
        "search.json",
        "missing link",
        lambda b: b["organic_results"][0].pop("link"),
        "result.link",
    ),
    (
        "google_search",
        "search.json",
        "wrong type",
        lambda b: b["organic_results"][1].update(title=CANARY, snippet=7),
        "result.snippet",
    ),
    (
        "google_search",
        "search.json",
        "organic not a list",
        lambda b: b.update(organic_results=CANARY),
        "organic_results",
    ),
]


# Verifies: specs/lead-source-adapters/requirements.md#5.4
@pytest.mark.parametrize(
    ("provider", "file", "case", "mutate", "path"),
    MUTATIONS,
    ids=[f"{m[0]}/{m[1]}:{m[2]}" for m in MUTATIONS],
)
def test_a_mutated_fixture_fails_naming_provider_and_field(
    tmp_path: Path,
    provider: str,
    file: str,
    case: str,
    mutate: Callable[[Any], None],
    path: str,
) -> None:
    root = _copy(tmp_path)
    _edit(root, provider, file, mutate)
    error = _fails(root, provider, file)
    assert error.field == f"{file}:{path}"
    assert CANARY not in f"{error} {error.field} {error!r}"


# Verifies: specs/lead-source-adapters/requirements.md#5.4
def test_the_first_failure_stops_the_provider_check(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _edit(root, "hunter", "domain_search.json", lambda b: b["data"].update(domain=7))
    _edit(root, "hunter", "email_verifier.json", lambda b: b["data"].pop("status"))
    with pytest.raises(FixtureSchemaError) as exc:
        validate_provider_fixtures(root / "hunter", SOURCES["hunter"])
    assert exc.value.field == "domain_search.json:data.domain"
    with pytest.raises(FixtureSchemaError) as whole:
        validate_fixture_schemas(root, SOURCES)
    assert whole.value.provider == "hunter"


# Verifies: specs/lead-source-adapters/requirements.md#5.4
def test_the_reference_csv_is_checked_by_the_adapters_own_loader(
    tmp_path: Path,
) -> None:
    root = _copy(tmp_path)
    csv_file = root / "apollo" / "supported_technologies_excerpt.csv"
    csv_file.write_text("uid,name\ndatastax,DataStax\n", encoding="utf-8")
    error = _fails(root, "apollo", "supported_technologies_excerpt.csv")
    assert error.field == "supported_technologies_excerpt.csv:Technology"
    csv_file.write_text("Category,Technology\n", encoding="utf-8")
    assert _fails(root, "apollo", "supported_technologies_excerpt.csv").field == (
        "supported_technologies_excerpt.csv:Technology"
    )
    csv_file.write_text(f"Category,Technology\n{CANARY},\n", encoding="utf-8")
    error = _fails(root, "apollo", "supported_technologies_excerpt.csv")
    assert CANARY not in f"{error} {error.field}"
    csv_file.write_bytes(b"\xff\xfe\x00")
    assert _fails(root, "apollo", "supported_technologies_excerpt.csv").field == (
        "supported_technologies_excerpt.csv"
    )


# Verifies: specs/lead-source-adapters/requirements.md#5.4
@pytest.mark.parametrize(
    "content",
    [
        b"Category,Technology\n" + b"x" * 200_000 + b",y\n",
        b'Category,Technology\n"a\nb\n',
        b"Category,Technology\n,\n",
    ],
    ids=["oversized-field", "unterminated-quote", "blank-technology"],
)
def test_a_hostile_reference_csv_fails_by_a_named_error(
    tmp_path: Path, content: bytes
) -> None:
    root = _copy(tmp_path)
    (root / "apollo" / "supported_technologies_excerpt.csv").write_bytes(content)
    error = _fails(root, "apollo", "supported_technologies_excerpt.csv")
    assert error.field.startswith("supported_technologies_excerpt.csv")
    assert error.__cause__ is None
    assert error.__context__ is None
