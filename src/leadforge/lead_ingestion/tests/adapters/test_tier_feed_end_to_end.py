"""End to end: Enrichment tiers feed later tiers (ADR-0006; follow-up 2026-10-06).

User decisions (2026-10-06): sources enhance each other; HubSpot and Hunter "have
information we can cross reference or append to the lead"; a Lead is a person; Hunter
runs before Apollo, so a Hunter 451 prunes the person before Apollo's paid match.

Through ``run_ingestion`` (real orchestrator, merge, store, run report) in synthetic
mode, with the four REAL adapters. Each answers from its SHIPPED hand-made fixture
files, routed per request (``Served``): which file serves a request depends on who is
asked, and a few fields are rewritten to the person asked (named at each route). One
TEST-ONLY STAND-IN Discovery (``crm_import``) names two people by address, because no
shipped Discovery adapter yields an address (gap c), and HubSpot and Hunter look
people up by it. Apollo's own search is the other Discovery.

The chain proved: Apollo search -> HubSpot -> Hunter -> Apollo match -> Google. Google
is asked about the company domain only Apollo's match found, and its evidence attaches;
the person HubSpot knows is enriched by Apollo on ONE lead; the person Hunter answers
451 for is never asked of Apollo or Google; calls per source are counted exactly.
"""

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
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
    enrichment_tiers,
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
from leadforge.lead_ingestion.tests.socket_guard import SocketGuard, guard_for_mode
from leadforge.lead_ingestion.transport import FixtureTransport, TransportResponse

FIXTURES = FixtureTransport("apollo", {}).fixtures_root
# Ada: found by Apollo's search; only Apollo's match names her company's domain.
ADA_ID = "apollo-person-1"
ADA_COMPANY = "example.com"  # fixtures/apollo/match.json organization.primary_domain
GRACE_ID = "apollo-person-2"
# Hana: the person HubSpot knows (by address).
HANA = "hana@example.net"
HANA_COMPANY = "example.net"
HANA_LINKEDIN = "http://www.linkedin.com/in/hana-example"
# Sam: Hunter answers 451 for his address.
SAM = "sam@example.org"
SAM_COMPANY = "example.org"
TERM = "DataStax"


Route = Callable[[str, Mapping[str, object], Mapping[str, object]], TransportResponse]


class Served:
    """Serves shipped fixture files per request and records every call made."""

    calls: ClassVar[list[tuple[str, str, dict[str, object]]]] = []

    def __init__(self, provider: str, endpoints: Mapping[str, Endpoint], route: Route):
        self._provider = provider
        self._names = {endpoint: name for name, endpoint in endpoints.items()}
        self._route = route

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        name = self._names[endpoint]
        asked = dict(params or {})
        Served.calls.append((self._provider, name, {**asked, **(json_body or {})}))
        return self._route(name, asked, json_body or {})


def shipped(provider: str, file: str) -> dict[str, Any]:
    loaded = json.loads((FIXTURES / provider / file).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def ok(body: object) -> TransportResponse:
    return TransportResponse(status=200, headers={}, body=body)


def apollo_route(
    name: str, params: Mapping[str, object], _: object
) -> TransportResponse:
    if name == "search":
        return ok(shipped("apollo", "search.json"))
    if params.get("id") == ADA_ID:
        return ok(shipped("apollo", "match.json"))  # unedited: primary_domain shipped
    if params.get("linkedin_url") == HANA_LINKEDIN:
        # The shipped match, rewritten to Hana: her name, address and company.
        body = shipped("apollo", "match.json")
        person = body["person"]
        person.update(
            id="apollo-person-3",
            first_name="Hana",
            last_name="Example",
            email=HANA,
            linkedin_url=HANA_LINKEDIN,
        )
        person["organization"] = {
            **person["organization"],
            "name": "Example Net Co",
            "primary_domain": HANA_COMPANY,
        }
        return ok(body)
    return ok(shipped("apollo", "no_match/match.json"))


def hubspot_route(
    name: str, _: object, body: Mapping[str, object]
) -> TransportResponse:
    if name == "deal_search":
        return ok(shipped("hubspot", "deal_search.json"))
    if HANA in json.dumps(body):
        return ok(shipped("hubspot", "not_opted_out/contact_search.json"))
    return ok(shipped("hubspot", "not_found/contact_search.json"))


def hunter_route(
    name: str, params: Mapping[str, object], _: object
) -> TransportResponse:
    assert name == "email_verifier", name  # nobody here is on the finder route
    if params.get("email") == SAM:
        return TransportResponse(451, {}, {"errors": [{"details": "claimed_email"}]})
    body = shipped("hunter", "email_verifier.json")
    body["data"]["email"] = params["email"]  # Hunter answers for the address asked
    return ok(body)


def google_route(
    name: str, params: Mapping[str, object], _: object
) -> TransportResponse:
    if params.get("q") == f'"{ADA_COMPANY}" {TERM}':
        # The shipped page, with its first result moved onto the company's own site.
        body = shipped("google_search", "search.json")
        body["organic_results"][0]["link"] = f"https://www.{ADA_COMPANY}/datastax"
        return ok(body)
    return ok(shipped("google_search", "no_results/search.json"))


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


class FedGoogle(GoogleSearchSource):
    @classmethod
    def build_transport(cls, mode: DataMode, **_: Any) -> Any:
        return Served(cls.name, cls.endpoints, google_route)


class CrmImport(BaseLeadSource):
    """TEST-ONLY STAND-IN Discovery: two people named by address (gap c)."""

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
        return [
            make_lead(
                self.name,
                person__email=HANA,
                person__first_name="Hana",
                person__last_name="Example",
                person__linkedin_url=HANA_LINKEDIN,
                company__domain=HANA_COMPANY,
            ),
            make_lead(
                self.name,
                person__email=SAM,
                person__first_name="Sam",
                person__last_name="Gone",
                company__domain=SAM_COMPANY,
            ),
        ]


SOURCES: list[type[BaseLeadSource]] = [
    FedApollo,
    FedHubSpot,
    FedHunter,
    FedGoogle,
    CrmImport,
]


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
    Served.calls = []
    return path


@pytest.fixture
def guard(database: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    blocked: SocketGuard = guard_for_mode(DataMode.SYNTHETIC)
    blocked.install(monkeypatch)
    yield blocked
    blocked.assert_clean()


def profile(tmp_path: Path) -> Path:
    path = tmp_path / "target_profile.yaml"
    path.write_text(
        f"technologies:\n  datastax:\n    apollo: [datastax]\n"
        f'    google_search:\n      - "{TERM}"\n'
    )
    return path


def calls(provider: str, name: str | None = None) -> list[dict[str, object]]:
    return [
        asked
        for p, n, asked in Served.calls
        if p == provider and (name is None or n == name)
    ]


# Verifies: specs/lead-source-adapters/requirements.md#6.9
def test_the_shipped_adapters_run_hubspot_then_hunter_then_apollo_then_google() -> None:
    shipped_sources: list[type[BaseLeadSource]] = [
        GoogleSearchSource,
        ApolloSource,
        HunterSource,
        HubSpotSource,
    ]
    tiers = [
        [s.name for s in tier]
        for tier in enrichment_tiers(shipped_sources)  # type: ignore[type-var]
    ]
    assert tiers == [["hubspot"], ["hunter"], ["apollo"], ["google_search"]]


# Verifies: specs/lead-source-adapters/requirements.md#6.9
# Verifies: specs/lead-source-adapters/requirements.md#6.10
# Verifies: specs/lead-source-adapters/requirements.md#14.5
# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_each_tier_feeds_the_next_through_a_real_run(
    database: Path, guard: SocketGuard, tmp_path: Path
) -> None:
    registry = SourceRegistry(
        SOURCES, {s.name: SourceSettings(mode=DataMode.SYNTHETIC) for s in SOURCES}
    )

    outcome = await run_ingestion(
        registry=registry, target_profile_path=profile(tmp_path)
    )

    assert outcome.exit.exit_code == 0
    assert all(r.outcome.status is SourceStatus.OK for r in outcome.results)

    # Google IS asked about the company domain only Apollo's match found, and Hana's
    # (Discovery's); never Sam's, pruned by Hunter's 451.
    assert sorted(str(c["q"]) for c in calls("google_search")) == [
        f'"{ADA_COMPANY}" {TERM}',
        f'"{HANA_COMPANY}" {TERM}',
    ]
    # Apollo: one free search, then one paid match per person, none for Sam.
    assert len(calls("apollo", "search")) == 1
    matches = calls("apollo", "match")
    assert sorted(json.dumps(c, sort_keys=True) for c in matches) == sorted(
        json.dumps(c, sort_keys=True)
        for c in ({"id": ADA_ID}, {"id": GRACE_ID}, {"linkedin_url": HANA_LINKEDIN})
    )
    assert SAM not in json.dumps(matches)
    # Hunter verifies each address once; HubSpot asks each address, and deals once.
    assert sorted(str(c["email"]) for c in calls("hunter")) == [HANA, SAM]
    assert len(calls("hubspot", "contact_search")) == 2
    assert len(calls("hubspot", "deal_search")) == 1
    assert len(Served.calls) == 1 + 3 + 2 + 3 + 2

    engine = create_store_engine(f"sqlite:///{database}")
    try:
        with Session(engine) as session:
            leads = session.scalars(sa.select(m.CanonicalLeadRow)).all()
            evidence = session.execute(
                sa.select(
                    m.ContributionField.contribution_id,
                    m.ContributionField.canonical_path,
                    m.ContributionField.value,
                )
                .join(
                    m.SourceContribution,
                    m.SourceContribution.id == m.ContributionField.contribution_id,
                )
                .where(m.SourceContribution.source_name == "google_search")
            ).all()
            report = build_run_report(session, outcome.run_id)
    finally:
        engine.dispose()

    # Google's evidence attaches to the company Apollo found (its own site).
    by_record: dict[Any, dict[str, Any]] = {}
    for contribution_id, path, value in evidence:
        by_record.setdefault(contribution_id, {})[path] = value
    attached = [e for e in by_record.values() if e.get("company.domain") == ADA_COMPANY]
    assert attached
    assert {e["company.web_evidence.attachment"] for e in attached} == {"own_domain"}

    # Hana, whom HubSpot knows: Apollo is asked about her once although three records
    # name her (Discovery's, HubSpot's, Hunter's), and its match lands on ONE lead,
    # the one holding her Discovery record and Hunter's verdict.
    with_apollo = [
        lead
        for lead in leads
        if lead.email == HANA and "apollo" in lead.contributing_sources
    ]
    [hana] = with_apollo
    assert {"crm_import", "hunter", "apollo"} <= set(hana.contributing_sources)
    assert hana.linkedin_url is not None  # appended by Apollo's match
    # HubSpot's record stays a lead of its own: the shipped contact's own address is
    # not hers, so it carries no request echo (option B, 2026-10-06; a confirmed
    # contact joins: test_hubspot_request_echo.py), and its unverified address is no
    # Match Key (8.2).
    hubspot_only = [lead for lead in leads if lead.contributing_sources == ["hubspot"]]
    assert {lead.email for lead in hubspot_only} == {HANA, SAM}
    # Every lead holding Sam's address is suppressed; Apollo contributed to none.
    sams = [lead for lead in leads if lead.email == SAM]
    assert sams
    assert all(lead.suppressed for lead in sams)
    assert not any("apollo" in lead.contributing_sources for lead in sams)

    # The report's per-source counts are the run's own contribution counts.
    produced: dict[str, int] = {}
    for result in outcome.results:
        produced[result.source_name] = produced.get(result.source_name, 0) + len(
            result.contributions or ()
        )
    assert {s.source_name: s.leads_normalized for s in report.sources} == produced
    enrichment = {r.source_name for r in outcome.results if r.phase is Phase.ENRICHMENT}
    assert enrichment == {"hubspot", "hunter", "apollo", "google_search"}
    [google] = [s for s in report.sources if s.source_name == "google_search"]
    assert google.web_evidence == (len(attached), len(by_record) - len(attached))
