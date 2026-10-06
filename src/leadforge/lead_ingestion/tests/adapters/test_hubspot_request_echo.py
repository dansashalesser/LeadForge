"""HubSpot's contact joins the person it was asked about (user option B, 2026-10-06).

User decision (2026-10-06, option B): HubSpot's contact record must join the right
person's lead without changing merge rules. Each contribution answering a lookup
echoes the requester's identity (its LinkedIn URL and/or verified address) under
``asked.*`` (``REQUEST_ECHO_PREFIX``), as Hunter and Apollo do; an echo never
corroborates (``conflicts._observed_first``). A contact that is not the asked person
(another address), several contacts for one ask, or one ask made for two
distinguishable people carry no echo, so they stay unattached.

Proved on the adapter, through the real orchestrator + clustering + projection, and
through ``run_ingestion`` with the real HubSpot and Apollo adapters on their shipped
fixtures (a test-only Discovery stand-in names the people, as no shipped Discovery
adapter yields an address).
"""

import json
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    EnrichmentRequest,
    LeadContribution,
    RateBucket,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.clustering import cluster_contributions
from leadforge.lead_ingestion.compliance import blocked_identities
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import ConfidenceOrigin, DataMode
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    SourceStatus,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.projection import ProjectionResult, project_lead
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.adapters.test_synthetic_zero_sockets import (
    make_lead,
)
from leadforge.lead_ingestion.tests.socket_guard import SocketGuard, guard_for_mode
from leadforge.lead_ingestion.transport import FixtureTransport, TransportResponse

CONTACT_PATH = "/crm/objects/{version}/contacts/search"
ENV = {"HUBSPOT_ACCESS_TOKEN": "pat-test-not-real", "HUBSPOT_API_VERSION": "2027-03"}
APOLLO_ENV = {"APOLLO_API_KEY": "test-key-not-real"}
FIXTURES = FixtureTransport("hubspot", {}).fixtures_root

ADA = "ada@acme.com"
ADA_LINKEDIN = "https://www.linkedin.com/in/ada-lovelace"
OTHER_LINKEDIN = "https://www.linkedin.com/in/someone-else"
HANA = "hana@example.net"
HANA_LINKEDIN = "https://www.linkedin.com/in/hana-example"
KIM = "kim@example.org"
LEE = "lee@example.com"
LEE_LINKEDIN = "https://www.linkedin.com/in/lee-example"


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a test reached for the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)


def ok(body: object) -> TransportResponse:
    return TransportResponse(status=200, headers={}, body=body)


def shipped(provider: str, file: str) -> dict[str, Any]:
    loaded = json.loads((FIXTURES / provider / file).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def contact(email: str | None, contact_id: str = "501", optout: str = "false") -> Any:
    """The shipped contact, rewritten to ``email`` (absent when None)."""
    body = shipped("hubspot", "not_opted_out/contact_search.json")
    [found] = body["results"]
    found["id"] = contact_id
    found["properties"]["hs_email_optout"] = optout
    if email is None:
        del found["properties"]["email"]
    else:
        found["properties"]["email"] = email
    return found


class Contacts:
    """Answers every contact search with ``found`` (callable on the asked address)."""

    def __init__(self, found: Any) -> None:
        self.found = found
        self.bodies: list[Mapping[str, object]] = []

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        body = json_body or {}
        if endpoint.path != CONTACT_PATH:
            return ok({"total": 1, "results": []})
        self.bodies.append(body)
        groups: Any = body["filterGroups"]
        [asked] = groups[0]["filters"]
        results = self.found(asked["value"]) if callable(self.found) else self.found
        return ok({"total": len(results), "results": results})


def hubspot(transport: Contacts) -> HubSpotSource:
    return HubSpotSource(DataMode.LIVE, transport=transport, environ=ENV)


def requester(**values: object) -> LeadContribution:
    return make_lead("crm", **values)


async def answer(
    transport: Contacts, *people: LeadContribution
) -> list[LeadContribution]:
    source = hubspot(transport)
    request = EnrichmentRequest(kind="enrich", work_list=people)
    return source.normalize_checked(await source.fetch_raw(request))


def echoed(contribution: LeadContribution) -> dict[str, object]:
    paths = {
        p.canonical_path
        for p in contribution.provenance
        if p.raw_field_path.startswith("asked.")
    }
    return {path: contribution.values[path] for path in paths}


# ------------------------------------------------------------------ the adapter


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_a_found_contact_echoes_the_requesters_linkedin_url_and_verified_email() -> (  # noqa: E501
    None
):
    transport = Contacts([contact(ADA)])
    [found] = await answer(
        transport,
        requester(
            person__email=ADA,
            person__email_status="verified",
            person__linkedin_url=ADA_LINKEDIN,
        ),
    )
    assert echoed(found) == {
        "person.linkedin_url": ADA_LINKEDIN,
        "person.email": ADA,
        "person.email_status": "verified",
    }
    echo_records = [
        p for p in found.provenance if p.raw_field_path.startswith("asked.")
    ]
    assert all(p.confidence_origin is ConfidenceOrigin.NONE for p in echo_records)
    # HubSpot's own observations are kept as they were.
    assert found.values["email"] == ADA
    assert found.values["crm.contact_exists"] is True
    assert found.values["crm.lifecycle_stage"] == "lead"
    assert found.values["opt_out"] is False


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_an_unverified_requester_address_is_not_echoed_as_a_key() -> None:
    transport = Contacts([contact(ADA)])
    [found] = await answer(
        transport,
        requester(person__email=ADA, person__linkedin_url=ADA_LINKEDIN),
    )
    assert echoed(found) == {"person.linkedin_url": ADA_LINKEDIN}


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_a_requester_with_no_linkedin_and_no_verified_address_gets_no_echo() -> (
    None
):
    transport = Contacts([contact(ADA)])
    [found] = await answer(transport, requester(person__email=ADA))
    assert echoed(found) == {}


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_the_contact_search_asks_for_the_contacts_own_address() -> None:
    transport = Contacts([contact(ADA)])
    await answer(transport, requester(person__email=ADA))
    [body] = transport.bodies
    assert "email" in body["properties"]  # type: ignore[operator]


# Verifies: specs/lead-source-adapters/requirements.md#13.1
@pytest.mark.parametrize(
    ("returned", "reason"),
    [("someone@else.com", "email_mismatch"), (None, "email_unconfirmed")],
)
async def test_a_contact_that_is_not_the_asked_address_carries_no_echo(
    returned: str | None, reason: str
) -> None:
    transport = Contacts([contact(returned)])
    with capture_logs() as logs:
        [found] = await answer(
            transport,
            requester(
                person__email=ADA,
                person__email_status="verified",
                person__linkedin_url=ADA_LINKEDIN,
            ),
        )
    assert echoed(found) == {}
    assert found.values["crm.contact_exists"] is True  # still HubSpot's answer
    withheld = [e for e in logs if e["event"] == "hubspot_echo_withheld"]
    assert [e["reason"] for e in withheld] == [reason]
    assert ADA not in json.dumps(withheld, default=str)  # no personal value logged


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_several_contacts_for_one_ask_are_ambiguous_and_carry_no_echo() -> None:
    transport = Contacts([contact(ADA, "501"), contact(ADA, "502", optout="true")])
    with capture_logs() as logs:
        found = await answer(
            transport,
            requester(person__email=ADA, person__linkedin_url=ADA_LINKEDIN),
        )
    assert len(found) == 2
    assert all(echoed(c) == {} for c in found)
    assert any(c.values["opt_out"] is True for c in found)  # the flag is still there
    withheld = [e for e in logs if e["event"] == "hubspot_echo_withheld"]
    assert [e["reason"] for e in withheld] == ["multiple_contacts"]


# Verifies: specs/lead-source-adapters/requirements.md#13.1
@pytest.mark.parametrize("ambiguity", ["two_linkedin_profiles", "two_names"])
async def test_one_ask_made_for_two_distinguishable_people_carries_no_echo(
    ambiguity: str,
) -> None:
    if ambiguity == "two_linkedin_profiles":
        people = (
            requester(person__email=ADA, person__linkedin_url=ADA_LINKEDIN),
            requester(person__email=ADA, person__linkedin_url=OTHER_LINKEDIN),
        )
    else:
        people = (
            requester(
                person__email=ADA,
                person__email_status="verified",
                person__first_name="Ada",
                person__last_name="Lovelace",
            ),
            requester(
                person__email=ADA, person__first_name="Bob", person__last_name="Ng"
            ),
        )
    transport = Contacts([contact(ADA)])
    with capture_logs() as logs:
        [found] = await answer(transport, *people)
    assert echoed(found) == {}
    withheld = [e for e in logs if e["event"] == "hubspot_echo_withheld"]
    assert [e["reason"] for e in withheld] == ["ambiguous_requester"]


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_an_ask_for_two_people_across_fetches_echoes_neither_later() -> None:
    transport = Contacts([contact(ADA)])
    source = hubspot(transport)
    for url in (ADA_LINKEDIN, OTHER_LINKEDIN):
        request = EnrichmentRequest(
            kind="enrich",
            work_list=(requester(person__email=ADA, person__linkedin_url=url),),
        )
        batch = await source.fetch_raw(request)
    [found] = source.normalize_checked(batch)
    assert echoed(found) == {}
    assert len(transport.bodies) == 1  # the lookup itself is still cached


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_the_echo_is_recorded_in_the_raw_batch_and_an_older_batch_has_none() -> (
    None
):
    transport = Contacts([contact(ADA)])
    source = hubspot(transport)
    request = EnrichmentRequest(
        kind="enrich",
        work_list=(requester(person__email=ADA, person__linkedin_url=ADA_LINKEDIN),),
    )
    batch = await source.fetch_raw(request)
    [entry] = batch.payload["lookups"]
    assert entry["asked"] == {"linkedin_url": ADA_LINKEDIN}
    older = RawBatch(
        source_name="hubspot",
        payload={"lookups": [{k: v for k, v in entry.items() if k != "asked"}]},
    )
    [found] = source.normalize_checked(older)
    assert echoed(found) == {}


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_an_unknown_address_carries_no_echo() -> None:
    # Its ``email`` is the question, not an address HubSpot holds: joining it would
    # let the question count as agreement on the requester's address.
    transport = Contacts([])
    [found] = await answer(
        transport, requester(person__email=ADA, person__linkedin_url=ADA_LINKEDIN)
    )
    assert echoed(found) == {}
    assert found.values == {"email": ADA}


# Verifies: specs/lead-source-adapters/requirements.md#13.1
@pytest.mark.parametrize(
    ("returned", "reason"),
    [
        ("Ada@ACME.com", None),
        ("ada@acme.com", None),
        ("straße@acme.com", "email_mismatch"),
    ],
)
async def test_the_contacts_own_address_is_compared_as_the_email_match_key_is(
    returned: str, reason: str | None
) -> None:
    # The email Match Key lower()s, never casefold()s: "ß" and "ss" are two mailboxes.
    asked = "strasse@acme.com" if "ß" in returned else ADA
    transport = Contacts([contact(returned)])
    with capture_logs() as logs:
        [found] = await answer(
            transport,
            requester(person__email=asked, person__email_status="verified"),
        )
    withheld = [e["reason"] for e in logs if e["event"] == "hubspot_echo_withheld"]
    assert withheld == ([reason] if reason else [])
    assert echoed(found) == (
        {} if reason else {"person.email": asked, "person.email_status": "verified"}
    )


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_hubspot_is_asked_the_requesters_mailbox_as_the_match_key_reads_it() -> (
    None
):
    transport = Contacts([contact("straße@acme.com")])
    [found] = await answer(
        transport,
        requester(person__email=" Straße@ACME.com ", person__email_status="verified"),
    )
    [body] = transport.bodies
    [[asked]] = [group["filters"] for group in body["filterGroups"]]  # type: ignore[attr-defined]
    assert asked["value"] == "straße@acme.com"
    assert echoed(found) == {
        "person.email": "straße@acme.com",
        "person.email_status": "verified",
    }


# Verifies: specs/lead-source-adapters/requirements.md#13.1
def test_hubspot_hunter_and_apollo_echo_through_one_shared_rule_table() -> None:
    # One definition of "asked.<key> -> canonical path" (no per-adapter copies).
    from leadforge.lead_ingestion.adapters import apollo, hubspot, hunter
    from leadforge.lead_ingestion.normalizer import (
        REQUEST_ECHO_KEY,
        REQUEST_ECHO_RULES,
    )

    assert REQUEST_ECHO_KEY == "asked"
    assert {key: rule.raw_field_path for key, rule in REQUEST_ECHO_RULES.items()} == {
        key: f"asked.{key}"
        for key in (
            "linkedin_url",
            "email",
            "email_status",
            "first_name",
            "last_name",
            "domain",
        )
    }
    shared = {id(rule) for rule in REQUEST_ECHO_RULES.values()}  # the same objects
    assert {id(rule) for rule in hubspot._ECHO_RULES.values()} <= shared
    assert {id(rule) for rule in hunter.HunterSource.ASKED_RULES} <= shared
    assert frozenset(REQUEST_ECHO_RULES) == apollo._ASKED_KEYS
    untrusted = {k for k, rule in REQUEST_ECHO_RULES.items() if rule.untrusted}
    assert untrusted == {"first_name", "last_name"}


# ------------------------------------- end to end: orchestrator + clustering + merge


class _Discovery(BaseLeadSource):
    """TEST-ONLY STAND-IN Discovery handing the run a fixed set of people."""

    name: ClassVar[str] = "crm"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()
    people: ClassVar[tuple[LeadContribution, ...]] = ()

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        return RawBatch(source_name=self.name, payload={"stand_in": True})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return list(type(self).people)


class _ApolloMatches:
    """Apollo: no search results; a match for Ada's or Hana's LinkedIn URL only."""

    known: ClassVar[Mapping[str, tuple[str, str, str]]] = {
        ADA_LINKEDIN: ("Ada", "Lovelace", ADA),
        HANA_LINKEDIN: ("Hana", "Example", HANA),
    }

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        asked = dict(params or {})
        if "page" in asked:
            return ok({"total_entries": 0, "people": []})
        url = str(asked.get("linkedin_url"))
        if url not in self.known:
            return ok({"match_confidence": "none"})
        first, last, address = self.known[url]
        body = shipped("apollo", "match.json")
        body["person"].update(
            id=f"apollo-{first.lower()}",
            first_name=first,
            last_name=last,
            email=address,
            email_status="verified",
            linkedin_url=url,
        )
        return ok(body)


RANKS = {"crm": 2, "apollo": 1, "hubspot": 3}


async def merged_run(
    contacts: Contacts, *people: LeadContribution
) -> list[ProjectionResult]:
    """The real orchestrator over the real adapters, then the real merge."""

    class People(_Discovery):
        pass

    People.people = people

    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        if source_class is HubSpotSource:
            return HubSpotSource(mode, transport=contacts, environ=ENV, pacing=pacing)
        if source_class is ApolloSource:
            return ApolloSource(
                mode,
                transport=_ApolloMatches(),
                environ=APOLLO_ENV,
                pacing=pacing,
                vocabulary={"datastax": ["datastax"]},
            )
        return source_class(mode)

    orchestrator = IngestionOrchestrator(
        SourceRegistry([People, HubSpotSource, ApolloSource]),
        resolve_mode=lambda _c, _s: ModeResolution(DataMode.LIVE, "test"),
        build_source=build,
        max_concurrent_sources=3,
        run_timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=2, base_delay_s=0.001, max_delay_s=0.002),
    )
    results = await orchestrator.run(SourceRequest(kind="search"))
    assert all(r.outcome.status is SourceStatus.OK for r in results)
    contributions = [c for r in results for c in (r.contributions or ())]
    blocked = blocked_identities(contributions)
    return [
        project_lead(cluster, RANKS, blocked=blocked)
        for cluster in cluster_contributions(contributions)
    ]


def with_source(leads: list[ProjectionResult], name: str) -> list[ProjectionResult]:
    return [lead for lead in leads if name in lead.contributing_sources]


def hubspot_paths(lead: ProjectionResult) -> set[str]:
    return {
        p.canonical_path
        for p in lead.provenance
        if p.source_name == "hubspot" and not p.raw_field_path.startswith("asked.")
    }


# Verifies: specs/lead-source-adapters/requirements.md#13.1
# Verifies: specs/lead-source-adapters/requirements.md#8.2
async def test_end_to_end_a_linkedin_person_apollo_knows_and_hubspot_has_is_one_lead() -> (  # noqa: E501
    None
):
    contacts = Contacts([contact(ADA)])
    leads = await merged_run(
        contacts,
        requester(
            person__email=ADA,
            person__first_name="Ada",
            person__last_name="Lovelace",
            person__linkedin_url=ADA_LINKEDIN,
        ),
    )
    [lead] = leads
    assert lead.lead is not None
    assert set(lead.contributing_sources) == {"apollo", "crm", "hubspot"}
    assert {
        "crm.contact_exists",
        "crm.lifecycle_stage",
        "crm.owner",
        "crm.last_activity_date",
        "crm.has_open_deal",
        "opt_out",
    } <= hubspot_paths(lead)
    agreement = dict(lead.agreement)
    # Only crm observed the URL: Apollo's and HubSpot's are echoes, never agreement.
    assert agreement["person.linkedin_url"] == 1
    # crm, Apollo and HubSpot's confirmed contact each hold the address.
    assert agreement["person.email"] == 3
    assert lead.opt_out is False
    assert lead.suppressed is False


# Verifies: specs/lead-source-adapters/requirements.md#13.1
# Verifies: specs/lead-source-adapters/requirements.md#11.4
async def test_end_to_end_an_opted_out_contact_joins_and_suppresses_the_person() -> (
    None
):
    # A person known by a verified address (as Hunter verifies one); HubSpot's
    # opted-out contact for that address joins her lead and suppresses it.
    contacts = Contacts([contact(ADA, optout="true")])
    leads = await merged_run(
        contacts,
        requester(person__email=ADA, person__email_status="verified"),
    )
    [lead] = leads
    assert set(lead.contributing_sources) == {"crm", "hubspot"}
    assert lead.opt_out is True
    assert lead.suppressed is True
    assert "hubspot" in lead.compliance_sources
    # crm and HubSpot's own confirmed address: the echoed address adds nothing and
    # no longer shadows HubSpot's observation (projection rules revision 3).
    assert dict(lead.agreement)["person.email"] == 2


# Verifies: specs/lead-source-adapters/requirements.md#13.1
@pytest.mark.parametrize("answer_kind", ["other_address", "two_contacts"])
async def test_end_to_end_a_mismatched_or_ambiguous_answer_stays_unattached(
    answer_kind: str,
) -> None:
    found = (
        [contact("someone@else.com")]
        if answer_kind == "other_address"
        else [contact(ADA, "501"), contact(ADA, "502")]
    )
    leads = await merged_run(
        Contacts(found),
        requester(
            person__email=ADA,
            person__first_name="Ada",
            person__last_name="Lovelace",
            person__linkedin_url=ADA_LINKEDIN,
        ),
    )
    [person] = with_source(leads, "crm")
    assert "hubspot" not in person.contributing_sources
    assert "apollo" in person.contributing_sources
    loose = [lead for lead in leads if lead.contributing_sources == ("hubspot",)]
    assert len(loose) == len(found)


# ---------------------------------------------- end to end: run_ingestion + store


def hubspot_route(asked: str) -> list[Any]:
    """Hana: her contact; Kim: her contact, opted out; Lee: two contacts."""
    if asked == HANA:
        return [contact(HANA, "601")]
    if asked == KIM:
        return [contact(KIM, "602", optout="true")]
    return [contact(LEE, "603"), contact(LEE, "604")]


class RunHubSpot(HubSpotSource):
    @classmethod
    def build_transport(cls, mode: DataMode, **_: Any) -> Any:
        return Contacts(hubspot_route)


class RunApollo(ApolloSource):
    @classmethod
    def build_transport(cls, mode: DataMode, **_: Any) -> Any:
        return _ApolloMatches()


class CrmImport(_Discovery):
    name: ClassVar[str] = "crm_import"
    people: ClassVar[tuple[LeadContribution, ...]] = (
        make_lead(
            "crm_import",
            person__email=HANA,
            person__first_name="Hana",
            person__last_name="Example",
            person__linkedin_url=HANA_LINKEDIN,
        ),
        make_lead("crm_import", person__email=KIM, person__email_status="verified"),
        make_lead(
            "crm_import",
            person__email=LEE,
            person__first_name="Lee",
            person__last_name="Example",
            person__linkedin_url=LEE_LINKEDIN,
        ),
    )


SOURCES: list[type[BaseLeadSource]] = [RunApollo, RunHubSpot, CrmImport]


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for source in SOURCES:
        for variable in source.required_env:
            monkeypatch.delenv(variable, raising=False)
    for variable in ("LEADFORGE_MODE", "LEADFORGE_ENV_FILE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "store.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    return path


@pytest.fixture
def guard(database: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    blocked: SocketGuard = guard_for_mode(DataMode.SYNTHETIC)
    blocked.install(monkeypatch)
    yield blocked
    blocked.assert_clean()


# Verifies: specs/lead-source-adapters/requirements.md#13.1
# Verifies: specs/lead-source-adapters/requirements.md#11.4
async def test_run_ingestion_joins_hubspot_contacts_to_the_asked_people(
    database: Path, guard: SocketGuard, tmp_path: Path
) -> None:
    registry = SourceRegistry(
        SOURCES, {s.name: SourceSettings(mode=DataMode.SYNTHETIC) for s in SOURCES}
    )
    target = tmp_path / "target_profile.yaml"
    target.write_text("technologies:\n  datastax:\n    apollo: [datastax]\n")

    outcome = await run_ingestion(registry=registry, target_profile_path=target)

    assert outcome.exit.exit_code == 0
    engine = create_store_engine(f"sqlite:///{database}")
    try:
        with Session(engine) as session:
            leads = session.scalars(sa.select(m.CanonicalLeadRow)).all()
    finally:
        engine.dispose()

    def holding(address: str) -> list[Any]:
        return [
            lead
            for lead in leads
            if "crm_import" in lead.contributing_sources and lead.email == address
        ]

    [hana] = holding(HANA)  # LinkedIn echo: Discovery + HubSpot + Apollo, one lead
    assert set(hana.contributing_sources) == {"crm_import", "hubspot", "apollo"}
    [kim] = holding(KIM)  # verified-address echo: her opt-out is on her lead
    assert set(kim.contributing_sources) == {"crm_import", "hubspot"}
    assert kim.suppressed
    [lee] = holding(LEE)  # two contacts: ambiguous, nothing attached
    assert "hubspot" not in lee.contributing_sources
    hubspot_only = [lead for lead in leads if lead.contributing_sources == ["hubspot"]]
    # The two contacts give identical observations: stored once, one lead (0006).
    assert len(hubspot_only) == 1
    assert {lead.email for lead in hubspot_only} == {LEE}
