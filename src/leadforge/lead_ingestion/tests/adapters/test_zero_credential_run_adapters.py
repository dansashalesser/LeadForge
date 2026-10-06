"""What the zero-credential run does with the four shipped adapters (task 20).

The vendor-specific half of tests/test_end_to_end_zero_credential.py. It states what the
run PRODUCES from the real adapters over their hand-made fixtures, including what it
cannot yet:

* GAP (c): no real Discovery adapter yields an email, and HubSpot and Hunter look a
  lead up by email (or company domain). So in a real run both are invoked and succeed,
  make no provider call and contribute nothing. Pinned below by ``..._gap_c``; the tests
  that show their fixtures flowing through the merge use a scripted Discovery STAND-IN
  and say so in their names.
* HubSpot's ``datetime`` and Hunter's ``tuple`` are stored as ISO-8601 UTC text and a
  JSON array (the store once refused both; pinned below, through the STAND-IN).
* Apollo's enrichment fixture yields a person with an email and a LinkedIn URL, so the
  real run persists at least one canonical Lead without any stand-in.
* GAP (d), since the 14.2 completion (option C): Google asks only queries anchored to
  a discovered company's domain. No shipped Discovery adapter yields a domain, so in a
  real run Google is invoked in both phases, makes no call and contributes nothing.
  Attachment through a real run is shown with a stand-in in
  ``test_google_search_web_evidence_run.py``.
"""

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session

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
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.adapters.test_synthetic_zero_sockets import (
    make_lead,
)
from leadforge.lead_ingestion.tests.socket_guard import SocketGuard, guard_for_mode
from leadforge.lead_ingestion.transport import FixtureTransport, TransportResponse

REPO_ROOT = Path(__file__).resolve().parents[5]
PROFILE = REPO_ROOT / "config" / "target_profile.yaml"
DISCOVERED = SourceRegistry.discover()
PROVIDERS = {"apollo", "google_search", "hubspot", "hunter"}


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in DISCOVERED.names():
        for variable in DISCOVERED.source_class(name).required_env:
            monkeypatch.delenv(variable, raising=False)
    for variable in ("LEADFORGE_MODE", "LEADFORGE_ENV_FILE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
    database = tmp_path / "store.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    return database


@pytest.fixture
def guard(
    clean_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SocketGuard]:
    blocked = guard_for_mode(DataMode.SYNTHETIC)
    blocked.install(monkeypatch)
    yield blocked
    blocked.assert_clean()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every fixture the run read, as ``provider:path``."""
    seen: list[str] = []
    real_send = FixtureTransport.send

    async def send(self: FixtureTransport, endpoint: Endpoint, **kwargs: Any) -> Any:
        seen.append(f"{self._provider}:{endpoint.path}")
        return await real_send(self, endpoint, **kwargs)

    monkeypatch.setattr(FixtureTransport, "send", send)
    return seen


def engine_of(database: Path) -> Engine:
    return create_store_engine(f"sqlite:///{database}")


# Verifies: specs/lead-source-adapters/requirements.md#6.9
# Verifies: specs/lead-source-adapters/requirements.md#4.5
async def test_the_real_adapters_run_in_both_phases_and_a_real_lead_is_persisted(
    clean_environment: Path, guard: SocketGuard, served: list[str]
) -> None:
    assert set(DISCOVERED.names()) >= PROVIDERS

    outcome = await run_ingestion(target_profile_path=PROFILE)

    phases = {(r.source_name, r.phase) for r in outcome.results}
    assert phases >= {
        ("apollo", Phase.DISCOVERY),
        ("apollo", Phase.ENRICHMENT),
        ("google_search", Phase.DISCOVERY),
        ("google_search", Phase.ENRICHMENT),
        ("hubspot", Phase.ENRICHMENT),
        ("hunter", Phase.ENRICHMENT),
    }
    assert all(r.outcome.status is SourceStatus.OK for r in outcome.results)
    # Apollo (search and match) answered from its fixtures. Google had no company
    # domain to anchor a query to (gap d), so it asked nothing.
    assert {s for s in served if s.startswith(("apollo:", "google_search:"))} == {
        "apollo:/api/v1/mixed_people/api_search",
        "apollo:/api/v1/people/match",
    }
    engine = engine_of(clean_environment)
    try:
        with Session(engine) as session:
            leads = session.scalars(sa.select(m.CanonicalLeadRow)).all()
            by_source = {
                source: count
                for source, count in session.execute(
                    sa.select(
                        m.SourceContribution.source_name,
                        sa.func.count(m.SourceContribution.id),
                    ).group_by(m.SourceContribution.source_name)
                )
            }
    finally:
        engine.dispose()
    enriched = [lead for lead in leads if lead.email is not None]
    assert enriched, "no canonical lead with an email: Apollo's match fixture is unread"
    assert enriched[0].linkedin_url is not None
    # Masked search results ('Lo***') are no name: they must not become Leads.
    assert all("*" not in (lead.full_name or "") for lead in leads)
    assert len(leads) == len(enriched)
    assert "apollo" in enriched[0].contributing_sources
    assert by_source["apollo"] >= 1
    assert "google_search" not in by_source  # gap d: no anchor, no call


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_hubspot_and_hunter_are_invoked_but_idle_in_a_real_run_gap_c(
    clean_environment: Path, guard: SocketGuard, served: list[str]
) -> None:
    outcome = await run_ingestion(target_profile_path=PROFILE)

    for name in ("hubspot", "hunter"):
        results = [r for r in outcome.results if r.source_name == name]
        assert [r.phase for r in results] == [Phase.ENRICHMENT]
        assert results[0].outcome.succeeded == 1
        assert results[0].contributions == ()
    assert not [s for s in served if s.startswith(("hubspot:", "hunter:"))]


class StandInDiscovery(BaseLeadSource):
    """TEST-ONLY STAND-IN: a Discovery source that names the emails and domains
    HubSpot and Hunter need, which no real Discovery adapter yields (gap c)."""

    name: ClassVar[str] = "stand_in_discovery"
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
            make_lead(self.name, company__domain="alpha.example"),
            make_lead(
                self.name,
                person__first_name="Ada",
                person__last_name="Lovelace",
                company__domain="beta.example",
            ),
            make_lead(self.name, person__email="ada.lovelace@example.com"),
        ]


def with_stand_in(*, without: frozenset[str] = frozenset()) -> SourceRegistry:
    real = [DISCOVERED.source_class(n) for n in DISCOVERED.names() if n not in without]
    return SourceRegistry(
        [*real, StandInDiscovery],
        {StandInDiscovery.name: SourceSettings(mode=DataMode.SYNTHETIC)},
    )


# Verifies: specs/lead-source-adapters/requirements.md#6.1
@pytest.mark.parametrize(
    ("provider", "other", "path", "served_endpoint", "stored"),
    [
        (
            "hubspot",
            "hunter",
            "crm.last_activity_date",  # a datetime, stored as ISO-8601 UTC text
            "hubspot:/crm/objects/{version}/contacts/search",
            lambda value: isinstance(value, str) and value.endswith("+00:00"),
        ),
        (
            "hunter",
            "hubspot",
            "person.email_sources",  # a tuple, stored as a JSON array
            "hunter:/v2/email-finder",
            lambda value: isinstance(value, list) and value,
        ),
    ],
)
async def test_a_lead_that_reaches_this_adapter_persists_its_datetime_and_tuple(
    provider: str,
    other: str,
    path: str,
    served_endpoint: str,
    stored: Any,
    clean_environment: Path,
    guard: SocketGuard,
    served: list[str],
) -> None:
    """With a STAND-IN Discovery that names the email and domains the adapter needs
    (no real Discovery yields them, gap c), the adapter answers from its fixture and
    the merge write persists its datetime / tuple value (it once rolled back)."""
    outcome = await run_ingestion(
        registry=with_stand_in(without=frozenset({other})),
        target_profile_path=PROFILE,
    )

    assert outcome.exit.exit_code == 0
    assert served_endpoint in served
    engine = engine_of(clean_environment)
    try:
        with Session(engine) as session:
            values = session.scalars(
                sa.select(m.ContributionField.value).where(
                    m.ContributionField.canonical_path == path
                )
            ).all()
            leads = session.scalars(sa.select(m.CanonicalLeadRow)).all()
            run = session.scalars(sa.select(m.IngestionRun)).one()
    finally:
        engine.dispose()
    assert values
    assert all(stored(v) for v in values)
    assert leads
    assert (run.status, run.exit_code) == ("completed", 0)


# Verifies: specs/lead-source-adapters/requirements.md#6.4
async def test_every_source_that_runs_failing_exits_non_zero_naming_each_class(
    clean_environment: Path, guard: SocketGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Google is left out: with no discovered company domain it makes no call (gap d),
    # so it cannot fail and would make the run partial rather than all-failed.
    statuses = {"apollo": 401}
    registry = SourceRegistry(
        [DISCOVERED.source_class(n) for n in DISCOVERED.names() if n != "google_search"]
    )

    async def send(self: FixtureTransport, endpoint: Endpoint, **kwargs: Any) -> Any:
        return TransportResponse(
            status=statuses.get(self._provider, 200), headers={}, body={}
        )

    monkeypatch.setattr(FixtureTransport, "send", send)

    outcome = await run_ingestion(registry=registry, target_profile_path=PROFILE)

    assert outcome.exit.exit_code != 0
    lines = outcome.exit.summary.splitlines()
    assert lines[0] == "all enabled sources failed"
    assert any(line.startswith("apollo: unauthorized ") for line in lines)
    # Enrichment-only sources had no work list, so they never ran: not failures.
    assert not any(line.startswith(("hubspot", "hunter")) for line in lines)
    engine = engine_of(clean_environment)
    try:
        with Session(engine) as session:
            run = session.get_one(m.IngestionRun, outcome.run_id)
            leads = session.scalars(sa.select(m.CanonicalLeadRow)).all()
    finally:
        engine.dispose()
    assert run.exit_code == outcome.exit.exit_code
    assert leads == []
    assert "failure=unauthorized" in outcome.report_text
