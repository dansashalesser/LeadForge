"""The run merges with the stored log, asks for tie answers, and reports retirements.

Requirements 8.12 (no duplicate Lead from repeated ingestion), 8.18 (the run resolves
an exact primary-domain tie through the stored answer; never a model in synthetic
mode) and 21.5 (the report names how many Leads were merged and retired). Run-level
tests use the composed run of ``test_run_lifecycle_both_engines`` on both engines.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion import ingest_runner
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.remerge import project_with_store
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.test_lead_remerge import (
    NONE,
    NOW,
    RANKS,
    contribution,
)
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    PROFILE,
    composed,
)
from leadforge.lead_ingestion.tie_resolution import TieSource


def _active(backend: Backend) -> int:
    with Session(backend.engine) as s:
        return (
            s.scalar(
                sa.select(sa.func.count(m.CanonicalLeadRow.id))
                .join(
                    m.LeadIdentity,
                    m.LeadIdentity.id == m.CanonicalLeadRow.lead_identity_id,
                )
                .where(m.LeadIdentity.retired_at.is_(None))
            )
            or 0
        )


def _count(backend: Backend, model: type[m.Base]) -> int:
    with Session(backend.engine) as s:
        return s.scalar(sa.select(sa.func.count()).select_from(model)) or 0


# Verifies: specs/lead-source-adapters/requirements.md#8.12
# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_running_twice_on_the_same_data_grows_no_lead_on_each_engine(
    composed: Backend,
) -> None:
    first = await ingest_runner.run_ingestion(target_profile=PROFILE)
    leads = _active(composed)
    identities = _count(composed, m.LeadIdentity)
    contributions = _count(composed, m.SourceContribution)
    again = await ingest_runner.run_ingestion(target_profile=PROFILE)

    assert leads > 0
    assert _active(composed) == leads
    assert _count(composed, m.LeadIdentity) == identities
    assert _count(composed, m.SourceContribution) == contributions
    assert _count(composed, m.CanonicalLeadRow) == leads
    assert (again.stored.leads_created, again.stored.leads_retired) == (0, 0)
    assert f"leads: {leads} merged, 0 retired" in first.report_text.splitlines()
    # Incremental: the identical second run re-projects and rewrites nothing.
    assert "leads: 0 merged, 0 retired" in again.report_text.splitlines()
    with Session(composed.engine) as s:
        runs = s.scalars(sa.select(m.IngestionRun)).all()
    assert sorted((r.leads_merged, r.leads_retired) for r in runs) == [
        (0, 0),
        (leads, 0),
    ]


class _Resolver:
    model = "test-model"
    prompt_version = "v1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def choose(self, candidates: tuple[str, ...], *, timeout: float) -> object:
        self.calls.append(candidates)
        return candidates[-1]


def _tied(mode: DataMode) -> LeadContribution:
    return contribution(
        "alpha",
        mode=mode,
        person__email="ada@example.com",
        person__email_status="verified",
        company__domain=["a.com", "b.com"],
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_live_tie_is_resolved_once_stored_and_read_back(backend: Backend) -> None:
    resolver = _Resolver()
    with Session(backend.engine) as s, s.begin():
        ((_, result),) = project_with_store(
            s,
            [_tied(DataMode.LIVE)],
            exclusions=NONE,
            trust_ranks=RANKS,
            now=NOW,
            tie_resolver=lambda: resolver,
        )
    assert resolver.calls == [("a.com", "b.com")]
    assert result.primary_domain == "b.com"
    assert not result.primary_domain_flagged
    assert _count(backend, m.PrimaryDomainTieResolution) == 1

    with Session(backend.engine) as s, s.begin():
        ((_, again),) = project_with_store(
            s,
            [_tied(DataMode.LIVE)],
            exclusions=NONE,
            trust_ranks=RANKS,
            now=NOW + timedelta(days=1),
            tie_resolver=lambda: resolver,
        )
    assert len(resolver.calls) == 1  # the stored answer is read, not asked again
    assert (again.primary_domain, again.primary_domain_source) == (
        "b.com",
        TieSource.STORED,
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_synthetic_tie_never_asks_and_is_flagged(backend: Backend) -> None:
    resolver = _Resolver()
    with Session(backend.engine) as s, s.begin():
        ((_, result),) = project_with_store(
            s,
            [_tied(DataMode.SYNTHETIC)],
            exclusions=NONE,
            trust_ranks=RANKS,
            now=NOW,
            tie_resolver=lambda: resolver,
        )
    assert resolver.calls == []
    assert (result.primary_domain, result.primary_domain_source) == (
        "a.com",
        TieSource.SYNTHETIC_PROVISIONAL,
    )
    assert result.primary_domain_flagged
    assert _count(backend, m.PrimaryDomainTieResolution) == 0
