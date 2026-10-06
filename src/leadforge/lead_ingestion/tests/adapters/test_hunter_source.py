"""Hunter Source adapter: email discovery batched by domain (15.1), polling (15.2)."""

import asyncio
import json
import socket
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from structlog.testing import capture_logs

from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.adapters.hunter import (
    CONFIDENCE_SCALE,
    SANDBOX_KEY,
    HunterSource,
    credits_in,
)
from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    EnrichmentRequest,
    LeadContribution,
    RawBatch,
    SourceRequest,
    enrichment_sort_key,
)
from leadforge.lead_ingestion.clustering import cluster_contributions
from leadforge.lead_ingestion.errors import (
    MissingCredentialError,
    NormalizationError,
    SourceComplianceRestricted,
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
    SourceTransient,
    SourceUnauthorized,
    UndeclaredEndpointError,
)
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
    UntrustedText,
)
from leadforge.lead_ingestion.normalizer import unmapped_raw_paths
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    SourceOutcome,
    SourceResult,
    SourceStatus,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.projection import project_lead
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.throttle import SourceThrottle
from leadforge.lead_ingestion.transport import RestTransport, TransportResponse

SEARCH_PATH = "/v2/domain-search"
FINDER_PATH = "/v2/email-finder"
VERIFIER_PATH = "/v2/email-verifier"
FIXTURE = Path(__file__).parents[2] / "fixtures" / "hunter" / "domain_search.json"
KEY = "hunter-key-not-real"
ENV = {"HUNTER_API_KEY": KEY}


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here may reach the network; a scripted or fixture transport is used."""

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a Hunter test opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket.socket, "sendto", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


Responder = Callable[[Mapping[str, object]], TransportResponse]


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


def email(value: str = "a@example.com", **fields: object) -> dict[str, object]:
    return {"value": value, **fields}


def found(
    domain: str = "example.com",
    emails: list[dict[str, object]] | None = None,
    *,
    total: int | None = None,
) -> TransportResponse:
    listed = [email()] if emails is None else emails
    body = {
        "data": {"domain": domain, "organization": "Org", "emails": listed},
        "meta": {"results": len(listed) if total is None else total},
    }
    return TransportResponse(status=200, headers={}, body=body)


def lead(domain: object, *, source: str = "apollo") -> LeadContribution:
    if domain is None:
        return LeadContribution(source_name=source)
    return LeadContribution(
        source_name=source,
        values={"company.domain": domain},
        provenance=(
            FieldProvenance(
                canonical_path="company.domain",
                source_name=source,
                data_mode=DataMode.LIVE,
                fetched_at=datetime.now(UTC),
                raw_field_path="organization.primary_domain",
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            ),
        ),
    )


def enrich(*leads: LeadContribution) -> EnrichmentRequest:
    return EnrichmentRequest(kind="enrich", work_list=leads)


def live(transport: Scripted, *, environ: Mapping[str, str] = ENV) -> HunterSource:
    return HunterSource(DataMode.LIVE, transport=transport, environ=environ)


async def fixture_batch() -> tuple[HunterSource, RawBatch]:
    transport = HunterSource.build_transport(DataMode.SYNTHETIC)
    source = HunterSource(DataMode.SYNTHETIC, transport=transport, environ={})
    return source, await source.fetch_raw(enrich(lead("example.com")))


def only_batch(*emails: dict[str, object]) -> RawBatch:
    return RawBatch(
        source_name="hunter",
        payload={
            "searches": [
                {"domain": "example.com", "response": found(emails=list(emails)).body}
            ],
            "credits_billable": True,
        },
    )


def normalize_one(**fields: object) -> LeadContribution:
    source = HunterSource(DataMode.LIVE, transport=Scripted(lambda _: found()))
    [contribution] = source.normalize_checked(
        only_batch({"value": "a@example.com", **fields})
    )
    return contribution


# --- declarations (16.1, 16.5) -----------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#16.1
def test_declares_one_read_only_domain_search_on_the_v2_path() -> None:
    assert HunterSource.name == "hunter"
    assert HunterSource.required_env == ("HUNTER_API_KEY",)
    assert HunterSource.base_url == "https://api.hunter.io"
    assert {(e.method, e.path) for e in HunterSource.endpoints.values()} == {
        ("GET", SEARCH_PATH),
        ("GET", FINDER_PATH),
        ("GET", VERIFIER_PATH),
    }
    assert all(e.read_only is True for e in HunterSource.endpoints.values())
    assert HunterSource.capabilities == frozenset({Capability.ENRICH})


# Verifies: specs/lead-source-adapters/requirements.md#16.1
async def test_the_live_transport_is_https_and_undeclared_paths_are_refused() -> None:
    transport = HunterSource.build_transport(DataMode.LIVE)
    assert isinstance(transport, RestTransport)
    await transport.aclose()
    synthetic = HunterSource.build_transport(DataMode.SYNTHETIC)
    other = Endpoint(method="GET", path="/v2/leads", bucket="finder")
    with pytest.raises(UndeclaredEndpointError):
        await synthetic.send(other, params=None, json_body=None, headers={})


# Verifies: specs/lead-source-adapters/requirements.md#16.1
async def test_the_key_goes_in_x_api_key_and_never_in_the_query_or_a_bearer() -> None:
    transport = Scripted(lambda _: found())
    await live(transport).fetch_raw(enrich(lead("example.com")))
    _, params, headers = transport.calls[0]
    assert headers["X-API-KEY"] == KEY
    assert not any(k.lower() == "authorization" for k in headers)
    assert KEY not in json.dumps(params)
    assert "api_key" not in params


# Verifies: specs/lead-source-adapters/requirements.md#16.1
async def test_a_live_run_without_the_key_fails_naming_only_the_variable() -> None:
    transport = Scripted(lambda _: found())
    with pytest.raises(MissingCredentialError) as caught:
        await live(transport, environ={}).fetch_raw(enrich(lead("example.com")))
    assert caught.value.missing == ("HUNTER_API_KEY",)
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#16.1
async def test_a_synthetic_run_needs_no_key_and_sends_none() -> None:
    transport = Scripted(lambda _: found())
    source = HunterSource(DataMode.SYNTHETIC, transport=transport, environ={})
    await source.fetch_raw(enrich(lead("example.com")))
    assert transport.calls[0][2] == {}


# Verifies: specs/lead-source-adapters/requirements.md#16.5
def test_declares_the_finder_and_verifier_buckets_with_both_windows_anded() -> None:
    finder = HunterSource.rate_limit["finder"]
    verifier = HunterSource.rate_limit["verifier"]
    assert {(w.requests, w.per_seconds) for w in finder.windows} == {
        (15, 1.0),
        (500, 60.0),
    }
    assert {(w.requests, w.per_seconds) for w in verifier.windows} == {
        (10, 1.0),
        (300, 60.0),
    }
    assert finder.documented
    assert verifier.documented
    assert HunterSource.endpoints["domain_search"].bucket == "finder"
    assert HunterSource.endpoints["email_finder"].bucket == "finder"
    assert HunterSource.endpoints["email_verifier"].bucket == "verifier"


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_a_domain_search_takes_capacity_from_the_finder_bucket_only() -> None:
    throttle = SourceThrottle("hunter", HunterSource.rate_limit)
    pacing = SourcePacing(throttle=throttle, retry=RetryPolicy())
    seen: list[tuple[float, float]] = []

    def respond(_: Mapping[str, object]) -> TransportResponse:
        seen.append(
            (
                throttle.bucket("finder").available()[0],
                throttle.bucket("verifier").available()[0],
            )
        )
        return found()

    source = HunterSource(
        DataMode.LIVE, transport=Scripted(respond), environ=ENV, pacing=pacing
    )
    await source.fetch_raw(enrich(lead("example.com")))
    finder_left, verifier_left = seen[0]
    assert finder_left == pytest.approx(14, abs=0.1)
    assert verifier_left == pytest.approx(10, abs=0.1)


# Verifies: specs/lead-source-adapters/requirements.md#16.5
def test_declares_paid_per_lead_enrichment_that_yields_suppression() -> None:
    # Leads are per person (user decision, 2026-10-06): the finder and verifier are
    # per-person calls, so the orchestrator must hand Hunter every lead, not one per
    # company. Domain search stays once per company through the per-run domain cache.
    # A 451 flags the person suppressed (16.6), so Hunter yields Suppression (user
    # decision, 2026-10-06): that puts it in a paid tier ahead of Apollo's.
    assert HunterSource.cost_class is CostClass.PAID
    assert HunterSource.charge_unit is ChargeUnit.PER_LEAD
    assert HunterSource.yields_suppression is True
    assert enrichment_sort_key(HunterSource) == (True, False, False, 2, "hunter")


# --- batching by domain (16.5 via ADR-0002, 6.11) ----------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_one_domain_search_per_distinct_domain_however_many_leads() -> None:
    transport = Scripted(lambda p: found(str(p["domain"])))
    leads = (
        lead("Example.com"),
        lead(" example.com "),
        lead(("other.org", "example.com")),
        lead(["third.io"]),
        lead(None),
    )
    batch = await live(transport).fetch_raw(enrich(*leads))
    assert [c[1]["domain"] for c in transport.calls] == [
        "example.com",
        "other.org",
        "third.io",
    ]
    assert [s["domain"] for s in batch.payload["searches"]] == [
        "example.com",
        "other.org",
        "third.io",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_the_whole_work_list_still_makes_one_search_per_company() -> None:
    transport = Scripted(lambda _: found())
    work = tuple(lead("example.com") for _ in range(5))
    await live(transport).fetch_raw(enrich(*work))
    assert len(transport.calls) == 1


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_the_search_is_one_capped_page_per_domain() -> None:
    transport = Scripted(lambda _: found())
    await live(transport).fetch_raw(enrich(lead("example.com")))
    assert transport.calls[0][1] == {"domain": "example.com", "limit": 100}


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_no_call_for_an_empty_work_list_or_a_lead_with_no_domain() -> None:
    transport = Scripted(lambda _: found())
    source = live(transport)
    await source.fetch_raw(enrich())
    await source.fetch_raw(enrich(lead(None), lead(""), lead("   ")))
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#16.5
@pytest.mark.parametrize(
    "bad", ["not a domain", "a/b.com", "http://x.com", "-x.com", "x" * 300 + ".com"]
)
async def test_a_domain_that_is_not_hostname_shaped_is_never_sent(bad: str) -> None:
    transport = Scripted(lambda _: found())
    with capture_logs() as logs:
        await live(transport).fetch_raw(enrich(lead(bad)))
    assert transport.calls == []
    skipped = [e for e in logs if e["event"] == "hunter_domain_skipped"]
    assert len(skipped) == 1
    assert bad not in json.dumps(skipped, default=str)


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_a_non_text_company_domain_is_a_normalization_error() -> None:
    source = live(Scripted(lambda _: found()))
    with pytest.raises(NormalizationError) as caught:
        await source.fetch_raw(enrich(lead(42)))
    assert caught.value.canonical_path == "company.domain"


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_a_discovery_request_is_refused_because_hunter_only_enriches() -> None:
    transport = Scripted(lambda _: found())
    with pytest.raises(SourceError):
        await live(transport).fetch_raw(SourceRequest(kind="discovery"))
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_a_retried_fetch_does_not_pay_again_for_a_domain_already_searched() -> (
    None
):
    failing = {"two.org": 1}

    def respond(params: Mapping[str, object]) -> TransportResponse:
        who = str(params["domain"])
        if failing.get(who):
            failing[who] -= 1
            return TransportResponse(status=503, headers={}, body=None)
        return found(who)

    transport = Scripted(respond)
    source = live(transport)
    work = enrich(lead("one.org"), lead("two.org"))
    with pytest.raises(SourceTransient):
        await source.fetch_raw(work)
    batch = await source.fetch_raw(work)  # the orchestrator's retry of the fetch
    assert [c[1]["domain"] for c in transport.calls] == [
        "one.org",
        "two.org",
        "two.org",
    ]
    assert [s["domain"] for s in batch.payload["searches"]] == ["one.org", "two.org"]


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_a_truncated_result_set_warns_with_counts_and_no_domain() -> None:
    transport = Scripted(lambda _: found(emails=[email()], total=250))
    with capture_logs() as logs:
        await live(transport).fetch_raw(enrich(lead("example.com")))
    [warning] = [e for e in logs if e["event"] == "hunter_domain_search_truncated"]
    assert warning["returned"] == 1
    assert warning["total"] == 250
    assert "example.com" not in json.dumps(logs, default=str)


# --- contributions (16.2) ----------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_fixture_contributes_email_verdict_confidence_and_sources() -> None:
    source, batch = await fixture_batch()
    first, second = source.normalize_checked(batch)
    plain = {
        path: value.value if isinstance(value, UntrustedText) else value
        for path, value in first.values.items()
    }
    assert plain == {
        "company.domain": "example.com",
        "company.name": "Example Data Corp",
        "person.email": "ada.lovelace@example.com",
        "person.email_status": EmailStatus.VERIFIED,
        "person.first_name": "Ada",
        "person.last_name": "Lovelace",
        "person.title": "VP Engineering",
        "person.email_sources": ("https://example.com/team",),
    }
    assert {p.canonical_path for p in first.provenance} == set(first.values)
    assert all(p.data_mode is DataMode.SYNTHETIC for p in first.provenance)
    assert first.absences == ()
    # The second address has no verdict and no sources: nothing is invented for it.
    assert "person.email_status" not in second.values
    assert "person.email_sources" not in second.values
    assert "person.title" not in second.values


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_free_text_is_untrusted_and_identifiers_are_not() -> None:
    source, batch = await fixture_batch()
    [first, _] = source.normalize_checked(batch)
    untrusted = {p.canonical_path for p in first.provenance if p.untrusted}
    assert untrusted == {
        "company.name",
        "person.first_name",
        "person.last_name",
        "person.title",
    }
    for path in untrusted:
        assert isinstance(first.values[path], UntrustedText)
    assert not isinstance(first.values["person.email"], UntrustedText)


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_every_fixture_field_is_mapped_or_intentionally_ignored() -> None:
    _, batch = await fixture_batch()
    data = batch.payload["searches"][0]["response"]["data"]
    for item in data["emails"]:
        wrapper = {**{k: v for k, v in data.items() if k != "emails"}, "email": item}
        assert (
            unmapped_raw_paths(wrapper, HunterSource.RULES, HunterSource.IGNORED) == []
        )


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_a_search_asks_no_field_question_so_absence_is_never_recorded() -> None:
    source = live(Scripted(lambda _: found(emails=[email()])))
    batch = await source.fetch_raw(enrich(lead("example.com")))
    [contribution] = source.normalize_checked(batch)
    assert contribution.absences == ()


# Verifies: specs/lead-source-adapters/requirements.md#16.2
def test_a_domain_with_no_emails_contributes_nothing() -> None:
    source = live(Scripted(lambda _: found()))
    assert source.normalize_checked(only_batch()) == []


# Verifies: specs/lead-source-adapters/requirements.md#16.2
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("valid", EmailStatus.VERIFIED),
        ("accept_all", EmailStatus.ACCEPT_ALL),
        ("invalid", EmailStatus.INVALID),
        ("unknown", EmailStatus.UNKNOWN),
        ("webmail", EmailStatus.UNKNOWN),
        ("disposable", EmailStatus.UNKNOWN),
    ],
)
def test_the_verdict_maps_onto_email_status_without_overclaiming(
    status: str, expected: EmailStatus
) -> None:
    contribution = normalize_one(verification={"date": None, "status": status})
    assert contribution.values["person.email_status"] is expected
    provenance = {p.canonical_path: p for p in contribution.provenance}
    assert (
        provenance["person.email_status"].raw_field_path == "email.verification.status"
    )


# Verifies: specs/lead-source-adapters/requirements.md#16.2
@pytest.mark.parametrize(
    "verification", [None, {"date": None, "status": None}, {"date": "2026-01-01"}]
)
def test_an_address_with_no_verdict_is_never_marked_verified(
    verification: object,
) -> None:
    fields: dict[str, object] = {"confidence": 99}
    if verification is not None:
        fields["verification"] = verification
    contribution = normalize_one(**fields)
    assert "person.email_status" not in contribution.values


# Verifies: specs/lead-source-adapters/requirements.md#16.2
def test_an_unrecognized_verdict_is_refused_not_guessed() -> None:
    with pytest.raises(NormalizationError) as caught:
        normalize_one(verification={"status": "probably"})
    assert caught.value.canonical_path == "person.email_status"
    assert "probably" not in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#16.2
def test_hunters_confidence_is_provider_stated_with_its_verbatim_value_and_scale() -> (
    None
):
    contribution = normalize_one(confidence=94)
    provenance = {p.canonical_path: p for p in contribution.provenance}
    stated = provenance["person.email"]
    assert stated.confidence_origin is ConfidenceOrigin.PROVIDER_STATED
    assert stated.confidence == pytest.approx(0.94)
    assert stated.confidence_raw == "94"
    assert stated.confidence_scale == CONFIDENCE_SCALE
    # Confidence is about the address; no other field claims one.
    assert all(
        p.confidence_origin is ConfidenceOrigin.NONE
        for path, p in provenance.items()
        if path != "person.email"
    )


# Verifies: specs/lead-source-adapters/requirements.md#16.2
def test_a_zero_confidence_is_stated_not_treated_as_absent() -> None:
    stated = {p.canonical_path: p for p in normalize_one(confidence=0).provenance}[
        "person.email"
    ]
    assert stated.confidence_origin is ConfidenceOrigin.PROVIDER_STATED
    assert stated.confidence == 0.0
    assert stated.confidence_raw == "0"


# Verifies: specs/lead-source-adapters/requirements.md#16.2
def test_no_stated_confidence_means_origin_none_never_a_default_number() -> None:
    stated = {p.canonical_path: p for p in normalize_one().provenance}["person.email"]
    assert stated.confidence_origin is ConfidenceOrigin.NONE
    assert stated.confidence is None
    assert stated.confidence_raw is None


# Verifies: specs/lead-source-adapters/requirements.md#16.2
@pytest.mark.parametrize("bad", [101, -1, 50.5, "94", True])
def test_a_confidence_outside_the_documented_scale_is_refused(bad: object) -> None:
    with pytest.raises(NormalizationError) as caught:
        normalize_one(confidence=bad)
    assert caught.value.raw_field_path.endswith("confidence")


# Verifies: specs/lead-source-adapters/requirements.md#16.2
@pytest.mark.parametrize(
    "payload",
    [None, [], {"searches": "x"}, {"searches": [{"domain": "d"}]}],
)
def test_a_batch_of_the_wrong_shape_is_a_normalization_error(payload: object) -> None:
    source = live(Scripted(lambda _: found()))
    with pytest.raises(NormalizationError):
        source.normalize(RawBatch(source_name="hunter", payload=payload))


# Verifies: specs/lead-source-adapters/requirements.md#16.2
def test_an_email_record_of_the_wrong_shape_names_the_path() -> None:
    source = live(Scripted(lambda _: found()))
    with pytest.raises(NormalizationError) as caught:
        source.normalize(only_batch({"type": "personal"}))
    assert "value" in caught.value.raw_field_path


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_a_body_that_is_not_a_search_result_is_a_normalization_error() -> None:
    transport = Scripted(lambda _: TransportResponse(200, {}, ["not", "an", "object"]))
    with pytest.raises(NormalizationError):
        await live(transport).fetch_raw(enrich(lead("example.com")))


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_a_non_success_status_is_not_swallowed() -> None:
    transport = Scripted(lambda _: TransportResponse(500, {}, None))
    with pytest.raises(SourceTransient):
        await live(transport).fetch_raw(enrich(lead("example.com")))


# --- sandbox (16.8) ----------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#16.8
async def test_the_sandbox_key_runs_live_mode_and_counts_zero_credits() -> None:
    transport = Scripted(lambda _: found())
    source = live(transport, environ={"HUNTER_API_KEY": SANDBOX_KEY})
    batch = await source.fetch_raw(enrich(lead("example.com")))
    assert SANDBOX_KEY == "test-api-key"
    assert source.data_mode is DataMode.LIVE
    assert transport.calls[0][2]["X-API-KEY"] == SANDBOX_KEY
    assert credits_in(batch) == 0


# Verifies: specs/lead-source-adapters/requirements.md#16.8
async def test_a_real_key_counts_one_credit_per_domain_search() -> None:
    transport = Scripted(lambda p: found(str(p["domain"])))
    batch = await live(transport).fetch_raw(enrich(lead("a.org"), lead("b.org")))
    assert credits_in(batch) == 2


# Verifies: specs/lead-source-adapters/requirements.md#16.8
async def test_a_synthetic_run_spends_no_credits() -> None:
    _, batch = await fixture_batch()
    assert credits_in(batch) == 0


# --- secrets and errors ------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#16.1
async def test_the_key_never_reaches_a_log_a_repr_or_an_error_message() -> None:
    transport = Scripted(lambda _: TransportResponse(500, {}, {"echo": KEY}))
    source = live(transport)
    with capture_logs() as logs, pytest.raises(SourceError) as caught:
        await source.fetch_raw(enrich(lead("example.com")))
    for text in (repr(source), str(caught.value), repr(caught.value), str(logs)):
        assert KEY not in text


# Verifies: specs/lead-source-adapters/requirements.md#16.2
def test_a_validation_error_carries_no_provider_text() -> None:
    source = live(Scripted(lambda _: found()))
    secret = "ada@leak.example"
    with pytest.raises(NormalizationError) as caught:
        source.normalize(only_batch(email(secret, first_name=7)))
    assert secret not in str(caught.value)


# --- routing: verifier, finder, domain search (16.3) -------------------------------


def person(
    *,
    email: object = None,
    first: object = None,
    last: object = None,
    domain: str | None = "example.com",
) -> LeadContribution:
    values: dict[str, object] = {}
    for path, value in (
        ("person.email", email),
        ("person.first_name", first),
        ("person.last_name", last),
        ("company.domain", domain),
    ):
        if value is not None:
            values[path] = value
    return LeadContribution(
        source_name="apollo",
        values=values,
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name="apollo",
                data_mode=DataMode.LIVE,
                fetched_at=datetime.now(UTC),
                raw_field_path=path,
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=isinstance(value, UntrustedText),
            )
            for path, value in values.items()
        ),
    )


def verdict(
    status: object = "valid", address: str = "a@example.com", **more: object
) -> TransportResponse:
    body = {"data": {"status": status, "email": address, **more}, "meta": {}}
    return TransportResponse(status=200, headers={}, body=body)


def found_one(
    address: str | None = "ada@example.com", **more: object
) -> TransportResponse:
    data = {"email": address, "first_name": "Ada", "last_name": "Lovelace", **more}
    return TransportResponse(status=200, headers={}, body={"data": data, "meta": {}})


class Routed(Scripted):
    """Answers each endpoint from its own response factory."""

    def __init__(self, **answers: Responder) -> None:
        super().__init__(lambda _: TransportResponse(404, {}, None))
        self.answers: dict[str, Responder] = {
            SEARCH_PATH: answers.get("search", lambda p: found(str(p["domain"]))),
            FINDER_PATH: answers.get("finder", lambda _: found_one()),
            VERIFIER_PATH: answers.get("verifier", lambda _: verdict()),
        }

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.calls.append((endpoint, dict(params or {}), dict(headers)))
        return self.answers[endpoint.path](params or {})


def paths(transport: Scripted) -> list[str]:
    return [call[0].path for call in transport.calls]


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_a_known_address_goes_to_the_verifier_alone() -> None:
    transport = Routed()
    batch = await live(transport).fetch_raw(
        enrich(person(email="Ada@Example.com ", first="Ada", last="L"))
    )
    assert paths(transport) == [VERIFIER_PATH]
    endpoint, params, headers = transport.calls[0]
    assert endpoint.bucket == "verifier"
    assert params == {"email": "ada@example.com"}
    assert headers["X-API-KEY"] == KEY
    assert [v["email"] for v in batch.payload["verifications"]] == ["ada@example.com"]


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_a_name_and_domain_without_an_address_go_to_the_finder() -> None:
    transport = Routed()
    await live(transport).fetch_raw(
        enrich(
            person(
                first=UntrustedText(value="Ada", truncated=False, original_length=3),
                last="Lovelace",
                domain="X.io",
            )
        )
    )
    assert paths(transport) == [FINDER_PATH]
    endpoint, params, _ = transport.calls[0]
    assert endpoint.bucket == "finder"
    assert params == {"domain": "x.io", "first_name": "Ada", "last_name": "Lovelace"}


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_a_company_only_lead_still_gets_a_domain_search() -> None:
    transport = Routed()
    await live(transport).fetch_raw(enrich(person(first="Ada")))  # no last name
    assert paths(transport) == [SEARCH_PATH]


# Verifies: specs/lead-source-adapters/requirements.md#16.3
@pytest.mark.parametrize("last", ["Lo***n", "", "   ", "L" * 101, "bad\nname"])
async def test_a_name_that_cannot_be_asked_for_is_not_sent_to_the_finder(
    last: str,
) -> None:
    transport = Routed()
    await live(transport).fetch_raw(enrich(person(first="Ada", last=last)))
    assert paths(transport) == [SEARCH_PATH]  # falls back to the domain search


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_a_name_without_a_domain_cannot_be_found_and_costs_nothing() -> None:
    transport = Routed()
    await live(transport).fetch_raw(enrich(person(first="Ada", last="L", domain=None)))
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#16.3
@pytest.mark.parametrize("bad", ["no-at-sign", "a b@x.com", "a@b@c.com", "a@x.com?y=1"])
async def test_a_malformed_address_is_not_verified_and_is_not_logged(bad: str) -> None:
    transport = Routed()
    with capture_logs() as logs:
        await live(transport).fetch_raw(enrich(person(email=bad)))
    assert paths(transport) == [SEARCH_PATH]  # no usable address: company-level
    assert bad not in json.dumps(logs, default=str)


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_hostile_domains_never_alter_the_request() -> None:
    transport = Routed()
    hostile = (
        "evil.com/../x",
        "a.com?x=1",
        "a.com#f",
        "a.com\\b",
        "u@a.com",
        "a.com:80",
    )
    await live(transport).fetch_raw(
        enrich(*(person(first="A", last="B", domain=h) for h in hostile))
    )
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_each_question_is_asked_once_even_when_the_fetch_retries() -> None:
    transport = Routed()
    source = live(transport)
    work = enrich(
        person(email="a@example.com"),
        person(email="A@example.com"),
        person(first="Ada", last="Lovelace"),
        person(first="ADA", last="lovelace"),
    )
    await source.fetch_raw(work)
    await source.fetch_raw(work)  # the orchestrator's retry
    assert sorted(paths(transport)) == [FINDER_PATH, VERIFIER_PATH]


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_a_non_text_address_is_a_normalization_error() -> None:
    with pytest.raises(NormalizationError) as caught:
        await live(Routed()).fetch_raw(enrich(person(email=42)))
    assert caught.value.canonical_path == "person.email"


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_a_verifier_verdict_becomes_the_email_status_and_stated_confidence() -> (
    None
):
    transport = Routed(verifier=lambda _: verdict("valid", "a@example.com", score=88))
    source = live(transport)
    batch = await source.fetch_raw(enrich(person(email="a@example.com")))
    [contribution] = source.normalize_checked(batch)
    assert contribution.values == {
        "person.email": "a@example.com",
        "person.email_status": EmailStatus.VERIFIED,
    }
    records = {p.canonical_path: p for p in contribution.provenance}
    assert records["person.email_status"].raw_field_path == "status"
    assert records["person.email"].confidence_origin is ConfidenceOrigin.PROVIDER_STATED
    assert records["person.email"].confidence == pytest.approx(0.88)
    assert records["person.email"].confidence_raw == "88"
    assert records["person.email_status"].confidence_origin is ConfidenceOrigin.NONE


# Verifies: specs/lead-source-adapters/requirements.md#16.2
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("valid", EmailStatus.VERIFIED),
        ("accept_all", EmailStatus.ACCEPT_ALL),
        ("invalid", EmailStatus.INVALID),
        ("unknown", EmailStatus.UNKNOWN),
        ("webmail", EmailStatus.UNKNOWN),
        ("disposable", EmailStatus.UNKNOWN),
    ],
)
async def test_the_verifier_verdict_never_overclaims(
    status: str, expected: EmailStatus
) -> None:
    source = live(Routed(verifier=lambda _: verdict(status)))
    batch = await source.fetch_raw(enrich(person(email="a@example.com")))
    [contribution] = source.normalize_checked(batch)
    assert contribution.values["person.email_status"] is expected


# Verifies: specs/lead-source-adapters/requirements.md#16.2
@pytest.mark.parametrize("status", ["probably", "", None, 1, True])
async def test_an_unknown_or_missing_verifier_verdict_is_refused_not_guessed(
    status: object,
) -> None:
    source = live(Routed(verifier=lambda _: verdict(status)))
    batch = await source.fetch_raw(enrich(person(email="a@example.com")))
    with pytest.raises(NormalizationError) as caught:
        source.normalize_checked(batch)
    assert "probably" not in str(caught.value)
    assert "a@example.com" not in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_a_found_address_carries_verdict_confidence_and_sources() -> None:
    answer = found_one(
        score=91,
        position="CTO",
        company="Example Data",
        verification={"date": None, "status": "accept_all"},
        sources=[{"uri": "https://example.com/team"}],
    )
    source = live(Routed(finder=lambda _: answer))
    batch = await source.fetch_raw(enrich(person(first="Ada", last="Lovelace")))
    [contribution] = source.normalize_checked(batch)
    values = contribution.values
    assert values["person.email"] == "ada@example.com"
    assert values["person.email_status"] is EmailStatus.ACCEPT_ALL
    assert values["person.email_sources"] == ("https://example.com/team",)
    for path in (
        "person.first_name",
        "person.last_name",
        "person.title",
        "company.name",
    ):
        assert isinstance(values[path], UntrustedText)
    records = {p.canonical_path: p for p in contribution.provenance}
    assert records["person.email"].confidence_origin is ConfidenceOrigin.PROVIDER_STATED
    assert records["person.email"].confidence == pytest.approx(0.91)
    assert records["person.email"].confidence_scale == CONFIDENCE_SCALE
    assert records["person.title"].raw_field_path == "position"
    assert records["person.title"].confidence_origin is ConfidenceOrigin.NONE


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_a_finder_that_finds_nothing_contributes_nothing_and_no_absence() -> None:
    source = live(Routed(finder=lambda _: found_one(None)))
    batch = await source.fetch_raw(enrich(person(first="Ada", last="Lovelace")))
    assert source.normalize_checked(batch) == []


# Verifies: specs/lead-source-adapters/requirements.md#16.2
@pytest.mark.parametrize("bad", [101, -1, 50.5, "94", True, float("nan")])
async def test_a_finder_or_verifier_score_outside_the_scale_is_refused(
    bad: object,
) -> None:
    source = live(Routed(finder=lambda _: found_one(score=bad)))
    batch = await source.fetch_raw(enrich(person(first="Ada", last="Lovelace")))
    with pytest.raises(NormalizationError):
        source.normalize_checked(batch)
    source = live(Routed(verifier=lambda _: verdict(score=bad)))
    batch = await source.fetch_raw(enrich(person(email="a@example.com")))
    with pytest.raises(NormalizationError):
        source.normalize_checked(batch)


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_finder_and_verifier_responses_of_the_wrong_shape_are_refused() -> None:
    for transport, work in (
        (
            Routed(finder=lambda _: TransportResponse(200, {}, [])),
            person(first="A", last="B"),
        ),
        (
            Routed(verifier=lambda _: TransportResponse(200, {}, {"data": 1})),
            person(email="a@example.com"),
        ),
    ):
        with pytest.raises(NormalizationError):
            await live(transport).fetch_raw(enrich(work))


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_the_synthetic_fixtures_serve_finder_and_verifier_with_no_socket() -> (
    None
):
    transport = HunterSource.build_transport(DataMode.SYNTHETIC)
    source = HunterSource(DataMode.SYNTHETIC, transport=transport, environ={})
    batch = await source.fetch_raw(
        enrich(
            person(email="a@example.com"), person(first="Ada", last="L", domain="b.io")
        )
    )
    contributions = source.normalize_checked(batch)
    assert len(contributions) == 2
    assert all(
        p.data_mode is DataMode.SYNTHETIC for c in contributions for p in c.provenance
    )
    assert credits_in(batch) == 0
    for name, rules, ignored in (
        ("email_finder", HunterSource.FINDER_RULES, HunterSource.FINDER_IGNORED),
        ("email_verifier", HunterSource.VERIFIER_RULES, HunterSource.VERIFIER_IGNORED),
    ):
        data = json.loads((FIXTURE.parent / f"{name}.json").read_text())["data"]
        assert unmapped_raw_paths(data, rules, ignored) == []


# Verifies: specs/lead-source-adapters/requirements.md#16.8
async def test_every_live_call_counts_one_credit_and_the_sandbox_none() -> None:
    work = enrich(
        person(email="a@example.com"),
        person(first="Ada", last="L", domain="b.io"),
        person(domain="c.io"),
    )
    paid = await live(Routed()).fetch_raw(work)
    sandbox = await live(Routed(), environ={"HUNTER_API_KEY": SANDBOX_KEY}).fetch_raw(
        work
    )
    assert credits_in(paid) == 3
    assert credits_in(sandbox) == 0


# --- pacing: both windows of the finder bucket (16.5) ------------------------------


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def paced(transport: Scripted, fake: FakeTime) -> HunterSource:
    throttle = SourceThrottle(
        "hunter", HunterSource.rate_limit, clock=fake.clock, sleep=fake.sleep
    )
    return HunterSource(
        DataMode.LIVE,
        transport=transport,
        environ=ENV,
        pacing=SourcePacing(throttle=throttle, retry=RetryPolicy()),
    )


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_sixteen_finder_calls_are_spread_and_charge_both_windows() -> None:
    fake = FakeTime()
    stamps: list[float] = []

    def respond(params: Mapping[str, object]) -> TransportResponse:
        stamps.append(fake.now)
        return found(str(params["domain"]))

    source = paced(Scripted(respond), fake)
    await source.fetch_raw(enrich(*(lead(f"d{n}.org") for n in range(16))))
    assert len(stamps) == 16
    assert len(set(stamps[:15])) == 1  # the burst the per-second window allows
    assert stamps[15] > stamps[0]  # the sixteenth waited for the 15/s window
    assert fake.sleeps == [pytest.approx(1 / 15)]
    per_second, per_minute = source._pacing.throttle.bucket("finder").available()  # type: ignore[union-attr]
    assert per_second == pytest.approx(0, abs=0.01)
    assert per_minute == pytest.approx(500 - 16, abs=1)  # both windows were charged


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_eleven_verifier_calls_are_spread_and_charge_both_windows() -> None:
    fake = FakeTime()
    stamps: list[float] = []

    def respond(_: Mapping[str, object]) -> TransportResponse:
        stamps.append(fake.now)
        return verdict()

    source = paced(Routed(verifier=respond), fake)
    await source.fetch_raw(enrich(*(person(email=f"p{n}@x.org") for n in range(11))))
    assert len(set(stamps[:10])) == 1
    assert stamps[10] > stamps[0]
    assert fake.sleeps == [pytest.approx(1 / 10)]
    per_second, per_minute = source._pacing.throttle.bucket("verifier").available()  # type: ignore[union-attr]
    assert per_second == pytest.approx(0, abs=0.01)
    assert per_minute == pytest.approx(300 - 11, abs=1)


# Verifies: specs/lead-source-adapters/requirements.md#16.5
async def test_a_verification_takes_capacity_from_the_verifier_bucket_only() -> None:
    throttle = SourceThrottle("hunter", HunterSource.rate_limit)
    seen: list[tuple[float, float]] = []

    def respond(_: Mapping[str, object]) -> TransportResponse:
        seen.append(
            (
                throttle.bucket("finder").available()[0],
                throttle.bucket("verifier").available()[0],
            )
        )
        return verdict()

    source = HunterSource(
        DataMode.LIVE,
        transport=Routed(verifier=respond),
        environ=ENV,
        pacing=SourcePacing(throttle=throttle, retry=RetryPolicy()),
    )
    await source.fetch_raw(enrich(person(email="a@example.com")))
    assert seen[0][0] == pytest.approx(15, abs=0.1)
    assert seen[0][1] == pytest.approx(9, abs=0.1)


WORK_FOR_EACH_ROUTE = {
    "search": person(domain="example.com"),
    "finder": person(first="Ada", last="Lovelace"),
    "verifier": person(email="ada@example.com"),
}


# Verifies: specs/lead-source-adapters/requirements.md#16.1
@pytest.mark.parametrize("route", sorted(WORK_FOR_EACH_ROUTE))
async def test_every_route_sends_the_key_in_the_header_only(route: str) -> None:
    transport = Routed()
    await live(transport).fetch_raw(enrich(WORK_FOR_EACH_ROUTE[route]))
    [(_, params, headers)] = transport.calls
    assert headers == {"X-API-KEY": KEY}
    assert KEY not in json.dumps(params)
    assert not {"api_key", "key"} & set(params)


# Verifies: specs/lead-source-adapters/requirements.md#16.1
@pytest.mark.parametrize("route", sorted(WORK_FOR_EACH_ROUTE))
async def test_a_malformed_answer_leaks_no_key_address_domain_or_name(
    route: str,
) -> None:
    echo = {"echo": [KEY, "ada@example.com", "example.com", "Lovelace"]}
    wrong = TransportResponse(200, {}, echo)
    transport = Routed(
        search=lambda _: wrong, finder=lambda _: wrong, verifier=lambda _: wrong
    )
    source = live(transport)
    with capture_logs() as logs, pytest.raises(NormalizationError) as caught:
        await source.fetch_raw(enrich(WORK_FOR_EACH_ROUTE[route]))
    leaked = (
        str(caught.value),
        repr(caught.value),
        repr(caught.value.__cause__),
        str(logs),
    )
    for text in leaked:
        for secret in (KEY, "ada@example.com", "example.com", "Lovelace"):
            assert secret not in text


# Verifies: specs/lead-source-adapters/requirements.md#16.1
@pytest.mark.parametrize("status", [401, 403, 404, 500])
@pytest.mark.parametrize("route", sorted(WORK_FOR_EACH_ROUTE))
async def test_an_error_status_on_any_route_names_no_key_or_query(
    route: str, status: int
) -> None:
    body = {"echo": [KEY, "ada@example.com"]}
    bad = TransportResponse(status, {}, body)
    transport = Routed(
        search=lambda _: bad, finder=lambda _: bad, verifier=lambda _: bad
    )
    with capture_logs() as logs, pytest.raises(SourceError) as caught:
        await live(transport).fetch_raw(enrich(WORK_FOR_EACH_ROUTE[route]))
    for text in (str(caught.value), repr(caught.value), str(logs)):
        for secret in (KEY, "ada@example.com", "Lovelace"):
            assert secret not in text


# --- Task 15.2: bounded polling of a verifier answer that is still running (16.4) ---

ADDRESS = "a@example.com"


def pending(retry_after: str | None = None) -> TransportResponse:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    return TransportResponse(202, headers, None)


class Sequence:
    """Verifier answers in order; the last one repeats."""

    def __init__(self, *answers: TransportResponse) -> None:
        self.answers = list(answers)
        self.asked = 0

    def __call__(self, _: Mapping[str, object]) -> TransportResponse:
        answer = self.answers[min(self.asked, len(self.answers) - 1)]
        self.asked += 1
        return answer


def polling(
    transport: Scripted,
    fake: FakeTime,
    *,
    pacing: SourcePacing | None = None,
    **config: Any,
) -> HunterSource:
    return HunterSource(
        DataMode.LIVE,
        transport=transport,
        environ=ENV,
        pacing=pacing,
        clock=fake.clock,
        sleep=fake.sleep,
        **config,
    )


def verify_one() -> EnrichmentRequest:
    return enrich(person(email=ADDRESS))


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_a_202_is_polled_until_a_final_verdict_which_maps_conservatively() -> (
    None
):
    fake = FakeTime()
    transport = Routed(verifier=Sequence(pending(), pending(), verdict("valid")))
    source = polling(transport, fake)
    batch = await source.fetch_raw(verify_one())
    assert paths(transport) == [VERIFIER_PATH] * 3
    assert [call[1] for call in transport.calls] == [{"email": ADDRESS}] * 3
    assert all(call[2] == {"X-API-KEY": KEY} for call in transport.calls)
    [contribution] = source.normalize_checked(batch)
    assert contribution.values["person.email_status"] is EmailStatus.VERIFIED
    assert len(fake.sleeps) == 2  # one wait before each poll, none after the verdict


# Verifies: specs/lead-source-adapters/requirements.md#16.4
@pytest.mark.parametrize(
    ("final", "expected"),
    [
        ("accept_all", EmailStatus.ACCEPT_ALL),
        ("invalid", EmailStatus.INVALID),
        ("webmail", EmailStatus.UNKNOWN),
        ("disposable", EmailStatus.UNKNOWN),
        ("unknown", EmailStatus.UNKNOWN),
    ],
)
async def test_a_polled_verdict_never_overclaims(
    final: str, expected: EmailStatus
) -> None:
    transport = Routed(verifier=Sequence(pending(), verdict(final)))
    source = polling(transport, FakeTime())
    [contribution] = source.normalize_checked(await source.fetch_raw(verify_one()))
    assert contribution.values["person.email_status"] is expected


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_a_still_running_verification_gives_up_after_the_poll_attempts() -> None:
    fake = FakeTime()
    transport = Routed(verifier=Sequence(pending()))
    source = polling(transport, fake, poll_attempts=3, poll_budget_s=1000.0)
    with capture_logs() as logs:
        batch = await source.fetch_raw(verify_one())
    assert len(transport.calls) == 4  # the question, then three polls
    assert len(fake.sleeps) == 3  # never a wait after the last answer
    assert source.normalize_checked(batch) == []  # no verdict: status stays unknown
    exhausted = [
        log for log in logs if log["event"] == "hunter_verification_unfinished"
    ]
    assert exhausted == [
        {
            "event": "hunter_verification_unfinished",
            "log_level": "warning",
            "reason": "attempts",
            "polls": 3,
        }
    ]
    assert ADDRESS not in json.dumps(logs, default=str)


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_polling_stops_when_the_total_wait_budget_is_spent() -> None:
    fake = FakeTime()
    transport = Routed(verifier=Sequence(pending("4")))
    source = polling(
        transport,
        fake,
        poll_attempts=100,
        poll_budget_s=10.0,
        poll_interval_s=1.0,
    )
    with capture_logs() as logs:
        batch = await source.fetch_raw(verify_one())
    assert sum(fake.sleeps) <= 10.0
    assert fake.sleeps == [4.0, 4.0, 2.0]  # the last wait is clamped to what is left
    assert len(transport.calls) == 4
    assert source.normalize_checked(batch) == []
    [event] = [log for log in logs if log["event"] == "hunter_verification_unfinished"]
    assert event["reason"] == "budget"
    assert event["polls"] == 3


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_retry_after_is_honoured_and_clamped_to_the_interval_and_the_budget() -> (
    None
):
    fake = FakeTime()
    transport = Routed(
        verifier=Sequence(
            pending("3"),  # honoured
            pending("0.001"),  # a hint below the interval is raised to it
            pending("Wed, 21 Oct 2026 07:28:00 GMT"),  # not a number: the interval
            pending("999999"),  # clamped to the budget left
            verdict(),
        )
    )
    source = polling(
        transport, fake, poll_attempts=10, poll_budget_s=20.0, poll_interval_s=2.0
    )
    await source.fetch_raw(verify_one())
    assert fake.sleeps == [3.0, 2.0, 2.0, 13.0]


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_each_poll_takes_capacity_from_the_verifier_bucket_only() -> None:
    fake = FakeTime()
    throttle = SourceThrottle(
        "hunter", HunterSource.rate_limit, clock=fake.clock, sleep=fake.sleep
    )
    pacing = SourcePacing(throttle=throttle, retry=RetryPolicy())
    transport = Routed(verifier=Sequence(pending(), pending(), verdict()))
    source = polling(transport, fake, pacing=pacing, poll_interval_s=0.0)
    await source.fetch_raw(verify_one())
    assert throttle.bucket("verifier").available()[0] == pytest.approx(7, abs=0.1)
    assert throttle.bucket("finder").available()[0] == pytest.approx(15, abs=0.1)


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_a_poll_is_one_credit_for_the_address_not_one_per_poll() -> None:
    transport = Routed(verifier=Sequence(pending(), pending(), verdict()))
    batch = await polling(transport, FakeTime()).fetch_raw(verify_one())
    assert len(transport.calls) == 3
    assert credits_in(batch) == 1


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_a_finished_poll_is_not_restarted_by_a_retried_fetch() -> None:
    for answers in (
        Sequence(pending(), verdict()),  # ended in a verdict
        Sequence(pending()),  # ended in a bounded give-up
    ):
        transport = Routed(verifier=answers)
        source = polling(transport, FakeTime(), poll_attempts=2)
        first = await source.fetch_raw(verify_one())
        calls = len(transport.calls)
        second = await source.fetch_raw(verify_one())  # the orchestrator's retry
        assert len(transport.calls) == calls
        assert second.payload == first.payload


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_a_poll_error_is_raised_by_type_and_a_retry_may_ask_again() -> None:
    transport = Routed(
        verifier=Sequence(pending(), TransportResponse(503, {}, None), verdict())
    )
    source = polling(transport, FakeTime())
    with pytest.raises(SourceTransient):
        await source.fetch_raw(verify_one())
    batch = await source.fetch_raw(verify_one())  # nothing was cached by the failure
    assert len(source.normalize_checked(batch)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_a_cancel_during_a_poll_wait_propagates_and_leaves_no_task() -> None:
    waiting = asyncio.Event()
    never = asyncio.Event()

    async def sleep(_: float) -> None:
        waiting.set()
        await never.wait()

    transport = Routed(verifier=Sequence(pending()))
    source = HunterSource(DataMode.LIVE, transport=transport, environ=ENV, sleep=sleep)
    task = asyncio.create_task(source.fetch_raw(verify_one()))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert asyncio.all_tasks() == {asyncio.current_task()}
    assert len(transport.calls) == 1  # no poll after the cancel


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_polling_cannot_outlive_a_run_deadline() -> None:
    async def slow(_: float) -> None:
        await asyncio.sleep(3600)  # a real sleep, cut short by the deadline

    source = HunterSource(
        DataMode.LIVE,
        transport=Routed(verifier=Sequence(pending())),
        environ=ENV,
        sleep=slow,
    )
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await source.fetch_raw(verify_one())
    assert asyncio.all_tasks() == {asyncio.current_task()}


# Verifies: specs/lead-source-adapters/requirements.md#16.4
@pytest.mark.parametrize(
    "config",
    [
        {"poll_attempts": -1},
        {"poll_attempts": True},
        {"poll_budget_s": 0.0},
        {"poll_budget_s": float("inf")},
        {"poll_budget_s": float("nan")},
        {"poll_interval_s": -1.0},
        {"poll_interval_s": float("nan")},
    ],
)
def test_a_poll_bound_that_is_not_a_finite_non_negative_number_is_refused(
    config: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="poll_"):
        polling(Routed(), FakeTime(), **config)


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_zero_poll_attempts_asks_once_and_records_no_verdict() -> None:
    transport = Routed(verifier=Sequence(pending(), verdict()))
    source = polling(transport, FakeTime(), poll_attempts=0)
    batch = await source.fetch_raw(verify_one())
    assert len(transport.calls) == 1
    assert source.normalize_checked(batch) == []


# Verifies: specs/lead-source-adapters/requirements.md#16.4
@pytest.mark.parametrize(
    "config",
    [
        {"poll_budget_s": 10**400},
        {"poll_interval_s": 10**400},
        {"poll_budget_s": True},
        {"poll_interval_s": False},
        {"poll_budget_s": "30"},
        {"poll_attempts": 1.5},
        {"poll_attempts": "5"},
        {"poll_attempts": None},
    ],
)
def test_a_poll_bound_of_the_wrong_kind_or_size_is_refused_by_name(
    config: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="poll_"):
        polling(Routed(), FakeTime(), **config)


# Verifies: specs/lead-source-adapters/requirements.md#16.4
@pytest.mark.parametrize(
    "hint", ["inf", "nan", "-5", "0", "1e9", "1000000000", "", "  ", "9" * 400]
)
async def test_a_hostile_retry_after_never_exceeds_the_budget(hint: str) -> None:
    fake = FakeTime()
    transport = Routed(verifier=Sequence(pending(hint)))
    source = polling(
        transport, fake, poll_attempts=50, poll_budget_s=10.0, poll_interval_s=1.0
    )
    await source.fetch_raw(verify_one())
    assert sum(fake.sleeps) <= 10.0
    assert all(s >= 0 for s in fake.sleeps)
    assert len(transport.calls) <= 51


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_a_budget_smaller_than_the_interval_is_still_bounded() -> None:
    fake = FakeTime()
    transport = Routed(verifier=Sequence(pending()))
    source = polling(transport, fake, poll_budget_s=1.0, poll_interval_s=5.0)
    await source.fetch_raw(verify_one())
    assert fake.sleeps == [1.0]
    assert len(transport.calls) == 2


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_a_polled_unknown_verdict_is_a_normalization_error() -> None:
    transport = Routed(verifier=Sequence(pending(), verdict("deliverable")))
    source = polling(transport, FakeTime())
    batch = await source.fetch_raw(verify_one())
    with pytest.raises(NormalizationError):
        source.normalize(batch)


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_a_batch_of_repeated_addresses_polls_and_pays_each_address_once() -> None:
    transport = Routed(verifier=Sequence(pending(), verdict()))
    source = polling(transport, FakeTime())
    work = enrich(
        person(email="a@example.com"),
        person(email="A@Example.com"),  # the same address, differently written
    )
    batch = await source.fetch_raw(work)
    again = await source.fetch_raw(work)  # the same address again in the same run
    assert len(transport.calls) == 2  # one question and one poll, for one address
    assert credits_in(batch) == 1
    assert again.payload == batch.payload


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_polling_many_addresses_stays_inside_the_verifier_bucket() -> None:
    fake = FakeTime()
    throttle = SourceThrottle(
        "hunter", HunterSource.rate_limit, clock=fake.clock, sleep=fake.sleep
    )
    pacing = SourcePacing(throttle=throttle, retry=RetryPolicy())
    stamps: list[float] = []

    def answer(params: Mapping[str, object]) -> TransportResponse:
        stamps.append(fake.now)
        return pending() if len(stamps) % 2 else verdict()

    transport = Routed(verifier=answer)
    source = polling(transport, fake, pacing=pacing, poll_interval_s=0.0)
    work = enrich(*[person(email=f"p{i}@example.com") for i in range(40)])
    await source.fetch_raw(work)
    assert len(stamps) == 80  # every poll is a paced request
    # A burst of ten, then ten a second: 80 requests cannot finish in under 7 seconds.
    assert stamps[-1] - stamps[0] >= 6.9


# Verifies: specs/lead-source-adapters/requirements.md#16.4
async def test_a_cancel_during_a_poll_request_propagates_and_leaves_no_task() -> None:
    inside = asyncio.Event()

    class Hanging(Routed):
        async def send(self, endpoint: Endpoint, **kwargs: Any) -> TransportResponse:
            if len(self.calls) == 1:
                inside.set()
                await asyncio.Event().wait()
            return await super().send(endpoint, **kwargs)

    transport = Hanging(verifier=Sequence(pending()))
    source = polling(transport, FakeTime())
    task = asyncio.create_task(source.fetch_raw(verify_one()))
    await inside.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert asyncio.all_tasks() == {asyncio.current_task()}
    assert source._verified == {}  # nothing cached by a cancelled poll


# --- Task 15.3: Hunter's inverted status conventions and 451 (16.6, 16.7) ----------

ADA = "ada@example.com"
OTHER = "bob@example.com"
SECRETS = (KEY, ADA, OTHER, "example.com", "Lovelace", "claimed_email")


def status_of(code: int, **headers: str) -> TransportResponse:
    return TransportResponse(code, headers, {"echo": list(SECRETS)})


def answering(code: int, **headers: str) -> Routed:
    bad = status_of(code, **headers)
    return Routed(search=lambda _: bad, finder=lambda _: bad, verifier=lambda _: bad)


# Verifies: specs/lead-source-adapters/requirements.md#16.7
@pytest.mark.parametrize("route", sorted(WORK_FOR_EACH_ROUTE))
async def test_a_403_is_throttling_never_unauthorized_on_every_route(
    route: str,
) -> None:
    transport = answering(403, **{"retry-after": "7"})
    with pytest.raises(SourceRateLimited) as caught:
        await live(transport).fetch_raw(enrich(WORK_FOR_EACH_ROUTE[route]))
    assert not isinstance(caught.value, SourceUnauthorized)
    assert caught.value.retry_after_s == 7.0


# Verifies: specs/lead-source-adapters/requirements.md#16.7
async def test_a_403_without_a_usable_retry_after_carries_none() -> None:
    for hint in ({}, {"retry-after": "soon"}):
        transport = Routed(verifier=Sequence(TransportResponse(403, hint, None)))
        with pytest.raises(SourceRateLimited) as caught:
            await live(transport).fetch_raw(enrich(person(email=ADA)))
        assert caught.value.retry_after_s is None


# Verifies: specs/lead-source-adapters/requirements.md#16.7
@pytest.mark.parametrize("route", sorted(WORK_FOR_EACH_ROUTE))
async def test_a_429_is_quota_exhausted_never_a_retryable_throttle(route: str) -> None:
    transport = answering(429, **{"retry-after": "7"})
    with pytest.raises(SourceQuotaExhausted) as caught:
        await live(transport).fetch_raw(enrich(WORK_FOR_EACH_ROUTE[route]))
    assert not isinstance(caught.value, SourceRateLimited)


# Verifies: specs/lead-source-adapters/requirements.md#16.7
def test_the_retry_policy_retries_a_403_but_not_a_429_or_a_451() -> None:
    retryable = RetryPolicy().retryable
    assert SourceRateLimited in retryable
    assert SourceQuotaExhausted not in retryable
    assert SourceComplianceRestricted not in retryable


# Verifies: specs/lead-source-adapters/requirements.md#16.7
@pytest.mark.parametrize(
    ("code", "expected"),
    [(401, SourceUnauthorized), (408, SourceTransient), (503, SourceTransient)],
)
async def test_the_other_statuses_keep_the_conventional_mapping(
    code: int, expected: type[SourceError]
) -> None:
    with pytest.raises(expected):
        await live(answering(code)).fetch_raw(enrich(person(email=ADA)))


# Verifies: specs/lead-source-adapters/requirements.md#16.7
async def test_a_401_names_the_endpoint_and_a_404_is_plain_permanent() -> None:
    with pytest.raises(SourceUnauthorized) as unauthorized:
        await live(answering(401)).fetch_raw(enrich(person(email=ADA)))
    assert unauthorized.value.endpoint == VERIFIER_PATH
    with pytest.raises(SourceError) as permanent:
        await live(answering(404)).fetch_raw(enrich(person(email=ADA)))
    assert type(permanent.value) is SourceError


# Verifies: specs/lead-source-adapters/requirements.md#16.7
@pytest.mark.parametrize("code", [403, 429])
async def test_these_errors_name_no_key_address_domain_name_or_body(code: int) -> None:
    work = [person(domain="example.com"), person(first="Ada", last="Lovelace")]
    for one in (*work, person(email=ADA)):
        with capture_logs() as logs, pytest.raises(SourceError) as caught:
            await live(answering(code)).fetch_raw(enrich(one))
        for text in (str(caught.value), repr(caught.value), str(logs)):
            for secret in SECRETS:
                assert secret not in text


def restricted() -> TransportResponse:
    return TransportResponse(451, {}, {"errors": [{"details": "claimed_email"}]})


def one_restricted(address: str = ADA) -> Responder:
    def respond(params: Mapping[str, object]) -> TransportResponse:
        if params["email"] == address:
            return restricted()
        return verdict(address=str(params["email"]))

    return respond


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_451_on_one_address_is_recorded_and_the_batch_goes_on() -> None:
    transport = Routed(verifier=one_restricted())
    source = live(transport)
    batch = await source.fetch_raw(
        enrich(person(email=ADA), person(email=OTHER), person(email="cy@example.com"))
    )
    assert len(transport.calls) == 3  # the addresses after the restricted one are asked
    contributions = source.normalize_checked(batch)
    [flagged] = [c for c in contributions if c.values.get("suppressed") is True]
    assert flagged.values["person.email"] == ADA
    assert set(flagged.values) == {"person.email", "suppressed"}  # no verdict, no data
    assert len(contributions) == 3
    others = [c for c in contributions if c is not flagged]
    assert {c.values["person.email"] for c in others} == {OTHER, "cy@example.com"}
    assert all(not c.values.get("suppressed") for c in others)


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_restricted_address_contributes_no_verdict_confidence_or_source() -> (
    None
):
    transport = Routed(verifier=lambda _: restricted())
    source = live(transport)
    batch = await source.fetch_raw(enrich(person(email=ADA)))
    [flagged] = source.normalize_checked(batch)
    assert flagged.values == {"person.email": ADA, "suppressed": True}
    assert all(
        record.confidence_origin is ConfidenceOrigin.NONE
        for record in flagged.provenance
    )
    assert credits_in(batch) == 0  # nothing was delivered, so nothing is counted


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_451_on_the_finder_flags_that_person_and_contributes_no_address() -> (
    None
):
    def finder(params: Mapping[str, object]) -> TransportResponse:
        if params["last_name"] == "Lovelace":
            return restricted()
        return found_one("bob@example.com")

    transport = Routed(finder=finder)
    source = live(transport)
    batch = await source.fetch_raw(
        enrich(
            person(first="Ada", last="Lovelace"), person(first="Bob", last="Babbage")
        )
    )
    assert len(transport.calls) == 2
    contributions = source.normalize_checked(batch)
    [flagged] = [c for c in contributions if c.values.get("suppressed") is True]
    assert "person.email" not in flagged.values
    assert flagged.values["company.domain"] == "example.com"
    assert isinstance(flagged.values["person.first_name"], UntrustedText)
    assert [c.values["person.email"] for c in contributions if c is not flagged] == [
        OTHER
    ]


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_451_on_a_domain_search_is_a_compliance_error_not_a_flag() -> None:
    transport = Routed(search=lambda _: restricted())
    with capture_logs() as logs, pytest.raises(SourceComplianceRestricted) as caught:
        await live(transport).fetch_raw(enrich(person(domain="example.com")))
    for text in (
        str(caught.value),
        repr(caught.value),
        caught.value.subject,
        str(logs),
    ):
        for secret in SECRETS:
            assert secret not in text


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_restriction_is_asked_once_even_when_the_fetch_retries() -> None:
    transport = Routed(verifier=one_restricted())
    source = live(transport)
    work = enrich(person(email=ADA), person(email=OTHER))
    first = await source.fetch_raw(work)
    second = await source.fetch_raw(work)
    assert len(transport.calls) == 2  # no second payment for the 451 or the verdict
    assert second.payload == first.payload


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_restriction_leaks_no_address_or_body_into_logs_or_batch() -> None:
    transport = Routed(verifier=lambda _: restricted())
    source = live(transport)
    with capture_logs() as logs:
        batch = await source.fetch_raw(enrich(person(email=ADA)))
        source.normalize_checked(batch)
    assert "claimed_email" not in str(batch.payload)
    assert "claimed_email" not in str(logs)
    assert ADA not in str(logs)


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_restricted_batch_of_the_wrong_shape_is_a_normalization_error() -> None:
    source = live(Routed())
    bad: list[object] = [None, "x", [None], [{"email": 3}], [{}]]
    for restrictions in bad:
        batch = RawBatch(
            source_name="hunter",
            payload={
                "searches": [],
                "finds": [],
                "verifications": [],
                "restricted_verifications": restrictions,
                "credits_billable": True,
            },
        )
        with pytest.raises(NormalizationError):
            source.normalize(batch)


# Verifies: specs/lead-source-adapters/requirements.md#16.7
@pytest.mark.parametrize(
    ("code", "kind"), [(403, SourceRateLimited), (429, SourceQuotaExhausted)]
)
async def test_a_403_or_429_mid_poll_stops_polling_and_pays_no_more(
    code: int, kind: type[SourceError]
) -> None:
    transport = Routed(
        verifier=Sequence(pending(), pending(), TransportResponse(code, {}, None))
    )
    fake = FakeTime()
    with pytest.raises(kind):
        await polling(transport, fake).fetch_raw(
            enrich(person(email=ADA), person(email=OTHER))
        )
    assert len(transport.calls) == 3  # no poll after the error, no second address


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_451_mid_poll_stops_that_address_and_the_batch_goes_on() -> None:
    def answer(params: Mapping[str, object]) -> TransportResponse:
        if params["email"] == ADA:
            return restricted() if seen[ADA] else pending_once(ADA)
        return verdict(address=str(params["email"]))

    seen = {ADA: False}

    def pending_once(address: str) -> TransportResponse:
        seen[address] = True
        return pending()

    transport = Routed(verifier=answer)
    source = polling(transport, FakeTime())
    batch = await source.fetch_raw(enrich(person(email=ADA), person(email=OTHER)))
    assert [call[1]["email"] for call in transport.calls] == [ADA, ADA, OTHER]
    contributions = source.normalize_checked(batch)
    flags = {
        c.values["person.email"]: c.values.get("suppressed") for c in contributions
    }
    assert flags == {ADA: True, OTHER: None}


class _Discovery(BaseLeadSource):
    """A stand-in discovery source handing Hunter one known address to verify."""

    name: ClassVar[str] = "discovery"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, Any]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        return RawBatch(source_name=self.name, payload={})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return [person(email=ADA)]


async def run_orchestrated(
    transport: Scripted,
) -> tuple[dict[str, SourceOutcome], dict[str, int]]:
    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        if source_class is HunterSource:
            return HunterSource(mode, transport=transport, environ=ENV, pacing=pacing)
        return _Discovery(mode)

    orchestrator = IngestionOrchestrator(
        SourceRegistry([_Discovery, HunterSource]),
        resolve_mode=lambda _c, _s: ModeResolution(DataMode.LIVE, "test"),
        build_source=build,
        max_concurrent_sources=2,
        run_timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.002),
    )
    results = await orchestrator.run(SourceRequest(kind="search"))
    flagged = {
        r.source_name: sum(
            1 for c in r.contributions or () if c.values.get("suppressed") is True
        )
        for r in results
    }
    return {r.source_name: r.outcome for r in results}, flagged


# Verifies: specs/lead-source-adapters/requirements.md#16.7
async def test_end_to_end_a_403_is_throttling_and_a_429_halts_the_source() -> None:
    throttled, _ = await run_orchestrated(answering(403, **{"retry-after": "0"}))
    assert throttled["hunter"].status is SourceStatus.RATE_LIMITED
    assert (throttled["hunter"].attempted, throttled["hunter"].retries) == (3, 2)
    assert throttled["discovery"].status is SourceStatus.OK
    transport = answering(429)
    spent, _ = await run_orchestrated(transport)
    assert spent["hunter"].status is SourceStatus.QUOTA_EXHAUSTED
    assert (spent["hunter"].attempted, spent["hunter"].retries) == (1, 0)
    assert len(transport.calls) == 1
    assert spent["discovery"].status is SourceStatus.OK


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_end_to_end_a_restricted_address_reaches_the_run_as_a_flagged_lead() -> (
    None
):
    outcomes, flagged = await run_orchestrated(Routed(verifier=lambda _: restricted()))
    assert outcomes["hunter"].status is SourceStatus.OK
    assert flagged["hunter"] == 1
    for text in (outcomes["hunter"].error, str(outcomes["hunter"])):
        for secret in SECRETS:
            assert secret not in str(text)


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_end_to_end_a_restricted_domain_search_is_a_compliance_outcome() -> None:
    class Domains(_Discovery):
        def normalize(self, raw: RawBatch) -> list[LeadContribution]:
            return [person(domain="example.com")]

    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        transport = Routed(search=lambda _: restricted())
        if source_class is HunterSource:
            return HunterSource(mode, transport=transport, environ=ENV, pacing=pacing)
        return Domains(mode)

    orchestrator = IngestionOrchestrator(
        SourceRegistry([_Discovery, HunterSource]),
        resolve_mode=lambda _c, _s: ModeResolution(DataMode.LIVE, "test"),
        build_source=build,
        max_concurrent_sources=2,
        run_timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.002),
    )
    results = await orchestrator.run(SourceRequest(kind="search"))
    [hunter] = [r for r in results if r.source_name == "hunter"]
    assert hunter.outcome.status is SourceStatus.COMPLIANCE_RESTRICTED
    assert hunter.outcome.attempted == 1  # never retried
    for secret in SECRETS:
        assert secret not in str(hunter.outcome.error)


# Verifies: specs/lead-source-adapters/requirements.md#16.6
@pytest.mark.parametrize("route", ["finder", "verifier"])
async def test_the_per_question_catch_lets_every_other_failure_through(
    route: str,
) -> None:
    work = (
        person(first="Ada", last="Lovelace") if route == "finder" else person(email=ADA)
    )
    for code, kind in (
        (403, SourceRateLimited),
        (404, SourceError),
        (503, SourceTransient),
    ):
        with pytest.raises(kind):
            await live(Routed(**{route: lambda _, c=code: status_of(c)})).fetch_raw(
                enrich(work)
            )

    def cancel(_: Mapping[str, object]) -> TransportResponse:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await live(Routed(**{route: cancel})).fetch_raw(enrich(work))


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_restricted_address_never_keeps_contact_data_from_the_finder() -> None:
    transport = Routed(verifier=lambda _: restricted(), finder=lambda _: found_one(ADA))
    source = live(transport)
    batch = await source.fetch_raw(
        enrich(person(email=ADA), person(first="Ada", last="Lovelace"))
    )
    contributions = source.normalize_checked(batch)
    assert [c.values for c in contributions] == [
        {"person.email": ADA, "suppressed": True}
    ]


# --- leads are per person: every person reaches Hunter (follow-up 2026-10-06) -------
#
# User decision: a lead is a person; several leads at one company are fine as long as
# they are different people. The finder and verifier are per-person calls, so every
# Lead must reach them; only the domain search is per company, paid once per domain.

ACME = "acme.com"
ACME_ADDRESSES = ("v1@acme.com", "v2@acme.com")
ACME_NAMES = (("Ada", "Lovelace"), ("Grace", "Hopper"), ("Alan", "Turing"))


def acme_people() -> tuple[LeadContribution, ...]:
    """Seven Leads at one company: two known addresses, three names, two bare."""
    return (
        *(person(email=a, domain=ACME) for a in ACME_ADDRESSES),
        *(person(first=f, last=last, domain=ACME) for f, last in ACME_NAMES),
        person(domain=ACME),
        person(domain=ACME),
    )


def acme_finder(params: Mapping[str, object]) -> TransportResponse:
    address = f"{params['first_name']}.{params['last_name']}@{ACME}".lower()
    return found_one(
        address,
        first_name=params["first_name"],
        last_name=params["last_name"],
        domain=params["domain"],
    )


def acme_routed(**answers: Responder) -> Routed:
    return Routed(
        finder=answers.get("finder", acme_finder),
        verifier=answers.get("verifier", lambda p: verdict(address=str(p["email"]))),
        search=answers.get(
            "search", lambda _: found(ACME, emails=[email(f"info@{ACME}")])
        ),
    )


def calls_to(transport: Scripted, path: str) -> list[Mapping[str, object]]:
    return [params for endpoint, params, _ in transport.calls if endpoint.path == path]


def finder_addresses() -> set[str]:
    return {f"{f}.{last}@{ACME}".lower() for f, last in ACME_NAMES}


# Verifies: specs/lead-source-adapters/requirements.md#16.3
# Verifies: specs/lead-source-adapters/requirements.md#16.8
async def test_every_person_at_one_company_is_asked_and_the_domain_once() -> None:
    transport = acme_routed()
    source = live(transport)
    batch = await source.fetch_raw(enrich(*acme_people()))
    assert sorted(
        str(p["email"]) for p in calls_to(transport, VERIFIER_PATH)
    ) == sorted(ACME_ADDRESSES)
    assert len(calls_to(transport, FINDER_PATH)) == len(ACME_NAMES)
    assert calls_to(transport, SEARCH_PATH) == [{"domain": ACME, "limit": 100}]
    out = {c.values.get("person.email") for c in source.normalize_checked(batch)}
    assert out == {*ACME_ADDRESSES, *finder_addresses(), f"info@{ACME}"}
    assert credits_in(batch) == len(transport.calls) == 6


# Verifies: specs/lead-source-adapters/requirements.md#16.8
async def test_a_retried_fetch_pays_for_no_person_or_domain_twice() -> None:
    failed = {"once": False}

    def flaky(params: Mapping[str, object]) -> TransportResponse:
        if params["email"] == ACME_ADDRESSES[-1] and not failed["once"]:
            failed["once"] = True
            return status_of(503)
        return verdict(address=str(params["email"]))

    transport = acme_routed(verifier=flaky)
    source = live(transport)
    with pytest.raises(SourceTransient):
        await source.fetch_raw(enrich(*acme_people()))
    batch = await source.fetch_raw(enrich(*acme_people()))
    # Six questions, one of them asked twice because its first answer was a 503.
    assert len(transport.calls) == 7
    assert [p["email"] for p in calls_to(transport, VERIFIER_PATH)] == [
        ACME_ADDRESSES[0],
        ACME_ADDRESSES[1],
        ACME_ADDRESSES[1],
    ]
    assert len(calls_to(transport, FINDER_PATH)) == len(ACME_NAMES)
    assert len(calls_to(transport, SEARCH_PATH)) == 1
    assert credits_in(batch) == 6
    await source.fetch_raw(enrich(*acme_people()))
    assert len(transport.calls) == 7  # a third fetch buys nothing at all


class _PerCompanyProbe(BaseLeadSource):
    """A genuinely per-company paid source: records the work list it is handed."""

    name: ClassVar[str] = "per_company_probe"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.ENRICH})
    rate_limit: ClassVar[Mapping[str, Any]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.PAID
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_COMPANY
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()
    handed: ClassVar[list[int]] = []

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        assert isinstance(request, EnrichmentRequest)
        type(self).handed.append(len(request.work_list))
        return RawBatch(source_name=self.name, payload={})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return []


async def run_people(
    transport: Scripted, people: tuple[LeadContribution, ...]
) -> dict[str, list[LeadContribution]]:
    """The real orchestrator: a stub Discovery hands ``people`` to the real tiers."""
    results = await run_people_results(transport, people)
    return {name: list(r.contributions or ()) for name, r in results.items()}


async def run_people_results(
    transport: Scripted, people: tuple[LeadContribution, ...]
) -> dict[str, SourceResult]:

    class People(_Discovery):
        def normalize(self, raw: RawBatch) -> list[LeadContribution]:
            return list(people)

    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        if source_class is HunterSource:
            return HunterSource(mode, transport=transport, environ=ENV, pacing=pacing)
        return source_class(mode)

    orchestrator = IngestionOrchestrator(
        SourceRegistry([People, HunterSource, _PerCompanyProbe]),
        resolve_mode=lambda _c, _s: ModeResolution(DataMode.LIVE, "test"),
        build_source=build,
        max_concurrent_sources=3,
        run_timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.002),
    )
    results = await orchestrator.run(SourceRequest(kind="search"))
    assert all(r.outcome.status is SourceStatus.OK for r in results)
    return {r.source_name: r for r in results}


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_end_to_end_every_person_at_one_company_reaches_hunter() -> None:
    _PerCompanyProbe.handed = []
    transport = acme_routed()
    out = await run_people(transport, acme_people())
    assert sorted(
        str(p["email"]) for p in calls_to(transport, VERIFIER_PATH)
    ) == sorted(ACME_ADDRESSES)
    assert sorted(
        (str(p["first_name"]), str(p["last_name"]))
        for p in calls_to(transport, FINDER_PATH)
    ) == sorted(ACME_NAMES)
    assert len(calls_to(transport, SEARCH_PATH)) == 1
    assert {c.values.get("person.email") for c in out["hunter"]} == {
        *ACME_ADDRESSES,
        *finder_addresses(),
        f"info@{ACME}",
    }
    assert len(out["discovery"]) == len(acme_people())  # no lead dropped
    # A genuinely per-company source in the same run is still handed one Lead.
    assert _PerCompanyProbe.handed == [1]


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_end_to_end_two_people_with_one_name_at_one_company_stay_two() -> None:
    twins = (
        person(email="john.smith@acme.com", first="John", last="Smith", domain=ACME),
        person(email="jsmith@acme.com", first="John", last="Smith", domain=ACME),
    )
    transport = acme_routed()
    out = await run_people(transport, twins)
    assert sorted(str(p["email"]) for p in calls_to(transport, VERIFIER_PATH)) == [
        "john.smith@acme.com",
        "jsmith@acme.com",
    ]
    assert sorted(str(c.values["person.email"]) for c in out["hunter"]) == [
        "john.smith@acme.com",
        "jsmith@acme.com",
    ]
    assert len(out["discovery"]) == 2


# Verifies: specs/lead-source-adapters/requirements.md#16.8
async def test_end_to_end_an_orchestrator_retry_repays_only_the_failed_question() -> (
    None
):
    # The orchestrator retries the whole fetch on the same instance; the per-run caches
    # keep every answered finder, verifier and domain-search question from being paid
    # twice, and the reported Credits match the live calls that delivered an answer.
    failed = {"once": False}

    def flaky(params: Mapping[str, object]) -> TransportResponse:
        if params["email"] == ACME_ADDRESSES[-1] and not failed["once"]:
            failed["once"] = True
            return status_of(503)
        return verdict(address=str(params["email"]))

    _PerCompanyProbe.handed = []
    transport = acme_routed(verifier=flaky)
    results = await run_people_results(transport, acme_people())
    hunter = results["hunter"]
    assert (hunter.outcome.attempted, hunter.outcome.retries) == (2, 1)
    assert len(transport.calls) == 7  # six questions, the 503 one asked twice
    assert len(calls_to(transport, FINDER_PATH)) == len(ACME_NAMES)
    assert len(calls_to(transport, SEARCH_PATH)) == 1
    assert hunter.batch is not None
    assert credits_in(hunter.batch) == 6


def test_hunter_runs_in_a_paid_tier_of_its_own_before_apollo() -> None:
    # Reverses the earlier pin "Hunter shares Apollo's tier" (user decision,
    # 2026-10-06): Hunter declares yields_suppression, so a Hunter 451 prunes that
    # person from the work list before Apollo's paid match is asked about them.
    def tier(source: type[BaseLeadSource]) -> tuple[bool, bool, bool, int]:
        cost, no_suppression, evidence_only, unit, _ = enrichment_sort_key(source)
        return cost, no_suppression, evidence_only, unit

    # Suppression outranks charge unit within the paid tier, so Hunter also moves
    # ahead of Google Search (which prunes nothing, so it asks Hunter nothing less).
    assert tier(HubSpotSource) < tier(HunterSource) < tier(GoogleSearchSource)
    assert tier(HunterSource) < tier(ApolloSource)  # an earlier tier, not shared


# --- the finder attaches to the person it was asked for (follow-up 2026-10-06) ------
#
# Review finding (HIGH): a found address carried only Hunter's answer, so the merge
# made it a Lead of its own. It must carry the identity it was asked for (the domain,
# the name asked, and the LinkedIn URL when the request had one) so the normal Match
# Keys join it to that person, and never to two distinguishable people at once.

ADA_LINKEDIN = "https://www.linkedin.com/in/ada-lovelace"
OTHER_LINKEDIN = "https://www.linkedin.com/in/ada-lovelace-2"
ADA_AT_ACME = "ada.lovelace@acme.com"
RANKS = {"discovery": 2, "hunter": 1, "per_company_probe": 3}


def requester(
    *,
    first: object = "Ada",
    last: str = "Lovelace",
    domain: str = ACME,
    linkedin: str | None = None,
    employer: str | None = None,
) -> LeadContribution:
    """A person another source found: a name and a company, no address."""
    values: dict[str, object] = {
        "person.first_name": first,
        "person.last_name": last,
        "company.domain": domain,
    }
    if linkedin is not None:
        values["person.linkedin_url"] = linkedin
    if employer is not None:
        values["company.name"] = employer
    return LeadContribution(
        source_name="discovery",
        values=values,
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name="discovery",
                data_mode=DataMode.LIVE,
                fetched_at=datetime.now(UTC),
                raw_field_path=path,
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=isinstance(value, UntrustedText),
            )
            for path, value in values.items()
        ),
    )


def ada_found(status: str = "valid", **more: object) -> Responder:
    """Hunter's finder answer for Ada, spelled its own way, with a verdict."""
    return lambda _: found_one(
        ADA_AT_ACME,
        first_name="ADA",
        last_name="LOVELACE",
        domain="hunter-echo.example",
        linkedin_url="https://www.linkedin.com/in/someone-hunter-thinks",
        verification={"date": None, "status": status},
        **more,
    )


def merged(out: dict[str, list[LeadContribution]]) -> list[Any]:
    """The real merge: clustering, then projection, over every contribution."""
    contributions = [c for name in ("discovery", "hunter") for c in out[name]]
    return [
        project_lead(cluster, RANKS) for cluster in cluster_contributions(contributions)
    ]


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_a_found_address_carries_the_identity_it_was_asked_for() -> None:
    source = live(acme_routed(finder=ada_found()))
    asked = requester(
        first=UntrustedText(value="Ada", truncated=False, original_length=3),
        linkedin=ADA_LINKEDIN,
    )
    [contribution] = source.normalize_checked(await source.fetch_raw(enrich(asked)))
    values = contribution.values
    assert values["person.email"] == ADA_AT_ACME
    assert values["company.domain"] == ACME  # the domain asked, not Hunter's echo
    assert values["person.first_name"] == UntrustedText(
        value="Ada", truncated=False, original_length=3
    )
    assert values["person.last_name"].value == "Lovelace"
    assert values["person.linkedin_url"] == ADA_LINKEDIN  # the request's, not Hunter's
    records = {p.canonical_path: p for p in contribution.provenance}
    for path, raw in (
        ("company.domain", "asked.domain"),
        ("person.first_name", "asked.first_name"),
        ("person.last_name", "asked.last_name"),
        ("person.linkedin_url", "asked.linkedin_url"),
    ):
        assert records[path].raw_field_path == raw
        assert records[path].source_name == "hunter"


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_a_request_without_linkedin_gets_no_linkedin_from_hunter() -> None:
    source = live(acme_routed(finder=ada_found()))
    [contribution] = source.normalize_checked(
        await source.fetch_raw(enrich(requester()))
    )
    assert "person.linkedin_url" not in contribution.values
    assert contribution.values["company.domain"] == ACME


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_end_to_end_the_found_address_lands_on_the_person_by_linkedin() -> None:
    out = await run_people(
        acme_routed(finder=ada_found("valid")), (requester(linkedin=ADA_LINKEDIN),)
    )
    [result] = merged(out)
    assert result.lead is not None
    assert result.lead.email == ADA_AT_ACME
    assert result.lead.email_status is EmailStatus.VERIFIED
    assert str(result.lead.linkedin_url).rstrip("/") == ADA_LINKEDIN
    assert result.contributing_sources == ("discovery", "hunter")


ECHOED = ("company.domain", "person.first_name", "person.last_name")


# Verifies: specs/lead-source-adapters/requirements.md#16.2
@pytest.mark.parametrize("hunter_rank", [1, 5], ids=["ranked_below", "ranked_above"])
async def test_the_echoed_identity_neither_corroborates_nor_conflicts(
    hunter_rank: int,
) -> None:
    # The name, domain and LinkedIn URL Hunter's contribution carries are the
    # request, not something Hunter observed: they join the address to the person
    # but must not count as a second source agreeing, nor compete (and win) against
    # the requester's own spelling.
    asked = requester(first=" Ada ", domain="WWW.Acme.com", linkedin=ADA_LINKEDIN + "/")
    out = await run_people(acme_routed(finder=ada_found("valid")), (asked,))
    contributions = [c for name in ("discovery", "hunter") for c in out[name]]
    [cluster] = cluster_contributions(contributions)
    result = project_lead(cluster, {**RANKS, "hunter": hunter_rank})
    agreement = dict(result.agreement)
    for path in (*ECHOED, "person.linkedin_url"):
        assert agreement[path] == 1, path
    assert [c.canonical_path for c in result.conflicts] == []
    assert result.lead is not None
    assert result.lead.email == ADA_AT_ACME
    for record in result.provenance:  # the requester's own spelling stands alone
        if record.canonical_path in (*ECHOED, "person.linkedin_url"):
            assert (record.source_name, record.superseded) == ("discovery", False)
    assert result.contributing_sources == ("discovery", "hunter")


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_an_echoed_identity_alone_still_names_the_lead() -> None:
    # With no other candidate for a path (the requester's record clustered apart),
    # the echo is all there is and still fills the field.
    source = live(acme_routed(finder=ada_found()))
    [found] = source.normalize_checked(
        await source.fetch_raw(enrich(requester(linkedin=ADA_LINKEDIN)))
    )
    [cluster] = cluster_contributions([found])
    result = project_lead(cluster, RANKS)
    assert result.lead is not None
    assert result.lead.full_name == "Ada Lovelace"
    assert str(result.lead.linkedin_url).rstrip("/") == ADA_LINKEDIN


# Verifies: specs/lead-source-adapters/requirements.md#16.2
async def test_end_to_end_without_linkedin_the_address_joins_by_name_and_domain() -> (
    None
):
    # A non-verified address is no Match Key, so name+domain (with the shared
    # employer as corroboration) is the link; the name is the one asked for.
    out = await run_people(
        acme_routed(finder=ada_found("accept_all", company="Acme Corp")),
        (requester(employer="Acme Corp"),),
    )
    [result] = merged(out)
    assert result.lead is not None
    assert result.lead.email == ADA_AT_ACME
    assert result.contributing_sources == ("discovery", "hunter")


# Verifies: specs/lead-source-adapters/requirements.md#16.3
@pytest.mark.parametrize(
    ("first", "second"),
    [(ADA_LINKEDIN, OTHER_LINKEDIN), (ADA_LINKEDIN, None)],
    ids=["two_linkedin_urls", "linkedin_and_bare"],
)
async def test_one_name_for_two_distinguishable_people_is_not_asked_or_attached(
    first: str, second: str | None
) -> None:
    transport = acme_routed(finder=ada_found())
    source = live(transport)
    twins = (requester(linkedin=first), requester(linkedin=second))
    with capture_logs() as logs:
        batch = await source.fetch_raw(enrich(*twins))
    assert calls_to(transport, FINDER_PATH) == []  # no Credit for an unusable answer
    assert batch.payload["finds"] == []
    assert credits_in(batch) == 0
    assert source.normalize_checked(batch) == []
    [warning] = [e for e in logs if e["event"] == "hunter_finder_ambiguous"]
    assert warning["log_level"] == "warning"
    for secret in ("Ada", "Lovelace", ACME, "ada-lovelace"):
        assert secret not in str(logs)


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_end_to_end_two_people_with_one_name_never_share_one_found_address() -> (
    None
):
    twins = (requester(linkedin=ADA_LINKEDIN), requester(linkedin=OTHER_LINKEDIN))
    out = await run_people(acme_routed(finder=ada_found()), twins)
    results = merged(out)
    assert len(results) == 2
    assert all(r.lead is not None and r.lead.email is None for r in results)


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_indistinguishable_records_of_one_name_share_one_finder_answer() -> None:
    transport = acme_routed(finder=ada_found())
    source = live(transport)
    batch = await source.fetch_raw(enrich(requester(), requester(first="ada")))
    assert len(calls_to(transport, FINDER_PATH)) == 1
    [contribution] = source.normalize_checked(batch)
    assert contribution.values["person.email"] == ADA_AT_ACME


# Verifies: specs/lead-source-adapters/requirements.md#16.3
async def test_a_cached_answer_is_not_reused_for_a_second_distinguishable_person() -> (
    None
):
    transport = acme_routed(finder=ada_found())
    source = live(transport)
    first = await source.fetch_raw(enrich(requester(linkedin=ADA_LINKEDIN)))
    retried = await source.fetch_raw(enrich(requester(linkedin=ADA_LINKEDIN)))
    other = await source.fetch_raw(enrich(requester(linkedin=OTHER_LINKEDIN)))
    assert len(calls_to(transport, FINDER_PATH)) == 1
    assert len(first.payload["finds"]) == len(retried.payload["finds"]) == 1
    assert other.payload["finds"] == []
    assert source.normalize_checked(other) == []


# --- credits follow Hunter's documented billing (follow-up 2026-10-06) --------------
#
# help.hunter.io "How do credits work": Domain Search costs 1 credit per 1-10 addresses
# returned (none when none); Email Finder costs 1 only when an address is found; Email
# Verifier costs 1 (Data plans; 0.5 on All-in-one plans, kept at 1 as conservative).


# Verifies: specs/lead-source-adapters/requirements.md#16.8
@pytest.mark.parametrize(
    ("returned", "credits"), [(0, 0), (1, 1), (10, 1), (11, 2), (25, 3), (100, 10)]
)
async def test_a_domain_search_costs_one_credit_per_ten_addresses_returned(
    returned: int, credits: int
) -> None:
    listed = [email(f"p{i}@example.com") for i in range(returned)]
    transport = Scripted(lambda _: found(emails=listed))
    batch = await live(transport).fetch_raw(enrich(lead("example.com")))
    assert credits_in(batch) == credits


# Verifies: specs/lead-source-adapters/requirements.md#16.8
@pytest.mark.parametrize(
    ("address", "credits"), [("ada@example.com", 1), (None, 0)], ids=["found", "none"]
)
async def test_a_finder_costs_one_credit_only_when_it_finds_an_address(
    address: str | None, credits: int
) -> None:
    transport = Routed(finder=lambda _: found_one(address))
    batch = await live(transport).fetch_raw(enrich(person(first="Ada", last="L")))
    assert len(calls_to(transport, FINDER_PATH)) == 1
    assert credits_in(batch) == credits


# Verifies: specs/lead-source-adapters/requirements.md#16.8
@pytest.mark.parametrize("status", ["valid", "invalid", "accept_all", "unknown"])
async def test_a_verification_costs_one_credit_whatever_the_verdict(
    status: str,
) -> None:
    transport = Routed(verifier=lambda _: verdict(status))
    batch = await live(transport).fetch_raw(enrich(person(email="a@example.com")))
    assert credits_in(batch) == 1


# Verifies: specs/lead-source-adapters/requirements.md#16.8
def test_a_search_whose_addresses_are_not_a_list_cannot_be_priced() -> None:
    batch = RawBatch(
        source_name="hunter",
        payload={
            "searches": [{"domain": "example.com", "response": {"data": {}}}],
            "credits_billable": True,
        },
    )
    with pytest.raises(NormalizationError):
        credits_in(batch)
