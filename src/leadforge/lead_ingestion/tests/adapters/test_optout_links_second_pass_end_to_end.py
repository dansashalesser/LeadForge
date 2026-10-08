"""End to end: opt-outs follow strong links; a second, free CRM pass (2026-10-06).

User decisions (2026-10-06):

1. An opt-out follows a person's strong identity links (LinkedIn URL, verified email),
   transitively, but never a shared or role address (info@) nor name+domain. Pat opts
   out in HubSpot by his address; a LinkedIn-only record of Pat exists; Hunter's finder
   links his address to his LinkedIn URL. Apollo's paid match is never asked about Pat.
   Quinn, a different person sharing ``info@`` with Pat, is still enriched.
2. After the forward pass, HubSpot runs once more, only for the identities first seen
   after its tier (Apollo's matches found Ada's and Grace's addresses). Its CRM fields
   and opt-outs reach those leads; nothing HubSpot was already asked is asked again.

Through ``run_ingestion`` (real orchestrator, merge, store, run report) in synthetic
mode with the four REAL adapters, each answering from its SHIPPED hand-made fixture
files routed per request (``Served``, shared with ``test_tier_feed_end_to_end.py``); a
few fields are rewritten to the person asked, named at each route. One TEST-ONLY
STAND-IN Discovery (``crm_import``) names Pat and Quinn, because no shipped Discovery
adapter yields an address (gap c).
"""

# ruff: noqa: F811
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.adapters.hunter import HunterSource
from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    LeadContribution,
    RateBucket,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import Phase, SourceStatus
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.run_report import build_run_report
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.adapters.test_synthetic_zero_sockets import (
    make_lead,
)
from leadforge.lead_ingestion.tests.adapters.test_tier_feed_end_to_end import (  # noqa: F401 - fixtures
    ADA_ID,
    GRACE_ID,
    FedGoogle,
    Served,
    calls,
    database,
    guard,
    ok,
    profile,
    shipped,
)
from leadforge.lead_ingestion.tests.socket_guard import SocketGuard
from leadforge.lead_ingestion.transport import TransportResponse

DOMAIN = "example.io"
PAT = "pat@example.io"
PAT_LINKEDIN = "http://www.linkedin.com/in/pat-example"
ROLE = "info@example.io"  # Pat's and Quinn's shared address
ADA = "ada@example.com"  # fixtures/apollo/match.json person.email
GRACE = "grace@example.com"
GRACE_LINKEDIN = "http://www.linkedin.com/in/grace-example"


def apollo_route(
    name: str, params: Mapping[str, object], body: Mapping[str, object]
) -> TransportResponse:
    if name == "search":
        return ok(shipped("apollo", "search.json"))
    if params.get("id") == ADA_ID:
        return ok(shipped("apollo", "match.json"))  # unedited: Ada's address
    asked = json.dumps({**params, **body})
    if params.get("id") == GRACE_ID or ROLE in asked:
        # The shipped match, rewritten to Grace (her address and LinkedIn URL) or to
        # Quinn (the role address he was asked by, and no LinkedIn URL).
        match = shipped("apollo", "match.json")
        person = match["person"]
        if params.get("id") == GRACE_ID:
            person.update(
                id=GRACE_ID,
                first_name="Grace",
                last_name="Example",
                email=GRACE,
                linkedin_url=GRACE_LINKEDIN,
            )
        else:
            person.update(
                id="apollo-person-9",
                first_name="Quinn",
                last_name="Other",
                email=ROLE,
                linkedin_url=None,
            )
        return ok(match)
    return ok(shipped("apollo", "no_match/match.json"))


def hubspot_route(
    name: str, _: object, body: Mapping[str, object]
) -> TransportResponse:
    if name == "deal_search":
        return ok(shipped("hubspot", "deal_search.json"))
    asked = json.dumps(body)
    # The contact's own address is rewritten to the one asked: a confirmed contact.
    for address, file in (
        (PAT, "contact_search.json"),  # opted out
        (GRACE, "contact_search.json"),  # opted out
        (ADA, "not_opted_out/contact_search.json"),
    ):
        if f'"{address}"' in asked:
            found = shipped("hubspot", file)
            found["results"][0]["properties"]["email"] = address
            return ok(found)
    return ok(shipped("hubspot", "not_found/contact_search.json"))


def hunter_route(
    name: str, params: Mapping[str, object], _: object
) -> TransportResponse:
    if name == "email_finder":
        # The shipped finder answer, rewritten to Pat: Hunter finds his address.
        assert (params.get("first_name"), params.get("domain")) == ("Pat", DOMAIN)
        found = shipped("hunter", "email_finder.json")
        found["data"].update(
            email=PAT, first_name="Pat", last_name="Example", domain=DOMAIN
        )
        return ok(found)
    assert name == "email_verifier", name
    verified = shipped("hunter", "email_verifier.json")
    verified["data"]["email"] = params["email"]  # Hunter answers for the address asked
    return ok(verified)


class FedApollo(ApolloSource):
    @classmethod
    def build_transport(cls, mode: DataMode, **_: Any) -> Any:
        return Served(cls.name, cls.endpoints, apollo_route)


class FedHubSpot(HubSpotSource):
    @classmethod
    def build_transport(cls, mode: DataMode, **_: Any) -> Any:
        return Served(cls.name, cls.endpoints, hubspot_route)


class FedHunter(HunterSource):
    @classmethod
    def build_transport(cls, mode: DataMode, **_: Any) -> Any:
        return Served(cls.name, cls.endpoints, hunter_route)


class CrmImport(BaseLeadSource):
    """TEST-ONLY STAND-IN Discovery: Pat (three records) and Quinn (gap c)."""

    name: ClassVar[str] = "crm_import"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        return RawBatch(source_name=self.name, payload={"stand_in": True})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        pat = {
            "person__first_name": "Pat",
            "person__last_name": "Example",
            "company__domain": DOMAIN,
        }
        return [
            # Pat by address: HubSpot opts this address out.
            make_lead(self.name, person__email=PAT, **pat),
            # Pat by LinkedIn URL only: Hunter's finder links it to his address.
            make_lead(self.name, person__linkedin_url=PAT_LINKEDIN, **pat),
            # Pat at the shared address, with his LinkedIn URL.
            make_lead(
                self.name,
                person__email=ROLE,
                person__email_status="verified",
                person__linkedin_url=PAT_LINKEDIN,
                **pat,
            ),
            # Quinn, another person at the same shared address.
            make_lead(
                self.name,
                person__email=ROLE,
                person__email_status="verified",
                person__first_name="Quinn",
                person__last_name="Other",
                company__domain=DOMAIN,
            ),
        ]


SOURCES: list[type[BaseLeadSource]] = [
    FedApollo,
    FedHubSpot,
    FedHunter,
    FedGoogle,
    CrmImport,
]


def hubspot_asked() -> list[str]:
    return [
        str(f["value"])
        for body in calls("hubspot", "contact_search")
        for group in body["filterGroups"]  # type: ignore[attr-defined]
        for f in group["filters"]
    ]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
# Verifies: specs/lead-source-adapters/requirements.md#8.14
# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_opt_outs_follow_links_and_hubspot_asks_again_what_was_found_later(
    database: Path, guard: SocketGuard, tmp_path: Path
) -> None:
    registry = SourceRegistry(
        SOURCES, {s.name: SourceSettings(mode=DataMode.SYNTHETIC) for s in SOURCES}
    )

    outcome = await run_ingestion(registry=registry, target_profile=profile(tmp_path))

    assert outcome.exit.exit_code == 0
    assert all(r.outcome.status is SourceStatus.OK for r in outcome.results)
    # Hunter's finder was asked about Pat's LinkedIn-only record, and found PAT.
    assert len(calls("hunter", "email_finder")) == 1
    # Its rerun (user decision 2026-10-07) verifies the addresses Apollo's matches
    # found; Pat, pruned before Apollo, is never asked again.
    assert sorted(str(c["email"]) for c in calls("hunter", "email_verifier")) == [
        ADA,
        GRACE,
        ROLE,
    ]

    # Decision 1: Apollo's paid match is never asked about Pat (his LinkedIn-only and
    # info@ records follow his opt-out); Quinn, at the same info@, still is.
    matches = calls("apollo", "match")
    assert PAT_LINKEDIN not in json.dumps(matches)
    assert "Pat" not in json.dumps(matches)
    assert sorted(json.dumps(c, sort_keys=True) for c in matches) == sorted(
        json.dumps(c, sort_keys=True)
        for c in ({"id": ADA_ID}, {"id": GRACE_ID}, {"email": ROLE})
    )

    # Decision 2: HubSpot asked Pat's and info@ in its tier, then ONLY the addresses
    # Apollo's matches found, each once; a deal search per contact found.
    assert hubspot_asked() == [PAT, ROLE, ADA, GRACE]
    assert len(calls("hubspot", "deal_search")) == 3
    hubspot = [r for r in outcome.results if r.source_name == "hubspot"]
    assert [r.phase for r in hubspot] == [Phase.ENRICHMENT, Phase.ENRICHMENT]
    assert hubspot[-1].outcome.attempted == 2  # both passes, under one source

    engine = create_store_engine(f"sqlite:///{database}")
    try:
        with Session(engine) as session:
            leads = session.scalars(sa.select(m.CanonicalLeadRow)).all()
            crm_fields = session.execute(
                sa.select(
                    m.ContributionLead.lead_identity_id,
                    m.ContributionField.canonical_path,
                    m.ContributionField.value,
                )
                .join(
                    m.SourceContribution,
                    m.SourceContribution.id == m.ContributionField.contribution_id,
                )
                .join(
                    m.ContributionLead,
                    m.ContributionLead.contribution_id
                    == m.ContributionField.contribution_id,
                )
                .where(m.SourceContribution.source_name == "hubspot")
            ).all()
            report = build_run_report(session, outcome.run_id)
    finally:
        engine.dispose()

    def lead_of(address: str, source: str) -> Any:
        [lead] = [
            lead
            for lead in leads
            if lead.email == address and source in lead.contributing_sources
        ]
        return lead

    # Quinn is enriched by Apollo and is not opted out.
    quinn = lead_of(ROLE, "apollo")
    assert not quinn.opt_out
    assert not quinn.suppressed
    # Ada: HubSpot's second-pass contact joins her lead, with its CRM fields.
    ada = lead_of(ADA, "hubspot")
    assert "apollo" in ada.contributing_sources
    assert not ada.opt_out
    on_ada = {
        path: value
        for lead_id, path, value in crm_fields
        if lead_id == ada.lead_identity_id
    }
    assert on_ada["crm.lifecycle_stage"] == "lead"
    assert on_ada["crm.contact_exists"] is True
    # Grace: HubSpot's second-pass opt-out lands on her lead.
    grace = lead_of(GRACE, "hubspot")
    assert "apollo" in grace.contributing_sources
    assert grace.opt_out
    assert grace.suppressed

    # The run report counts both passes under HubSpot.
    [hubspot_counts] = [s for s in report.sources if s.source_name == "hubspot"]
    assert hubspot_counts.attempted == 2
    assert hubspot_counts.leads_normalized == sum(
        len(r.contributions or ()) for r in hubspot
    )
