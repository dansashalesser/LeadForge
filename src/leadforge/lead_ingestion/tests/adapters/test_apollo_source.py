"""Apollo Source adapter: credit-free Discovery with technographic targeting (12.1)."""

import json
import socket
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from leadforge.lead_ingestion.adapters.apollo import (
    MAX_PAGE,
    MAX_PER_PAGE,
    ApolloSource,
)
from leadforge.lead_ingestion.base_source import (
    Capability,
    Endpoint,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.errors import (
    MissingCredentialError,
    NormalizationError,
    SourceError,
    UndeclaredEndpointError,
)
from leadforge.lead_ingestion.models import ConfidenceOrigin, DataMode, UntrustedText
from leadforge.lead_ingestion.normalizer import unmapped_raw_paths
from leadforge.lead_ingestion.transport import RestTransport, TransportResponse

UID_PARAM = "currently_using_any_of_technology_uids[]"
SEARCH_PATH = "/api/v1/mixed_people/api_search"
FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "apollo"
REQUEST = SourceRequest(kind="discovery")
KEY = "test-key-not-real"
ENV = {"APOLLO_API_KEY": KEY}


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here may reach the network; a scripted or fixture transport is used."""

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("an Apollo test opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket.socket, "sendto", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


Responder = Callable[[Mapping[str, object]], TransportResponse]


def page(people: int, total: int, *, start: int = 0) -> TransportResponse:
    body = {
        "total_entries": total,
        "people": [
            {"id": f"p{start + i}", "first_name": "A", "last_name_obfuscated": "B***"}
            for i in range(people)
        ],
    }
    return TransportResponse(status=200, headers={}, body=body)


class Scripted:
    """A transport that records every call and answers from ``responder``."""

    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[tuple[Endpoint, Mapping[str, object], Mapping[str, str]]] = []

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.calls.append((endpoint, dict(params or {}), dict(headers)))
        return self.responder(params or {})


def live(
    transport: Scripted,
    *,
    vocabulary: Mapping[str, object] | None = None,
    per_page: int = MAX_PER_PAGE,
) -> ApolloSource:
    return ApolloSource(
        DataMode.LIVE,
        transport=transport,
        vocabulary=vocabulary,
        environ=ENV,
        per_page=per_page,
    )


ONE_TERM: Mapping[str, object] = {"datastax": ["datastax"]}


# Verifies: specs/lead-source-adapters/requirements.md#12.8
def test_declares_the_credit_free_search_endpoint_and_discovery_only() -> None:
    assert ApolloSource.name == "apollo"
    assert ApolloSource.capabilities == frozenset({Capability.SEARCH})
    assert ApolloSource.required_env == ("APOLLO_API_KEY",)
    paths = {(e.method, e.path) for e in ApolloSource.endpoints.values()}
    assert paths == {("POST", SEARCH_PATH)}


# Verifies: specs/lead-source-adapters/requirements.md#12.1
async def test_sends_the_key_in_x_api_key_and_never_as_a_bearer_token() -> None:
    transport = Scripted(lambda _: page(0, 0))
    await live(transport, vocabulary=ONE_TERM).fetch_raw(REQUEST)
    headers = transport.calls[0][2]
    assert headers["x-api-key"] == KEY
    assert not any(k.lower() == "authorization" for k in headers)
    assert "bearer" not in " ".join(headers.values()).lower()


# Verifies: specs/lead-source-adapters/requirements.md#12.1
async def test_live_run_without_the_key_fails_naming_the_variable() -> None:
    transport = Scripted(lambda _: page(0, 0))
    source = ApolloSource(DataMode.LIVE, transport=transport, environ={})
    with pytest.raises(MissingCredentialError) as caught:
        await source.fetch_raw(REQUEST)
    assert caught.value.missing == ("APOLLO_API_KEY",)
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#12.1
async def test_synthetic_run_needs_no_key_and_sends_none() -> None:
    transport = Scripted(lambda _: page(0, 0))
    source = ApolloSource(DataMode.SYNTHETIC, transport=transport, environ={})
    await source.fetch_raw(REQUEST)
    assert transport.calls[0][2] == {}


# Verifies: specs/lead-source-adapters/requirements.md#12.12
async def test_technology_filter_is_a_snake_case_uid_on_the_search_call() -> None:
    transport = Scripted(lambda _: page(0, 0))
    await live(transport, vocabulary={"t": ["apache_cassandra"]}).fetch_raw(REQUEST)
    endpoint, params, _ = transport.calls[0]
    assert endpoint.path == SEARCH_PATH
    assert params[UID_PARAM] == "apache_cassandra"


# Verifies: specs/lead-source-adapters/requirements.md#12.13
async def test_uids_come_from_the_supplied_vocabulary_not_from_code() -> None:
    transport = Scripted(lambda _: page(0, 0))
    vocabulary = {"x": ["datastax", "couchbase"], "y": "mongodb"}
    await live(transport, vocabulary=vocabulary).fetch_raw(REQUEST)
    assert [c[1][UID_PARAM] for c in transport.calls] == [
        "datastax",
        "couchbase",
        "mongodb",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#12.13
async def test_without_a_profile_the_adapters_declared_default_is_used() -> None:
    transport = Scripted(lambda _: page(0, 0))
    await live(transport).fetch_raw(REQUEST)
    assert {c[1][UID_PARAM] for c in transport.calls} == {
        "datastax",
        "apache_cassandra",
    }


# Verifies: specs/lead-source-adapters/requirements.md#12.13
def test_a_vocabulary_value_that_is_not_a_uid_or_list_of_uids_is_refused() -> None:
    transport = Scripted(lambda _: page(0, 0))
    with pytest.raises(ValueError, match="bad_term"):
        live(transport, vocabulary={"bad_term": {"uid": 1}})


# Verifies: specs/lead-source-adapters/requirements.md#12.13
def test_startup_warns_naming_a_uid_missing_from_the_supported_snapshot() -> None:
    transport = Scripted(lambda _: page(0, 0))
    with capture_logs() as logs:
        live(transport, vocabulary={"t": ["datastax", "datastaxx"]})
    unknown = [e for e in logs if e["event"] == "apollo_unknown_technology_uid"]
    assert [e["uid"] for e in unknown] == ["datastaxx"]
    assert unknown[0]["log_level"] == "warning"


# Verifies: specs/lead-source-adapters/requirements.md#12.13
def test_startup_is_silent_when_every_uid_is_supported() -> None:
    transport = Scripted(lambda _: page(0, 0))
    with capture_logs() as logs:
        live(transport, vocabulary={"t": ["datastax", "apache_cassandra"]})
    assert logs == []


# Verifies: specs/lead-source-adapters/requirements.md#12.13
async def test_a_uid_with_no_matches_across_the_run_warns_naming_it() -> None:
    def respond(params: Mapping[str, object]) -> TransportResponse:
        return page(0, 0) if params[UID_PARAM] == "bogus_uid" else page(2, 2)

    transport = Scripted(respond)
    source = live(transport, vocabulary={"t": ["datastax", "bogus_uid"]})
    with capture_logs() as logs:
        await source.fetch_raw(REQUEST)
    empty = [e for e in logs if e["event"] == "apollo_technology_no_matches"]
    assert [e["uid"] for e in empty] == ["bogus_uid"]


# Verifies: specs/lead-source-adapters/requirements.md#12.13
async def test_a_uid_that_matched_on_any_page_does_not_warn() -> None:
    transport = Scripted(lambda _: page(2, 2))
    with capture_logs() as logs:
        await live(transport, vocabulary=ONE_TERM).fetch_raw(REQUEST)
    assert [e for e in logs if e["event"] == "apollo_technology_no_matches"] == []


# Verifies: specs/lead-source-adapters/requirements.md#12.10
@pytest.mark.parametrize("asked", [100, 101, 500, 10_000])
async def test_page_size_is_never_more_than_one_hundred(asked: int) -> None:
    transport = Scripted(lambda _: page(1, 1))
    await live(transport, vocabulary=ONE_TERM, per_page=asked).fetch_raw(REQUEST)
    assert transport.calls[0][1]["per_page"] == 100


# Verifies: specs/lead-source-adapters/requirements.md#12.10
async def test_a_smaller_page_size_is_kept() -> None:
    transport = Scripted(lambda _: page(1, 1))
    await live(transport, vocabulary=ONE_TERM, per_page=25).fetch_raw(REQUEST)
    assert transport.calls[0][1]["per_page"] == 25


# Verifies: specs/lead-source-adapters/requirements.md#12.10
@pytest.mark.parametrize("bad", [0, -1])
def test_a_non_positive_page_size_is_refused(bad: int) -> None:
    with pytest.raises(ValueError, match="per_page"):
        live(Scripted(lambda _: page(0, 0)), per_page=bad)


# Verifies: specs/lead-source-adapters/requirements.md#12.10
async def test_paging_stops_at_the_five_hundred_page_ceiling() -> None:
    def respond(params: Mapping[str, object]) -> TransportResponse:
        number = params["page"]
        assert isinstance(number, int)
        return page(100, 10**9, start=number * 100)

    transport = Scripted(respond)
    batch = await live(transport, vocabulary=ONE_TERM).fetch_raw(REQUEST)
    pages = [c[1]["page"] for c in transport.calls]
    assert pages == list(range(1, MAX_PAGE + 1))
    assert MAX_PAGE == 500
    assert len(batch.payload["people"]) == 500 * 100


# Verifies: specs/lead-source-adapters/requirements.md#12.10
async def test_paging_stops_at_the_first_short_page() -> None:
    def respond(params: Mapping[str, object]) -> TransportResponse:
        return page(100, 150) if params["page"] == 1 else page(50, 150, start=100)

    transport = Scripted(respond)
    batch = await live(transport, vocabulary=ONE_TERM).fetch_raw(REQUEST)
    assert [c[1]["page"] for c in transport.calls] == [1, 2]
    assert len(batch.payload["people"]) == 150


# Verifies: specs/lead-source-adapters/requirements.md#12.12
async def test_a_person_found_under_two_uids_is_kept_once() -> None:
    transport = Scripted(lambda _: page(2, 2))
    source = live(transport, vocabulary={"t": ["datastax", "couchbase"]})
    batch = await source.fetch_raw(REQUEST)
    assert [p["id"] for p in batch.payload["people"]] == ["p0", "p1"]


# Verifies: specs/lead-source-adapters/requirements.md#12.8
async def test_a_non_success_status_is_not_swallowed() -> None:
    transport = Scripted(lambda _: TransportResponse(status=500, headers={}, body=None))
    with pytest.raises(SourceError, match="500"):
        await live(transport, vocabulary=ONE_TERM).fetch_raw(REQUEST)


# Verifies: specs/lead-source-adapters/requirements.md#12.8
async def test_a_body_that_is_not_a_search_result_is_a_normalization_error() -> None:
    transport = Scripted(
        lambda _: TransportResponse(status=200, headers={}, body=["nope"])
    )
    with pytest.raises(NormalizationError):
        await live(transport, vocabulary=ONE_TERM).fetch_raw(REQUEST)


async def fixture_batch() -> tuple[ApolloSource, RawBatch]:
    transport = ApolloSource.build_transport(DataMode.SYNTHETIC)
    source = ApolloSource(DataMode.SYNTHETIC, transport=transport, vocabulary=ONE_TERM)
    return source, await source.fetch_raw(REQUEST)


# Verifies: specs/lead-source-adapters/requirements.md#12.2
async def test_fixture_search_contributes_identity_firmographics_and_no_contact() -> (
    None
):
    source, batch = await fixture_batch()
    contributions = source.normalize_checked(batch)
    assert len(contributions) == 2
    first = contributions[0]
    assert first.source_name == "apollo"
    plain = {
        path: value.value if isinstance(value, UntrustedText) else value
        for path, value in first.values.items()
    }
    assert plain == {
        "person.provider_id": "apollo-person-1",
        "person.first_name": "Ada",
        "person.last_name": "Lo***",
        "person.title": "VP Engineering",
        "company.name": "Example Data Corp",
    }
    assert {p.canonical_path for p in first.provenance} == set(first.values)
    # Provider free text is untrusted by construction (1.6, 22.1); the id is not text.
    untrusted = {p.canonical_path for p in first.provenance if p.untrusted}
    assert untrusted == {
        "person.first_name",
        "person.last_name",
        "person.title",
        "company.name",
    }
    for path in untrusted:
        assert isinstance(first.values[path], UntrustedText)
    assert all(p.confidence_origin is ConfidenceOrigin.NONE for p in first.provenance)
    assert all(p.data_mode is DataMode.SYNTHETIC for p in first.provenance)
    assert not [k for k in first.values if "email" in k or "phone" in k]


# Verifies: specs/lead-source-adapters/requirements.md#12.2
async def test_every_fixture_field_is_mapped_or_intentionally_ignored() -> None:
    _, batch = await fixture_batch()
    for person in batch.payload["people"]:
        assert (
            unmapped_raw_paths(person, ApolloSource.RULES, ApolloSource.IGNORED) == []
        )


# Verifies: specs/lead-source-adapters/requirements.md#12.2
async def test_technology_stack_evidence_is_contributed_when_apollo_returns_it() -> (
    None
):
    technologies = [{"uid": "datastax", "name": "DataStax", "category": "Databases"}]
    person = {
        "id": "p1",
        "first_name": "A",
        "last_name_obfuscated": "B***",
        "linkedin_url": "http://www.linkedin.com/in/a",
        "organization": {"name": "Org", "current_technologies": technologies},
    }
    transport = Scripted(lambda _: page(0, 0))
    source = live(transport, vocabulary=ONE_TERM)
    [contribution] = source.normalize_checked(
        RawBatch(source_name="apollo", payload={"people": [person]})
    )
    assert contribution.values["company.technologies"] == technologies
    assert contribution.values["person.linkedin_url"] == "http://www.linkedin.com/in/a"


# Verifies: specs/lead-source-adapters/requirements.md#12.2
def test_normalizing_a_person_with_the_wrong_shape_names_the_path() -> None:
    transport = Scripted(lambda _: page(0, 0))
    source = live(transport, vocabulary=ONE_TERM)
    bad = {"people": [{"id": "p1", "first_name": 7}]}
    with pytest.raises(NormalizationError) as caught:
        source.normalize(RawBatch(source_name="apollo", payload=bad))
    assert "first_name" in str(caught.value)


def test_snapshot_fixture_is_valid_csv_with_a_uid_column() -> None:
    header = (FIXTURE_DIR / "supported_technologies.csv").read_text().splitlines()[0]
    assert header.split(",")[0] == "uid"
    assert json.loads((FIXTURE_DIR / "search.json").read_text())["people"]


# Verifies: specs/lead-source-adapters/requirements.md#12.1
async def test_the_key_never_reaches_a_log_a_repr_or_an_error_message() -> None:
    transport = Scripted(lambda _: TransportResponse(status=401, headers={}, body=None))
    source = live(transport, vocabulary=ONE_TERM)
    with capture_logs() as logs, pytest.raises(SourceError) as caught:
        await source.fetch_raw(REQUEST)
    assert KEY not in str(caught.value) + repr(caught.value) + repr(source)
    assert KEY not in repr(logs)
    bad = {"people": [{"id": "p1", "first_name": 7}]}
    with pytest.raises(NormalizationError) as shape:
        source.normalize(RawBatch(source_name="apollo", payload=bad))
    assert KEY not in str(shape.value) + repr(shape.value)


# Verifies: specs/lead-source-adapters/requirements.md#12.1
async def test_a_missing_key_error_names_the_variable_and_holds_no_value() -> None:
    source = ApolloSource(
        DataMode.LIVE,
        transport=Scripted(lambda _: page(0, 0)),
        environ={"APOLLO_API_KEY": "  "},
    )
    with pytest.raises(MissingCredentialError) as caught:
        await source.fetch_raw(REQUEST)
    assert "APOLLO_API_KEY" in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#12.8
async def test_only_the_declared_read_only_search_endpoint_is_reachable() -> None:
    assert all(e.read_only is True for e in ApolloSource.endpoints.values())
    transport = ApolloSource.build_transport(DataMode.SYNTHETIC)
    enrich = Endpoint(method="POST", path="/api/v1/people/match", bucket="default")
    with pytest.raises(UndeclaredEndpointError):
        await transport.send(enrich, params=None, json_body=None, headers={})
    with pytest.raises(TypeError):
        ApolloSource.endpoints["enrich"] = enrich  # type: ignore[index]


# Verifies: specs/lead-source-adapters/requirements.md#12.1
async def test_the_live_transport_is_declared_over_https_and_search_only() -> None:
    assert ApolloSource.base_url == "https://api.apollo.io"
    transport = ApolloSource.build_transport(DataMode.LIVE)
    assert isinstance(transport, RestTransport)
    await transport.aclose()


# Verifies: specs/lead-source-adapters/requirements.md#12.1
async def test_a_synthetic_run_builds_no_live_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a live transport was built in synthetic mode")

    monkeypatch.setattr("leadforge.lead_ingestion.transport.RestTransport", refuse)
    source, batch = await fixture_batch()
    assert len(batch.payload["people"]) == 2
    assert source.data_mode is DataMode.SYNTHETIC


# Verifies: specs/lead-source-adapters/requirements.md#12.2
async def test_a_search_asks_no_field_question_so_absence_is_never_recorded() -> None:
    source, batch = await fixture_batch()
    contributions = source.normalize_checked(batch)
    assert all(c.absences == () for c in contributions)
    assert "person.linkedin_url" not in contributions[0].values


# Verifies: specs/lead-source-adapters/requirements.md#12.2
@pytest.mark.parametrize(
    ("person", "raw_path", "canonical"),
    [
        ({"id": 5}, "id", "person.provider_id"),
        ({"first_name": "A"}, "id", "person.provider_id"),
        (
            {"id": "p1", "organization": {"current_technologies": [{"uid": 3}]}},
            "organization.current_technologies.0.uid",
            "company.technologies",
        ),
    ],
)
def test_the_raw_model_refuses_a_wrong_or_missing_field_naming_both_paths(
    person: dict[str, object], raw_path: str, canonical: str
) -> None:
    source = live(Scripted(lambda _: page(0, 0)), vocabulary=ONE_TERM)
    with pytest.raises(NormalizationError) as caught:
        source.normalize(RawBatch(source_name="apollo", payload={"people": [person]}))
    text = str(caught.value)
    assert "apollo" in text
    assert raw_path in text
    assert canonical in text
