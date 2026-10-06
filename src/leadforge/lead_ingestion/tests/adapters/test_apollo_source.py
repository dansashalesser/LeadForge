"""Apollo Source adapter: credit-free Discovery with technographic targeting (12.1)."""

import asyncio
import json
import socket
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from leadforge.lead_ingestion.adapters.apollo import (
    MAX_PAGE,
    MAX_PER_PAGE,
    RUNG_CONFIDENCE,
    ApolloSource,
    credits_in,
)
from leadforge.lead_ingestion.base_source import (
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    EnrichmentRequest,
    LeadContribution,
    RawBatch,
    SourceRequest,
    enrichment_order,
    enrichment_sort_key,
)
from leadforge.lead_ingestion.errors import (
    MissingCredentialError,
    NormalizationError,
    SourceError,
    SourceRateLimited,
    SourceTransient,
    SourceUnauthorized,
    UndeclaredEndpointError,
)
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
    UntrustedText,
)
from leadforge.lead_ingestion.normalizer import unmapped_raw_paths
from leadforge.lead_ingestion.orchestrator import SourceCallLedger, SourceStatus
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.throttle import SourceThrottle
from leadforge.lead_ingestion.transport import RestTransport, TransportResponse

UID_PARAM = "currently_using_any_of_technology_uids[]"
SEARCH_PATH = "/api/v1/mixed_people/api_search"
MATCH_PATH = "/api/v1/people/match"
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
def test_declares_the_credit_free_search_and_the_one_credit_match_endpoint() -> None:
    assert ApolloSource.name == "apollo"
    assert ApolloSource.required_env == ("APOLLO_API_KEY",)
    paths = {(e.method, e.path) for e in ApolloSource.endpoints.values()}
    assert paths == {("POST", SEARCH_PATH), ("POST", MATCH_PATH)}


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
async def test_only_the_two_declared_read_only_endpoints_are_reachable() -> None:
    assert all(e.read_only is True for e in ApolloSource.endpoints.values())
    transport = ApolloSource.build_transport(DataMode.SYNTHETIC)
    bulk = Endpoint(method="POST", path="/api/v1/people/bulk_match", bucket="default")
    with pytest.raises(UndeclaredEndpointError):
        await transport.send(bulk, params=None, json_body=None, headers={})
    with pytest.raises(TypeError):
        ApolloSource.endpoints["bulk"] = bulk  # type: ignore[index]


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


# ---------------------------------------------------------------- 12.2: enrichment

MATCH = ApolloSource.endpoints["match"]
SEARCH = ApolloSource.endpoints["search"]


def lead(provider_id: str | None, *, source: str = "apollo") -> LeadContribution:
    values = {} if provider_id is None else {"person.provider_id": provider_id}
    provenance = (
        ()
        if provider_id is None
        else (
            FieldProvenance(
                canonical_path="person.provider_id",
                source_name=source,
                data_mode=DataMode.LIVE,
                fetched_at=datetime.now(UTC),
                raw_field_path="id",
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            ),
        )
    )
    return LeadContribution(source_name=source, values=values, provenance=provenance)


def enrich(*leads: LeadContribution) -> EnrichmentRequest:
    return EnrichmentRequest(kind="enrich", work_list=leads)


def matched(confidence: str = "high", person_id: str = "p1") -> TransportResponse:
    body: dict[str, object] = {"match_confidence": confidence}
    body["person"] = (
        None
        if confidence == "none"
        else {"id": person_id, "first_name": "A", "last_name": "Bee"}
    )
    return TransportResponse(status=200, headers={}, body=body)


def enrichment_source(transport: Scripted) -> ApolloSource:
    return live(transport, vocabulary=ONE_TERM)


# Verifies: specs/lead-source-adapters/requirements.md#12.3
def test_declares_both_discovery_and_enrichment_capabilities() -> None:
    assert ApolloSource.capabilities == frozenset(
        {Capability.SEARCH, Capability.ENRICH}
    )


# Verifies: specs/lead-source-adapters/requirements.md#12.8
def test_declared_cost_orders_apollo_enrichment_after_free_sources() -> None:
    assert ApolloSource.cost_class is CostClass.PAID
    assert ApolloSource.charge_unit is ChargeUnit.PER_LEAD
    assert ApolloSource.yields_suppression is False  # match returns no opt-out flag

    class Free(ApolloSource):
        name = "free-after-apollo-alphabetically"
        cost_class = CostClass.FREE
        yields_suppression = True

    assert enrichment_sort_key(ApolloSource)[0] is True
    transport = Scripted(lambda _: matched())
    apollo = ApolloSource(DataMode.SYNTHETIC, transport=transport, vocabulary=ONE_TERM)
    free = Free(DataMode.SYNTHETIC, transport=transport, vocabulary=ONE_TERM)
    assert enrichment_order([apollo, free]) == [free, apollo]


# Verifies: specs/lead-source-adapters/requirements.md#12.8
def test_the_match_endpoint_is_the_people_match_post_in_the_declared_bucket() -> None:
    assert (MATCH.method, MATCH.path) == ("POST", MATCH_PATH)
    assert MATCH.read_only is True
    assert MATCH.bucket in ApolloSource.rate_limit


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_enrichment_matches_each_work_list_lead_once_by_apollo_id() -> None:
    transport = Scripted(lambda params: matched(person_id=str(params["id"])))
    source = enrichment_source(transport)
    batch = await source.fetch_raw(enrich(lead("p1"), lead("p2"), lead("p1")))
    assert [(c[0], c[1]) for c in transport.calls] == [
        (MATCH, {"id": "p1"}),
        (MATCH, {"id": "p2"}),
    ]
    assert [m["lookup"] for m in batch.payload["matches"]] == ["p1", "p2"]


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_enrichment_makes_no_call_for_an_empty_work_list_or_unkeyed_lead() -> (
    None
):
    transport = Scripted(lambda _: matched())
    source = enrichment_source(transport)
    await source.fetch_raw(enrich())
    await source.fetch_raw(enrich(lead(None), lead("hs-9", source="other")))
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_discovery_never_calls_match_and_enrichment_never_searches() -> None:
    transport = Scripted(lambda params: page(0, 0) if "page" in params else matched())
    source = enrichment_source(transport)
    await source.fetch_raw(REQUEST)
    await source.fetch_raw(enrich(lead("p1")))
    assert [c[0] for c in transport.calls] == [SEARCH, MATCH]


# Verifies: specs/lead-source-adapters/requirements.md#12.1
async def test_the_match_call_carries_the_key_in_x_api_key_and_no_bearer() -> None:
    transport = Scripted(lambda _: matched())
    await enrichment_source(transport).fetch_raw(enrich(lead("p1")))
    headers = transport.calls[0][2]
    assert headers == {"x-api-key": KEY}


# Verifies: specs/lead-source-adapters/requirements.md#12.14
async def test_no_phone_waterfall_or_webhook_is_requested() -> None:
    transport = Scripted(lambda _: matched())
    await enrichment_source(transport).fetch_raw(enrich(lead("p1")))
    _, params, _ = transport.calls[0]
    assert set(params) == {"id"}
    assert not [e for e in ApolloSource.endpoints.values() if "webhook" in e.path]
    assert not [r for r in ApolloSource.MATCH_RULES if "phone" in r.canonical_path]


# Verifies: specs/lead-source-adapters/requirements.md#12.8
async def test_a_non_success_match_status_is_not_swallowed() -> None:
    transport = Scripted(lambda _: TransportResponse(status=402, headers={}, body=None))
    with pytest.raises(SourceError):
        await enrichment_source(transport).fetch_raw(enrich(lead("p1")))


# Verifies: specs/lead-source-adapters/requirements.md#12.8
async def test_a_match_body_that_is_not_an_object_is_a_normalization_error() -> None:
    transport = Scripted(
        lambda _: TransportResponse(status=200, headers={}, body=["x"])
    )
    with pytest.raises(NormalizationError):
        await enrichment_source(transport).fetch_raw(enrich(lead("p1")))


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_a_none_confidence_match_contributes_no_lead_and_no_credit() -> None:
    transport = Scripted(lambda _: matched("none"))
    source = enrichment_source(transport)
    batch = await source.fetch_raw(enrich(lead("p1")))
    assert source.normalize_checked(batch) == []
    assert credits_in(batch) == 0
    # The no-match outcome stays in the raw evidence.
    assert batch.payload["matches"][0]["response"]["match_confidence"] == "none"


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_one_credit_is_counted_per_real_match_only() -> None:
    answers = {"p1": "high", "p2": "none", "p3": "low", "p4": "medium"}
    transport = Scripted(
        lambda params: matched(answers[str(params["id"])], str(params["id"]))
    )
    source = enrichment_source(transport)
    batch = await source.fetch_raw(enrich(*(lead(i) for i in answers)))
    assert credits_in(batch) == 3
    assert len(source.normalize_checked(batch)) == 3


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_a_no_match_is_logged_naming_the_lookup_and_never_the_key() -> None:
    transport = Scripted(lambda _: matched("none"))
    source = enrichment_source(transport)
    batch = await source.fetch_raw(enrich(lead("p1")))
    with capture_logs() as logs:
        source.normalize(batch)
    assert [(e["event"], e["lookup"]) for e in logs] == [("apollo_no_match", "p1")]
    assert KEY not in repr(logs)


# Verifies: specs/lead-source-adapters/requirements.md#12.9
def test_a_billed_match_without_a_person_is_a_normalization_error() -> None:
    source = enrichment_source(Scripted(lambda _: matched()))
    body = {"match_confidence": "high", "person": None}
    batch = RawBatch(
        source_name="apollo",
        payload={"matches": [{"lookup": "p1", "response": body}]},
    )
    with pytest.raises(NormalizationError) as caught:
        source.normalize(batch)
    assert "person" in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#12.9
def test_an_unknown_match_confidence_is_refused_not_billed_silently() -> None:
    source = enrichment_source(Scripted(lambda _: matched()))
    body = {"match_confidence": "certain", "person": {"id": "p1"}}
    batch = RawBatch(
        source_name="apollo",
        payload={"matches": [{"lookup": "p1", "response": body}]},
    )
    with pytest.raises(NormalizationError) as caught:
        source.normalize(batch)
    assert "match_confidence" in str(caught.value)


async def fixture_match() -> tuple[ApolloSource, RawBatch]:
    transport = ApolloSource.build_transport(DataMode.SYNTHETIC)
    source = ApolloSource(DataMode.SYNTHETIC, transport=transport, vocabulary=ONE_TERM)
    return source, await source.fetch_raw(enrich(lead("apollo-person-1")))


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_fixture_match_contributes_contact_identity_with_provenance() -> None:
    source, batch = await fixture_match()
    [contribution] = source.normalize_checked(batch)
    plain = {
        path: value.value if isinstance(value, UntrustedText) else value
        for path, value in contribution.values.items()
    }
    assert plain == {
        "person.provider_id": "apollo-person-1",
        "person.first_name": "Ada",
        "person.last_name": "Lovelace",
        "person.title": "VP Engineering",
        "person.linkedin_url": "http://www.linkedin.com/in/ada-lovelace",
        "person.email": "ada@example.com",
        "person.email_status": "verified",
        "company.name": "Example Data Corp",
        "company.technologies": [
            {"uid": "datastax", "name": "DataStax", "category": "Databases"}
        ],
        # organization.primary_domain, registrable (ADR-0006, 2026-10-06).
        "company.domain": "example.com",
    }
    # One provenance record per populated field. Apollo states no per-field certainty;
    # the confidence is ours, by the lookup rung that found the person (follow-up
    # 2026-10-06, supersedes "origin none"): an id hit is a strong rung.
    assert {p.canonical_path for p in contribution.provenance} == set(
        contribution.values
    )
    assert all(
        (p.confidence_origin, p.confidence)
        == (ConfidenceOrigin.HEURISTIC, RUNG_CONFIDENCE["id"])
        for p in contribution.provenance
    )
    assert all(p.data_mode is DataMode.SYNTHETIC for p in contribution.provenance)
    untrusted = {p.canonical_path for p in contribution.provenance if p.untrusted}
    assert untrusted == {
        "person.first_name",
        "person.last_name",
        "person.title",
        "company.name",
    }
    for path in untrusted:
        assert isinstance(contribution.values[path], UntrustedText)
    assert contribution.absences == ()
    assert credits_in(batch) == 1


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_every_match_fixture_field_is_mapped_or_intentionally_ignored() -> None:
    _, batch = await fixture_match()
    response = batch.payload["matches"][0]["response"]
    assert (
        unmapped_raw_paths(response, ApolloSource.MATCH_RULES, ApolloSource.IGNORED)
        == []
    )


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_a_synthetic_enrichment_needs_no_key_and_sends_none() -> None:
    transport = Scripted(lambda _: matched())
    source = ApolloSource(DataMode.SYNTHETIC, transport=transport, vocabulary=ONE_TERM)
    await source.fetch_raw(enrich(lead("p1")))
    assert transport.calls[0][2] == {}


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_a_live_enrichment_without_the_key_fails_before_any_call() -> None:
    transport = Scripted(lambda _: matched())
    source = ApolloSource(DataMode.LIVE, transport=transport, environ={})
    with pytest.raises(MissingCredentialError):
        await source.fetch_raw(enrich(lead("p1")))
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#12.3
def test_normalizing_a_payload_of_neither_shape_is_a_normalization_error() -> None:
    source = enrichment_source(Scripted(lambda _: matched()))
    with pytest.raises(NormalizationError):
        source.normalize(RawBatch(source_name="apollo", payload={"other": []}))


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_search_and_match_each_take_capacity_from_their_own_bucket() -> None:
    throttle = SourceThrottle("apollo", ApolloSource.rate_limit)
    pacing = SourcePacing(throttle=throttle, retry=RetryPolicy())
    seen: list[tuple[float, float]] = []

    def respond(params: Mapping[str, object]) -> TransportResponse:
        seen.append(
            (
                throttle.bucket("search").available()[0],
                throttle.bucket("match").available()[0],
            )
        )
        return page(0, 0) if "page" in params else matched()

    source = ApolloSource(
        DataMode.LIVE,
        transport=Scripted(respond),
        vocabulary=ONE_TERM,
        environ=ENV,
        pacing=pacing,
    )
    await source.fetch_raw(REQUEST)
    await source.fetch_raw(enrich(lead("p1")))
    # Each call had already taken its token, from its own bucket only (free plan:
    # 50 per minute each), when the transport saw it.
    assert seen[0] == pytest.approx((49, 50), abs=0.1)
    assert seen[1] == pytest.approx((49, 49), abs=0.1)


# Verifies: specs/lead-source-adapters/requirements.md#7.5
async def test_a_synthetic_source_is_built_without_pacing_and_still_calls() -> None:
    transport = Scripted(lambda params: page(0, 0))
    source = ApolloSource(DataMode.SYNTHETIC, transport=transport, vocabulary=ONE_TERM)
    await source.fetch_raw(REQUEST)
    assert len(transport.calls) == 1


# Verifies: specs/lead-source-adapters/requirements.md#12.8
async def test_a_blank_apollo_id_is_never_sent_to_match() -> None:
    transport = Scripted(lambda _: matched())
    await enrichment_source(transport).fetch_raw(enrich(lead(""), lead("  ")))
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_a_retried_enrichment_does_not_pay_again_for_an_id_already_matched() -> (
    None
):
    failing = {"p2": 1}

    def respond(params: Mapping[str, object]) -> TransportResponse:
        who = str(params["id"])
        if failing.get(who):
            failing[who] -= 1
            return TransportResponse(status=503, headers={}, body=None)
        return matched(person_id=who)

    transport = Scripted(respond)
    source = enrichment_source(transport)
    work = enrich(lead("p1"), lead("p2"))
    with pytest.raises(SourceError):
        await source.fetch_raw(work)
    batch = await source.fetch_raw(work)  # the orchestrator's retry of the fetch
    assert [c[1]["id"] for c in transport.calls] == ["p1", "p2", "p2"]
    assert [m["lookup"] for m in batch.payload["matches"]] == ["p1", "p2"]


# --- error classification and per-window allowances (12.3) -------------------------

RATE_CODE = "USAGE.RATE_LIMIT.API_RATE_LIMIT_EXCEEDED"


def failure(
    status: int,
    *,
    code: str | None = None,
    headers: Mapping[str, str] | None = None,
    body: object | None = None,
) -> TransportResponse:
    if body is None and code is not None:
        body = {
            "error_details": {"code": code, "message": "words that must not matter"}
        }
    return TransportResponse(status=status, headers=headers or {}, body=body)


async def fail_with(response: TransportResponse) -> SourceError:
    source = live(Scripted(lambda _: response), vocabulary=ONE_TERM)
    with pytest.raises(SourceError) as caught:
        await source.fetch_raw(REQUEST)
    return caught.value


# Verifies: specs/lead-source-adapters/requirements.md#12.4
# Verifies: specs/lead-source-adapters/requirements.md#12.11
async def test_the_documented_rate_limit_code_is_rate_limited_with_retry_after() -> (
    None
):
    err = await fail_with(failure(429, code=RATE_CODE, headers={"retry-after": "42"}))
    assert isinstance(err, SourceRateLimited)
    assert err.retry_after_s == 42.0
    assert RATE_CODE in err.cause


# Verifies: specs/lead-source-adapters/requirements.md#12.4
@pytest.mark.parametrize("value", ["", "soon", "-3", "0", "nan", "inf", "1e999"])
async def test_a_bad_retry_after_header_is_ignored_never_invented(value: str) -> None:
    err = await fail_with(failure(429, code=RATE_CODE, headers={"retry-after": value}))
    assert isinstance(err, SourceRateLimited)
    assert err.retry_after_s is None


# Verifies: specs/lead-source-adapters/requirements.md#12.4
async def test_a_429_without_a_retry_after_header_has_no_interval() -> None:
    err = await fail_with(failure(429, code=RATE_CODE))
    assert isinstance(err, SourceRateLimited)
    assert err.retry_after_s is None


# Verifies: specs/lead-source-adapters/requirements.md#12.4
async def test_a_429_with_an_unrecognized_or_absent_code_is_still_rate_limited() -> (
    None
):
    for response in (
        failure(429, code="USAGE.SOMETHING.NEW", headers={"retry-after": "7"}),
        failure(429, body=["not", "an", "object"]),
        failure(429, body={"error": "top-level only", "error_details": "nope"}),
    ):
        err = await fail_with(response)
        assert isinstance(err, SourceRateLimited)


# Verifies: specs/lead-source-adapters/requirements.md#12.7
# Verifies: specs/lead-source-adapters/requirements.md#12.11
async def test_the_code_decides_not_the_message_text_or_a_top_level_field() -> None:
    # Rate-limit words in the message and a top-level field, but a different code.
    misleading = {
        "error": "rate limit exceeded",
        "message": "rate limit exceeded, too many requests",
        "error_details": {"code": "INPUT.INVALID", "message": "rate limit exceeded"},
    }
    err = await fail_with(failure(422, body=misleading))
    assert not isinstance(err, SourceRateLimited)
    # The code alone decides, whatever the message says.
    coded = {"error_details": {"code": RATE_CODE, "message": "everything is fine"}}
    assert isinstance(await fail_with(failure(429, body=coded)), SourceRateLimited)
    # A top-level code field is never read.
    top = {"code": RATE_CODE, "error_code": RATE_CODE}
    err = await fail_with(failure(422, body=top))
    assert not isinstance(err, SourceRateLimited)


# Verifies: specs/lead-source-adapters/requirements.md#12.6
async def test_a_scope_403_is_unauthorized_naming_endpoint_and_scope_cause() -> None:
    err = await fail_with(failure(403, code="AUTH.SCOPE.ENDPOINT_NOT_ALLOWED"))
    assert isinstance(err, SourceUnauthorized)
    assert err.endpoint == SEARCH_PATH
    assert err.scope_cause == "AUTH.SCOPE.ENDPOINT_NOT_ALLOWED"
    assert SEARCH_PATH in str(err)
    assert "words that must not matter" not in str(err)


# Verifies: specs/lead-source-adapters/requirements.md#12.6
async def test_a_403_on_the_match_endpoint_names_the_match_path() -> None:
    transport = Scripted(lambda _: failure(403, code="AUTH.SCOPE.X"))
    with pytest.raises(SourceUnauthorized) as caught:
        await enrichment_source(transport).fetch_raw(enrich(lead("p1")))
    assert caught.value.endpoint == MATCH_PATH


# Verifies: specs/lead-source-adapters/requirements.md#12.6
@pytest.mark.parametrize(
    "body",
    [None, ["x"], {"error_details": {}}, {"error_details": {"code": "has space!"}}],
)
async def test_a_403_without_a_usable_code_still_names_the_endpoint_and_a_cause(
    body: object,
) -> None:
    err = await fail_with(failure(403, body=body))
    assert isinstance(err, SourceUnauthorized)
    assert err.endpoint == SEARCH_PATH
    assert err.scope_cause == "no_error_code"


# Verifies: specs/lead-source-adapters/requirements.md#12.6
async def test_a_401_is_unauthorized_naming_the_endpoint() -> None:
    err = await fail_with(failure(401))
    assert isinstance(err, SourceUnauthorized)
    assert err.endpoint == SEARCH_PATH


# Verifies: specs/lead-source-adapters/requirements.md#12.11
async def test_other_failures_are_transient_or_permanent_by_type() -> None:
    for status in (500, 503, 408):
        err = await fail_with(failure(status))
        assert isinstance(err, SourceTransient)
        assert err.status == status
    for status in (400, 402, 404, 422):
        err = await fail_with(failure(status, code="INPUT.INVALID"))
        assert type(err) is SourceError
        assert str(status) in str(err)


# Verifies: specs/lead-source-adapters/requirements.md#12.6
async def test_error_text_carries_no_key_no_body_and_no_person_data() -> None:
    body = {
        "error_details": {"code": "AUTH.SCOPE.X", "message": "Jane Doe jane@x.test"},
        "person": {"name": "Jane Doe", "email": "jane@x.test"},
    }
    for status in (401, 403, 429, 422, 503):
        err = await fail_with(failure(status, body=body, headers={"x-api-key": KEY}))
        text = str(err) + repr(err) + repr(err.args)
        assert KEY not in text
        assert "Jane" not in text
        assert "jane@x.test" not in text


# Verifies: specs/lead-source-adapters/requirements.md#12.5
async def test_the_per_window_allowances_are_recorded_from_the_headers() -> None:
    headers = {
        "x-minute-requests-left": "58",
        "x-hourly-requests-left": "590",
        "x-24-hour-requests-left": "9000",
    }
    transport = Scripted(
        lambda _: TransportResponse(
            status=200, headers=headers, body={"total_entries": 0, "people": []}
        )
    )
    source = live(transport, vocabulary=ONE_TERM)
    assert source.allowances == {}
    await source.fetch_raw(REQUEST)
    assert source.allowances == {"minute": 58, "hour": 590, "day": 9000}


# Verifies: specs/lead-source-adapters/requirements.md#12.5
async def test_absent_allowance_headers_leave_no_entry_and_do_not_crash() -> None:
    source = live(Scripted(lambda _: page(0, 0)), vocabulary=ONE_TERM)
    await source.fetch_raw(REQUEST)
    assert source.allowances == {}


# Verifies: specs/lead-source-adapters/requirements.md#12.5
@pytest.mark.parametrize(
    "value",
    ["", "many", "-1", "1.5", "nan", "inf", " ", "9" * 5000, "5, 7", "\u0663", "1_0"],
    ids=lambda value: value[:12] or "empty",
)
async def test_a_malformed_allowance_header_is_dropped_never_invented(
    value: str,
) -> None:
    headers = {"x-minute-requests-left": value, "x-hourly-requests-left": "12"}
    transport = Scripted(
        lambda _: TransportResponse(
            status=200, headers=headers, body={"total_entries": 0, "people": []}
        )
    )
    source = live(transport, vocabulary=ONE_TERM)
    await source.fetch_raw(REQUEST)
    assert source.allowances == {"hour": 12}


# Verifies: specs/lead-source-adapters/requirements.md#12.5
async def test_allowances_are_recorded_from_an_error_response_too() -> None:
    headers = {"x-minute-requests-left": "0", "retry-after": "5"}
    source = live(
        Scripted(lambda _: failure(429, code=RATE_CODE, headers=headers)),
        vocabulary=ONE_TERM,
    )
    with pytest.raises(SourceRateLimited):
        await source.fetch_raw(REQUEST)
    assert source.allowances == {"minute": 0}


# Verifies: specs/lead-source-adapters/requirements.md#12.5
async def test_a_later_response_without_a_header_drops_the_stale_value() -> None:
    answers = iter(
        [
            TransportResponse(
                status=200,
                headers={"x-minute-requests-left": "9"},
                body={"total_entries": 0, "people": []},
            ),
            page(0, 0),
        ]
    )
    transport = Scripted(lambda _: next(answers))
    source = live(transport, vocabulary={"t": ["datastax", "couchbase"]})
    await source.fetch_raw(REQUEST)
    assert source.allowances == {}


# Verifies: specs/lead-source-adapters/requirements.md#12.4
async def test_the_retry_policy_backs_off_by_the_provider_interval() -> None:
    answers = iter(
        [failure(429, code=RATE_CODE, headers={"retry-after": "3"}), page(0, 0)]
    )
    source = live(Scripted(lambda _: next(answers)), vocabulary=ONE_TERM)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    batch = await RetryPolicy(max_attempts=2).run(
        lambda: source.fetch_raw(REQUEST), sleep=sleep
    )
    assert batch.payload == {"people": []}
    assert slept == [3.0]


# Verifies: specs/lead-source-adapters/requirements.md#12.11
# Verifies: specs/lead-source-adapters/requirements.md#6.2
# Verifies: specs/lead-source-adapters/requirements.md#7.4
@pytest.mark.parametrize(
    ("response", "attempts", "status"),
    [
        (failure(401), 1, SourceStatus.UNAUTHORIZED),
        (failure(403, code="AUTH.SCOPE.X"), 1, SourceStatus.UNAUTHORIZED),
        (failure(422, code="INPUT.INVALID"), 1, SourceStatus.FAILED),
        (failure(404), 1, SourceStatus.FAILED),
        (failure(503), 3, SourceStatus.TRANSIENT),
        (failure(429, code=RATE_CODE), 3, SourceStatus.RATE_LIMITED),
    ],
)
async def test_the_error_type_alone_drives_attempts_and_the_run_status(
    response: TransportResponse, attempts: int, status: SourceStatus
) -> None:
    transport = Scripted(lambda _: response)
    source = live(transport, vocabulary=ONE_TERM)
    policy = RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.001)
    ledger = SourceCallLedger("apollo", retry=policy)
    attempt = await ledger.call(partial(source.fetch_raw, REQUEST))
    assert not attempt.ok
    assert len(transport.calls) == attempts
    outcome = ledger.outcome()
    assert outcome.status is status
    assert outcome.attempted == attempts
    for leaked in ("words that must not matter", KEY):
        assert leaked not in (outcome.error or "")


# Verifies: specs/lead-source-adapters/requirements.md#12.5
async def test_allowances_survive_concurrent_calls_on_one_instance() -> None:
    def respond(params: Mapping[str, object]) -> TransportResponse:
        left = str(params["page"])
        return TransportResponse(
            status=200,
            headers={"x-minute-requests-left": left},
            body={"total_entries": 0, "people": []},
        )

    source = live(Scripted(respond), vocabulary=ONE_TERM)
    await asyncio.gather(*(source.fetch_raw(REQUEST) for _ in range(5)))
    assert set(source.allowances) <= {"minute"}
    assert source.allowances.get("minute", 1) >= 0
