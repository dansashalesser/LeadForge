"""Apollo enriches people other sources found (follow-up, user decision 2026-10-06).

User decision (verbatim): "can't we use either source to enhance information from
other sources? linkedin and apollo are the most relevant but can't we use other search
methods/terms if an apollo id isn't found to find the company/person?"

Each work-list person climbs a lookup ladder and stops at the first hit: the Apollo id
when known, then the LinkedIn URL, then the email, then first and last name with the
company domain (or the company name when there is no domain). The answer carries the
requester's identity (``asked.*``) so the merge puts it on that person, and a
name-only hit carries a lower Field Confidence than an id, LinkedIn or email hit.
"""

import socket
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest
from structlog.testing import capture_logs

from leadforge.lead_ingestion.adapters.apollo import (
    RUNG_CONFIDENCE,
    ApolloSource,
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
)
from leadforge.lead_ingestion.clustering import cluster_contributions
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
    UntrustedText,
)
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    SourceResult,
    SourceStatus,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.projection import ProjectionResult, project_lead
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.transport import TransportResponse

MATCH_PATH = "/api/v1/people/match"
SEARCH_PATH = "/api/v1/mixed_people/api_search"
ENV = {"APOLLO_API_KEY": "test-key-not-real"}
ONE_TERM: Mapping[str, object] = {"datastax": ["datastax"]}

ADA_LINKEDIN = "https://www.linkedin.com/in/ada-lovelace"
OTHER_LINKEDIN = "https://www.linkedin.com/in/ada-lovelace-2"
ACME = "acme.com"
ADA_EMAIL = "ada@acme.com"
GONE = "gone@acme.com"

Responder = Callable[[Mapping[str, object]], TransportResponse]


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a test reached for the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)


class Scripted:
    """Records every call; match calls are answered by ``responder``."""

    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.calls.append((endpoint.path, dict(params or {})))
        if endpoint.path == SEARCH_PATH:
            body = {"total_entries": 0, "people": []}
            return TransportResponse(status=200, headers={}, body=body)
        return self.responder(params or {})

    @property
    def matches(self) -> list[dict[str, object]]:
        return [params for path, params in self.calls if path == MATCH_PATH]


def answer(
    *,
    confidence: str = "high",
    person_id: str = "apollo-ada",
    linkedin: str | None = "http://www.linkedin.com/in/ada-lovelace",
    email: str | None = ADA_EMAIL,
    title: str = "CTO",
    organization: str = "Acme Corp",
) -> TransportResponse:
    if confidence == "none":
        return TransportResponse(
            status=200, headers={}, body={"match_confidence": "none"}
        )
    person: dict[str, object] = {
        "id": person_id,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "title": title,
        "organization": {"name": organization},
    }
    if linkedin is not None:
        person["linkedin_url"] = linkedin
    if email is not None:
        person["email"] = email
        person["email_status"] = "verified"
    body = {"match_confidence": confidence, "person": person}
    return TransportResponse(status=200, headers={}, body=body)


NO_MATCH = answer(confidence="none")


ADA = answer()


def by(rung: str, hit: TransportResponse = ADA) -> Responder:
    """Answers ``hit`` to a lookup by ``rung``'s parameter, no match otherwise."""
    return lambda params: hit if rung in params else NO_MATCH


def person(
    *,
    source: str = "crm",
    first: object = "Ada",
    last: object = "Lovelace",
    domain: str | None = ACME,
    linkedin: str | None = None,
    email: str | None = None,
    email_status: str | None = None,
    employer: str | None = None,
    title: str | None = None,
    provider_id: str | None = None,
) -> LeadContribution:
    """A person another source found."""
    values: dict[str, object] = {}
    for path, value in (
        ("person.first_name", first),
        ("person.last_name", last),
        ("company.domain", domain),
        ("person.linkedin_url", linkedin),
        ("person.email", email),
        ("person.email_status", email_status),
        ("company.name", employer),
        ("person.title", title),
        ("person.provider_id", provider_id),
    ):
        if value is not None:
            values[path] = value
    return LeadContribution(
        source_name=source,
        values=values,
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name=source,
                data_mode=DataMode.LIVE,
                fetched_at=datetime.now(UTC),
                raw_field_path=path,
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=isinstance(value, UntrustedText),
            )
            for path, value in values.items()
        ),
    )


def enrich(*people: LeadContribution) -> EnrichmentRequest:
    return EnrichmentRequest(kind="enrich", work_list=people)


def apollo(transport: Scripted) -> ApolloSource:
    return ApolloSource(
        DataMode.LIVE, transport=transport, vocabulary=ONE_TERM, environ=ENV
    )


def observed(contribution: LeadContribution) -> list[FieldProvenance]:
    return [
        p for p in contribution.provenance if not p.raw_field_path.startswith("asked.")
    ]


# ------------------------------------------------------------------ the ladder


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_a_person_from_another_source_is_looked_up_by_linkedin_url() -> None:
    transport = Scripted(by("linkedin_url"))
    source = apollo(transport)
    batch = await source.fetch_raw(enrich(person(linkedin=ADA_LINKEDIN)))
    assert transport.matches == [{"linkedin_url": ADA_LINKEDIN}]
    [found] = source.normalize_checked(batch)
    assert found.values["person.linkedin_url"] == ADA_LINKEDIN  # the request's text
    records = {p.canonical_path: p for p in found.provenance}
    assert records["person.linkedin_url"].raw_field_path == "asked.linkedin_url"
    assert found.values["person.email"] == ADA_EMAIL
    assert credits_in(batch) == 1


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_the_ladder_falls_through_on_no_match_and_stops_at_the_first_hit() -> (
    None
):
    transport = Scripted(by("email"))
    source = apollo(transport)
    asker = person(
        linkedin=ADA_LINKEDIN, email=ADA_EMAIL, employer="Acme Corp", title="CTO"
    )
    batch = await source.fetch_raw(enrich(asker))
    assert transport.matches == [
        {"linkedin_url": ADA_LINKEDIN},
        {"email": ADA_EMAIL},
    ]  # no name lookup after the email hit
    [found] = source.normalize_checked(batch)
    assert {p.confidence for p in observed(found)} == {RUNG_CONFIDENCE["email"]}
    assert credits_in(batch) == 1  # the no-match cost nothing


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_name_and_domain_are_asked_last_with_the_documented_parameters() -> None:
    transport = Scripted(by("first_name"))
    source = apollo(transport)
    await source.fetch_raw(enrich(person(employer="Acme Corp")))
    assert transport.matches == [
        {"first_name": "Ada", "last_name": "Lovelace", "domain": ACME}
    ]


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_without_a_domain_the_company_name_is_asked() -> None:
    transport = Scripted(by("first_name"))
    source = apollo(transport)
    asker = person(domain=None, linkedin=ADA_LINKEDIN, employer="Acme Corp")
    batch = await source.fetch_raw(enrich(asker))
    assert transport.matches == [
        {"linkedin_url": ADA_LINKEDIN},
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "organization_name": "Acme Corp",
        },
    ]
    [found] = source.normalize_checked(batch)
    assert {p.confidence for p in observed(found)} == {
        RUNG_CONFIDENCE["name_organization"]
    }


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_a_known_apollo_id_is_asked_first_even_for_another_sources_record() -> (
    None
):
    # Apollo's own search record and a CRM record name one LinkedIn profile: the CRM
    # person is asked by the id, so one call serves both and nothing is paid twice.
    transport = Scripted(lambda params: answer() if "id" in params else NO_MATCH)
    source = apollo(transport)
    own = person(
        source="apollo", last="Lo***", provider_id="apollo-ada", linkedin=ADA_LINKEDIN
    )
    crm = person(linkedin=ADA_LINKEDIN + "/")
    batch = await source.fetch_raw(enrich(own, crm))
    assert transport.matches == [{"id": "apollo-ada"}]
    assert credits_in(batch) == 1
    assert {p.confidence for c in source.normalize(batch) for p in observed(c)} == {
        RUNG_CONFIDENCE["id"]
    }


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_a_masked_name_is_never_a_lookup_term() -> None:
    transport = Scripted(lambda _: NO_MATCH)
    source = apollo(transport)
    await source.fetch_raw(
        enrich(
            person(last="Lo***", employer="Acme Corp", linkedin=ADA_LINKEDIN),
            person(first="A***", last="Lovelace", employer="Acme Corp"),
        )
    )
    assert transport.matches == [{"linkedin_url": ADA_LINKEDIN}]
    for params in transport.matches:
        assert "*" not in str(params)


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_a_person_whose_answer_could_not_attach_is_not_looked_up() -> None:
    # Name and domain alone, with no title or employer to corroborate an answer: the
    # merge could never put Apollo's answer on this person, so no Credit is spent.
    transport = Scripted(by("first_name"))
    source = apollo(transport)
    with capture_logs() as logs:
        await source.fetch_raw(enrich(person()))
    assert transport.matches == []
    [event] = [e for e in logs if e["event"] == "apollo_enrich_unattachable"]
    assert event["persons"] == 1
    assert "Lovelace" not in str(logs)


# ------------------------------------------------------- confidence by rung


def test_a_weaker_rung_maps_to_a_lower_field_confidence() -> None:
    strong = [RUNG_CONFIDENCE[r] for r in ("id", "linkedin_url", "email")]
    weak = [RUNG_CONFIDENCE[r] for r in ("name_domain", "name_organization")]
    assert min(strong) > max(weak)
    assert RUNG_CONFIDENCE["name_domain"] > RUNG_CONFIDENCE["name_organization"]


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_a_name_hit_is_heuristic_lower_confidence_and_adds_no_identity_key() -> (
    None
):
    transport = Scripted(by("first_name"))
    source = apollo(transport)
    batch = await source.fetch_raw(enrich(person(employer="Acme Corp")))
    [found] = source.normalize_checked(batch)
    for record in observed(found):
        assert record.confidence_origin is ConfidenceOrigin.HEURISTIC
        assert record.confidence == RUNG_CONFIDENCE["name_domain"]
    # A name-only match is too weak to give the person a new LinkedIn or verified
    # address: that would split the record from its requester or bridge two people.
    for path in ("person.linkedin_url", "person.email", "person.email_status"):
        assert path not in found.values
    asked = {
        p.canonical_path: p.raw_field_path
        for p in found.provenance
        if p.raw_field_path.startswith("asked.")
    }
    assert asked == {
        "person.first_name": "asked.first_name",
        "person.last_name": "asked.last_name",
        "company.domain": "asked.domain",
    }
    for record in found.provenance:
        if record.raw_field_path.startswith("asked."):
            assert record.confidence_origin is ConfidenceOrigin.NONE


# ------------------------------------------------------------- ambiguity


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_an_answer_naming_another_linkedin_profile_is_discarded() -> None:
    transport = Scripted(by("linkedin_url", answer(linkedin=OTHER_LINKEDIN)))
    source = apollo(transport)
    batch = await source.fetch_raw(enrich(person(linkedin=ADA_LINKEDIN)))
    with capture_logs() as logs:
        assert source.normalize_checked(batch) == []
    [event] = [e for e in logs if e["event"] == "apollo_match_discarded"]
    assert event["reason"] == "linkedin_mismatch"
    assert "ada-lovelace" not in str(logs)
    assert credits_in(batch) == 1  # Apollo still billed the answer
    assert len(transport.matches) == 1  # a discard ends the ladder


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_a_name_hit_that_nothing_corroborates_is_discarded() -> None:
    transport = Scripted(by("first_name", answer(title="Chef", organization="Other")))
    source = apollo(transport)
    batch = await source.fetch_raw(enrich(person(employer="Acme Corp", title="CTO")))
    with capture_logs() as logs:
        assert source.normalize_checked(batch) == []
    [event] = [e for e in logs if e["event"] == "apollo_match_discarded"]
    assert event["reason"] == "uncorroborated"


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_one_question_for_two_distinguishable_people_is_not_asked() -> None:
    transport = Scripted(by("first_name"))
    source = apollo(transport)
    twins = (
        person(linkedin=ADA_LINKEDIN, employer="Acme Corp"),
        person(linkedin=OTHER_LINKEDIN, employer="Acme Corp"),
    )
    with capture_logs() as logs:
        batch = await source.fetch_raw(enrich(*twins))
    # Each LinkedIn is asked; the shared name question is not.
    assert transport.matches == [
        {"linkedin_url": ADA_LINKEDIN},
        {"linkedin_url": OTHER_LINKEDIN},
    ]
    assert source.normalize_checked(batch) == []
    [event] = [e for e in logs if e["event"] == "apollo_match_ambiguous"]
    assert event["lookups"] == 1
    assert "Lovelace" not in str(logs)


# ----------------------------------------------------------- cache and credits


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_two_spellings_of_one_linkedin_profile_are_asked_once() -> None:
    transport = Scripted(by("linkedin_url"))
    source = apollo(transport)
    batch = await source.fetch_raw(
        enrich(
            person(linkedin="https://www.linkedin.com/in/Ada-Lovelace/"),
            person(source="web", linkedin="http://linkedin.com/in/ada-lovelace"),
        )
    )
    assert len(transport.matches) == 1  # one identity, one question
    assert credits_in(batch) == 1
    # One person, one attachment (ADR-0006, supersedes "the answer reaches both"):
    # the merge joins the other spelling to it by the normalised LinkedIn key.
    assert len(source.normalize_checked(batch)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_duplicate_people_and_a_retry_never_pay_twice() -> None:
    transport = Scripted(by("email"))
    source = apollo(transport)
    work = enrich(
        person(email=ADA_EMAIL, email_status="verified"),
        person(source="web", email=" ADA@acme.com ", email_status="verified"),
    )
    first = await source.fetch_raw(work)
    again = await source.fetch_raw(work)
    assert transport.matches == [{"email": ADA_EMAIL}]
    assert credits_in(first) == credits_in(again) == 1
    assert len(source.normalize_checked(again)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_the_no_match_log_names_no_personal_lookup_value() -> None:
    transport = Scripted(lambda _: NO_MATCH)
    source = apollo(transport)
    asker = person(linkedin=ADA_LINKEDIN, email=ADA_EMAIL)
    batch = await source.fetch_raw(enrich(asker))
    with capture_logs() as logs:
        assert source.normalize_checked(batch) == []
    events = [e for e in logs if e["event"] == "apollo_no_match"]
    assert [e["rung"] for e in events] == ["linkedin_url", "email", "name_domain"]
    assert all("lookup" not in e for e in events)
    for secret in ("ada-lovelace", ADA_EMAIL, "Lovelace", ACME):
        assert secret not in str(logs)


# ------------------------------------------------- end to end: orchestrator + merge


class _People(BaseLeadSource):
    """A stand-in Discovery source handing the run a fixed set of people."""

    name: ClassVar[str] = "crm"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, Any]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()
    people: ClassVar[tuple[LeadContribution, ...]] = ()

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        return RawBatch(source_name=self.name, payload={})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return list(type(self).people)


class _OptOuts(_People):
    """A free, Suppression-bearing Enrichment stand-in that opts out one address."""

    name: ClassVar[str] = "optouts"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.ENRICH})
    yields_suppression: ClassVar[bool] = True
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_LEAD

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        assert isinstance(request, EnrichmentRequest)
        asked = {c.values.get("person.email") for c in request.work_list}
        return RawBatch(source_name=self.name, payload={"flag": GONE in asked})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        if not raw.payload["flag"]:
            return []
        return [
            LeadContribution(
                source_name=self.name,
                values={"person.email": GONE, "opt_out": True},
                provenance=tuple(
                    FieldProvenance(
                        canonical_path=path,
                        source_name=self.name,
                        data_mode=DataMode.LIVE,
                        fetched_at=datetime.now(UTC),
                        raw_field_path=path,
                        confidence_origin=ConfidenceOrigin.NONE,
                        untrusted=False,
                    )
                    for path in ("person.email", "opt_out")
                ),
            )
        ]


RANKS = {"crm": 2, "apollo": 1, "optouts": 3}


async def run(
    transport: Scripted, *people: LeadContribution
) -> dict[str, SourceResult]:
    """The real orchestrator: stand-in Discovery, then the real Enrichment tiers."""

    class People(_People):
        pass

    People.people = people

    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        if source_class is ApolloSource:
            return ApolloSource(
                mode,
                transport=transport,
                environ=ENV,
                pacing=pacing,
                vocabulary=ONE_TERM,
            )
        return source_class(mode)

    orchestrator = IngestionOrchestrator(
        SourceRegistry([People, _OptOuts, ApolloSource]),
        resolve_mode=lambda _c, _s: ModeResolution(DataMode.LIVE, "test"),
        build_source=build,
        max_concurrent_sources=3,
        run_timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.002),
    )
    results = await orchestrator.run(SourceRequest(kind="search"))
    assert all(r.outcome.status is SourceStatus.OK for r in results)
    enrichment = {r.source_name: r for r in results if r.phase.value == "enrichment"}
    discovery = {r.source_name: r for r in results if r.phase.value == "discovery"}
    return {**discovery, **{f"{k}:enrich": v for k, v in enrichment.items()}}


def merged(results: dict[str, SourceResult]) -> list[ProjectionResult]:
    contributions = [
        c for result in results.values() for c in (result.contributions or ())
    ]
    return [
        project_lead(cluster, RANKS) for cluster in cluster_contributions(contributions)
    ]


def apollo_records(result: ProjectionResult) -> list[FieldProvenance]:
    return [
        p
        for p in result.provenance
        if p.source_name == "apollo" and not p.raw_field_path.startswith("asked.")
    ]


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_end_to_end_a_crm_person_with_linkedin_is_enriched_on_one_lead() -> None:
    transport = Scripted(by("linkedin_url"))
    results = await run(transport, person(linkedin=ADA_LINKEDIN))
    [lead] = merged(results)
    assert lead.lead is not None
    assert lead.lead.email == ADA_EMAIL
    assert lead.lead.email_status is EmailStatus.VERIFIED
    assert str(lead.lead.linkedin_url).rstrip("/") == ADA_LINKEDIN
    assert lead.contributing_sources == ("apollo", "crm")
    agreement = dict(lead.agreement)
    assert agreement["person.linkedin_url"] == 1  # the echo never corroborates
    assert {p.confidence for p in apollo_records(lead)} == {
        RUNG_CONFIDENCE["linkedin_url"]
    }


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_end_to_end_a_named_person_at_a_found_company_gets_lower_confidence() -> (
    None
):
    # A person known only by name at a company found on the web: no LinkedIn and no
    # address, so Apollo is asked by name and domain, and the answer lands on that one
    # Lead (joined by name+domain, corroborated by the employer Apollo reports).
    transport = Scripted(by("first_name"))
    web_person = person(source="crm", employer="Acme Corp")
    results = await run(transport, web_person)
    [lead] = merged(results)
    assert lead.lead is not None
    assert lead.contributing_sources == ("apollo", "crm")
    [employment] = lead.lead.employments
    assert employment.title == "CTO"  # Apollo's, the requester had none
    assert lead.lead.email is None  # a name-only hit asserts no address
    records = apollo_records(lead)
    assert records
    assert {p.confidence for p in records} == {RUNG_CONFIDENCE["name_domain"]}
    assert all(p.confidence_origin is ConfidenceOrigin.HEURISTIC for p in records)


# Verifies: specs/lead-source-adapters/requirements.md#12.3
@pytest.mark.parametrize("ambiguity", ["other_linkedin", "two_people_one_name"])
async def test_end_to_end_an_ambiguous_match_attaches_nothing(ambiguity: str) -> None:
    people: tuple[LeadContribution, ...]
    if ambiguity == "other_linkedin":
        transport = Scripted(by("linkedin_url", answer(linkedin=OTHER_LINKEDIN)))
        people = (person(linkedin=ADA_LINKEDIN),)
    else:
        transport = Scripted(by("first_name"))
        people = (
            person(linkedin=ADA_LINKEDIN, employer="Acme Corp"),
            person(linkedin=OTHER_LINKEDIN, employer="Acme Corp"),
        )
    results = await run(transport, *people)
    assert transport.matches  # Apollo was asked about these people ...
    assert results["apollo:enrich"].contributions == ()  # ... and attached nothing
    leads = merged(results)
    assert len(leads) == len(people)
    assert all(r.lead is not None and r.lead.email is None for r in leads)
    assert all("apollo" not in r.contributing_sources for r in leads)


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_end_to_end_a_retry_repays_only_the_failed_lookup() -> None:
    failed = {"once": False}

    def flaky(params: Mapping[str, object]) -> TransportResponse:
        if params.get("linkedin_url") == OTHER_LINKEDIN and not failed["once"]:
            failed["once"] = True
            return TransportResponse(status=503, headers={}, body=None)
        return answer(linkedin=str(params["linkedin_url"]))

    transport = Scripted(flaky)
    results = await run(
        transport, person(linkedin=ADA_LINKEDIN), person(linkedin=OTHER_LINKEDIN)
    )
    enriched = results["apollo:enrich"]
    # One ledger spans both phases: the free search, then the match fetch and its retry.
    assert (enriched.outcome.attempted, enriched.outcome.retries) == (3, 1)
    assert transport.matches == [
        {"linkedin_url": ADA_LINKEDIN},
        {"linkedin_url": OTHER_LINKEDIN},
        {"linkedin_url": OTHER_LINKEDIN},
    ]
    assert enriched.batch is not None
    assert credits_in(enriched.batch) == 2


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_end_to_end_a_person_pruned_by_suppression_is_never_looked_up() -> None:
    transport = Scripted(by("email"))
    await run(
        transport,
        person(email=GONE, email_status="verified"),
        person(email=ADA_EMAIL, email_status="verified"),
    )
    assert transport.matches == [{"email": ADA_EMAIL}]
