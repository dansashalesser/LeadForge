"""End to end: Google web evidence attached by agreement in a real run (14.2).

Through ``run_ingestion`` (the real orchestrator, merge, store and run report): a
TEST-ONLY STAND-IN Discovery names two companies by domain (no shipped Discovery
adapter yields a domain), and the REAL Google Search adapter, answering from a
scripted stand-in transport, issues one anchored query per company. All hosts and
pages are hand-made stand-ins.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
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
from leadforge.lead_ingestion.target_profile import TargetProfile
from leadforge.lead_ingestion.transport import Transport

from .test_google_search_attachment import organic, page, responder
from .test_synthetic_zero_sockets import make_lead

P = "company.web_evidence."
STRONG_CO = "acme-data.com"
WEAK_CO = "betaco.com"
PAGES: Mapping[str, list[dict[str, Any]]] = {
    f'"{STRONG_CO}" Apache Cassandra': [
        page(
            organic("https://www.acme-data.com/blog/cassandra"),  # own domain
            organic("https://jobs.board.com/acme-data.com/123"),  # third party, path
            organic("https://news.site.com/x", snippet="acme-data.com picks Cassandra"),
            organic("https://unrelated.org/cassandra"),  # names no domain
            organic("https://www.acme-data.com/blog/cassandra/"),  # duplicate
        )
    ],
    f'"{WEAK_CO}" Apache Cassandra': [
        page(
            organic("https://one.com/betaco.com"),
            organic("https://one.com/betaco.com#again"),  # duplicate
            organic("https://ONE.com/betaco.com/"),  # duplicate
        )
    ],
}


class StandInDiscovery(BaseLeadSource):
    """TEST-ONLY STAND-IN: one person at the strong company, one company-only record."""

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
            make_lead(
                self.name,
                person__email="ada@acme-data.com",
                person__full_name="Ada Example",
                company__domain=STRONG_CO,
            ),
            make_lead(self.name, company__domain=WEAK_CO),
        ]


class ScriptedGoogle(GoogleSearchSource):
    """The real adapter; only its transport is a scripted stand-in."""

    @classmethod
    def build_transport(cls, mode: DataMode, **_: Any) -> Transport:
        return responder(PAGES)


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for variable in ("LEADFORGE_MODE", "LEADFORGE_ENV_FILE", "SERPAPI_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "store.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    return path


def profile(tmp_path: Path) -> TargetProfile:
    return TargetProfile(
        technologies={"cassandra": {"google_search": ["Apache Cassandra"]}}
    )


def stored_google(database: Path) -> tuple[list[dict[str, Any]], int, list[Any]]:
    """Google's stored contributions as path -> value, the lead count, the leads."""
    engine = create_store_engine(f"sqlite:///{database}")
    try:
        with Session(engine) as session:
            rows = session.execute(
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
            leads = session.scalars(sa.select(m.CanonicalLeadRow)).all()
    finally:
        engine.dispose()
    by_id: dict[Any, dict[str, Any]] = {}
    for contribution_id, path, value in rows:
        by_id.setdefault(contribution_id, {})[path] = value
    return list(by_id.values()), len(leads), list(leads)


# Verifies: specs/lead-source-adapters/requirements.md#14.5
# Verifies: specs/lead-source-adapters/requirements.md#24.4
# Verifies: specs/lead-source-adapters/requirements.md#14.6
async def test_a_real_run_attaches_web_evidence_by_agreement_and_creates_no_lead(
    database: Path, tmp_path: Path
) -> None:
    registry = SourceRegistry(
        [StandInDiscovery, ScriptedGoogle],
        {
            StandInDiscovery.name: SourceSettings(mode=DataMode.SYNTHETIC),
            ScriptedGoogle.name: SourceSettings(mode=DataMode.SYNTHETIC),
        },
    )

    outcome = await run_ingestion(registry=registry, target_profile=profile(tmp_path))

    google = [r for r in outcome.results if r.source_name == "google_search"]
    enrichment = [r for r in google if r.phase is Phase.ENRICHMENT]
    assert [r.outcome.status for r in google] == [SourceStatus.OK] * len(google)
    assert len(enrichment) == 1

    evidence, lead_count, leads = stored_google(database)
    attached = [e for e in evidence if "company.domain" in e]
    unattached = [e for e in evidence if "company.domain" not in e]
    # Duplicates are emitted once: 4 distinct strong-company URLs, 1 weak-company URL.
    assert len(evidence) == 5
    assert [e[P + "url"] for e in unattached] == ["https://unrelated.org/cassandra"]
    assert unattached[0][P + "attachment"] == "unattached"
    assert P + "signal_strength" not in unattached[0]

    strong = [e for e in attached if e["company.domain"] == STRONG_CO]
    weak = [e for e in attached if e["company.domain"] == WEAK_CO]
    assert len(strong) == 3
    assert len(weak) == 1
    assert {e[P + "agreeing_hosts"] for e in strong} == {3}
    assert weak[0][P + "agreeing_hosts"] == 1  # duplicates did not inflate it
    assert {e[P + "signal_strength"] for e in strong} == {0.75}
    assert weak[0][P + "signal_strength"] == 0.25
    assert all(e[P + "signal_label"] == "cassandra" for e in attached)

    # Web evidence never creates a person or a Lead (ADR-0001).
    assert not any(path.startswith("person.") for e in evidence for path in e)
    assert lead_count == 1
    assert "google_search" not in leads[0].contributing_sources

    report = outcome.report_text
    assert "google_search: mode=synthetic" in report
    assert "leads_normalized=5" in report
    # Attached and unattached evidence are counted in the report, from the store.
    assert "  web_evidence: attached=4 unattached=1" in report
    assert report.count("web_evidence:") == 1  # only the source that stored any
