"""A stray field in any record of any adapter's fixture fails by path (17.3)."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.adapters.hunter import HunterSource
from leadforge.lead_ingestion.base_source import BaseLeadSource
from leadforge.lead_ingestion.errors import FixtureSchemaError
from leadforge.lead_ingestion.fixture_fields import validate_fixture_field_coverage
from leadforge.lead_ingestion.fixture_metadata import load_manifest
from leadforge.lead_ingestion.registry import SourceRegistry

FIXTURES_ROOT = Path(__file__).resolve().parent.parent.parent / "fixtures"
UNKNOWN = "zz_unknown_field"
_REGISTRY = SourceRegistry.discover()
SOURCES = {name: _REGISTRY.source_class(name) for name in _REGISTRY.names()}


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_ROOT, root)
    return root


def _check(root: Path, provider: str, file: str) -> None:
    record = next(r for r in load_manifest(root / provider).fixtures if r.file == file)
    validate_fixture_field_coverage(root / provider, SOURCES[provider], record)


def _edit(root: Path, provider: str, file: str, where: Callable[[Any], Any]) -> None:
    path = root / provider / file
    body = json.loads(path.read_text(encoding="utf-8"))
    where(body).update({UNKNOWN: "x"})
    path.write_text(json.dumps(body), encoding="utf-8")


Where = Callable[[Any], Any]
STRAY: list[tuple[str, str, Where, str]] = [
    ("apollo", "search.json", lambda b: b["people"][0], "people." + UNKNOWN),
    (
        "apollo",
        "search.json",
        lambda b: b["people"][1]["organization"],
        "people.organization." + UNKNOWN,
    ),
    ("apollo", "match.json", lambda b: b["person"], "person." + UNKNOWN),
    (
        "apollo",
        "match.json",
        lambda b: b["person"]["organization"],
        "person.organization." + UNKNOWN,
    ),
    (
        "hubspot",
        "contact_search.json",
        lambda b: b["results"][0],
        "results." + UNKNOWN,
    ),
    (
        "hubspot",
        "contact_search.json",
        lambda b: b["results"][0]["properties"],
        "results.properties." + UNKNOWN,
    ),
    ("hunter", "domain_search.json", lambda b: b["data"], "data." + UNKNOWN),
    (
        "hunter",
        "domain_search.json",
        lambda b: b["data"]["emails"][1],
        "data.emails." + UNKNOWN,
    ),
    (
        "hunter",
        "domain_search.json",
        lambda b: b["data"]["emails"][1]["verification"],
        "data.emails.verification." + UNKNOWN,
    ),
    ("hunter", "email_finder.json", lambda b: b["data"], "data." + UNKNOWN),
    (
        "hunter",
        "email_finder.json",
        lambda b: b["data"]["verification"],
        "data.verification." + UNKNOWN,
    ),
    ("hunter", "email_verifier.json", lambda b: b["data"], "data." + UNKNOWN),
    (
        "google_search",
        "search.json",
        lambda b: b["organic_results"][1],
        "organic_results." + UNKNOWN,
    ),
]


# Verifies: specs/lead-source-adapters/requirements.md#5.5
@pytest.mark.parametrize(
    ("provider", "file", "where", "path"),
    STRAY,
    ids=[f"{p}/{f}:{path}" for p, f, _, path in STRAY],
)
def test_a_stray_field_deep_in_a_record_fails_naming_its_path(
    tmp_path: Path, provider: str, file: str, where: Where, path: str
) -> None:
    root = _copy(tmp_path)
    _check(root, provider, file)
    _edit(root, provider, file, where)
    with pytest.raises(FixtureSchemaError) as exc:
        _check(root, provider, file)
    assert (exc.value.provider, exc.value.field) == (provider, f"{file}:{path}")


# Verifies: specs/lead-source-adapters/requirements.md#5.5
@pytest.mark.parametrize(
    ("provider", "file", "where"),
    [
        ("hubspot", "deal_search.json", lambda b: b["results"][0]),
    ],
)
def test_a_field_inside_a_declared_ignored_list_is_tolerated(
    tmp_path: Path, provider: str, file: str, where: Where
) -> None:
    root = _copy(tmp_path)
    _edit(root, provider, file, where)
    _check(root, provider, file)


# Verifies: specs/lead-source-adapters/requirements.md#5.5
@pytest.mark.parametrize(
    ("provider", "file", "where", "path"),
    [
        ("hunter", "domain_search.json", lambda b: b["meta"], "meta." + UNKNOWN),
        (
            "hunter",
            "email_finder.json",
            lambda b: b["meta"]["params"],
            "meta.params." + UNKNOWN,
        ),
        (
            "google_search",
            "search.json",
            lambda b: b["search_metadata"],
            "search_metadata." + UNKNOWN,
        ),
        (
            "google_search",
            "search.json",
            lambda b: b["search_information"],
            "search_information." + UNKNOWN,
        ),
    ],
)
def test_an_envelope_ignore_lists_leaves_so_a_stray_envelope_field_still_fails(
    tmp_path: Path, provider: str, file: str, where: Where, path: str
) -> None:
    root = _copy(tmp_path)
    _edit(root, provider, file, where)
    with pytest.raises(FixtureSchemaError) as exc:
        _check(root, provider, file)
    assert (exc.value.provider, exc.value.field) == (provider, f"{file}:{path}")


# Verifies: specs/lead-source-adapters/requirements.md#5.5
@pytest.mark.parametrize(
    ("source", "attribute", "entry", "file", "path"),
    [
        (ApolloSource, "IGNORED", "last_refreshed_at", "search.json", "people."),
        (ApolloSource, "IGNORED", "match_confidence", "match.json", ""),
        (ApolloSource, "SEARCH_ENVELOPE_IGNORED", "total_entries", "search.json", ""),
        (
            HubSpotSource,
            "IGNORED",
            "contact.createdAt",
            "contact_search.json",
            "results.",
        ),
        (HubSpotSource, "CONTACT_SEARCH_IGNORED", "total", "contact_search.json", ""),
        (HubSpotSource, "DEAL_SEARCH_IGNORED", "results", "deal_search.json", ""),
        (
            HunterSource,
            "IGNORED",
            "email.seniority",
            "domain_search.json",
            "data.emails.",
        ),
        (HunterSource, "FINDER_IGNORED", "twitter", "email_finder.json", "data."),
        (HunterSource, "VERIFIER_IGNORED", "score", "email_verifier.json", "data."),
        (HunterSource, "ENVELOPE_IGNORED", "meta.limit", "domain_search.json", ""),
        (
            GoogleSearchSource,
            "IGNORED",
            "result.position",
            "search.json",
            "organic_results.",
        ),
        (
            GoogleSearchSource,
            "ENVELOPE_IGNORED",
            "search_metadata.status",
            "search.json",
            "",
        ),
    ],
)
def test_dropping_an_ignore_entry_makes_the_field_it_covered_fail(
    monkeypatch: pytest.MonkeyPatch,
    source: type[BaseLeadSource],
    attribute: str,
    entry: str,
    file: str,
    path: str,
) -> None:
    provider = source.name
    record = next(
        r for r in load_manifest(FIXTURES_ROOT / provider).fixtures if r.file == file
    )
    validate_fixture_field_coverage(FIXTURES_ROOT / provider, source, record)
    monkeypatch.setattr(source, attribute, getattr(source, attribute) - {entry})
    with pytest.raises(FixtureSchemaError) as exc:
        validate_fixture_field_coverage(FIXTURES_ROOT / provider, source, record)
    assert exc.value.provider == provider
    assert exc.value.field.startswith(f"{file}:{path}")
    assert entry.rsplit(".", 1)[-1] in exc.value.field
