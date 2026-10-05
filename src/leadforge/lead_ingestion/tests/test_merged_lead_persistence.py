"""Persisting a merge: identities, contributions, canonical leads, provenance (task 20).

Requirements 4.5 (a run persists canonical leads), 9.x (the append-only contribution log
and the derived projection) and 8.5 to 8.7 (the winning field, its agreeing sources and
the superseded losers are kept). The merge itself is the real engine
(``cluster_contributions`` then ``project_lead``); only the contributions are hand-made.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import cluster_contributions
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.models import ConfidenceOrigin, DataMode, FieldProvenance
from leadforge.lead_ingestion.projection import project_lead
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.merged_leads import (
    MergeStored,
    SourceBatch,
    persist_merge,
)
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.raw_responses import RetentionPolicy

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    upgrade_to_head(url)
    eng = create_store_engine(url)
    yield eng
    eng.dispose()


def contribution(source: str, **values: object) -> LeadContribution:
    """A contribution from canonical path (``__`` for ``.``) to value."""
    paths = {k.replace("__", "."): v for k, v in values.items()}
    return LeadContribution(
        source_name=source,
        values=paths,
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name=source,
                data_mode=DataMode.SYNTHETIC,
                fetched_at=NOW,
                raw_field_path=f"raw.{path}",
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            )
            for path in paths
        ),
    )


def start_run(session: Session, *sources: str) -> uuid.UUID:
    run = m.IngestionRun(started_at=NOW, status="running")
    session.add(run)
    session.flush()
    for name in sources:
        session.add(
            m.SourceRun(run_id=run.id, source_name=name, resolved_mode="synthetic")
        )
    session.flush()
    return run.id


def merge(
    session: Session,
    run_id: uuid.UUID,
    batches: list[SourceBatch],
    ranks: dict[str, int],
) -> MergeStored:
    contributions = [c for b in batches for c in b.contributions]
    clusters = cluster_contributions(contributions)
    merged = [(c, project_lead(c, ranks)) for c in clusters]
    return persist_merge(
        session,
        run_id=run_id,
        batches=batches,
        merged=merged,
        computed_at=NOW,
        projection_version=1,
        retention=RetentionPolicy(),
    )


def two_source_batches() -> list[SourceBatch]:
    a = contribution(
        "alpha",
        person__email="ada@example.com",
        person__full_name="Ada Lovelace",
        person__linkedin_url="https://www.linkedin.com/in/ada",
        company__name="Analytical Engines",
    )
    b = contribution(
        "beta",
        person__email="ada@example.com",
        person__full_name="Ada King",
        person__linkedin_url="https://www.linkedin.com/in/ada",
    )
    return [
        SourceBatch("alpha", DataMode.SYNTHETIC, "discovery", {"raw": "a"}, (a,)),
        SourceBatch("beta", DataMode.SYNTHETIC, "enrichment", {"raw": "b"}, (b,)),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#4.5
def test_a_merge_persists_one_canonical_lead_with_its_identity_and_sources(
    engine: Engine,
) -> None:
    with Session(engine) as session, session.begin():
        run_id = start_run(session, "alpha", "beta")
        stored = merge(session, run_id, two_source_batches(), {"alpha": 2, "beta": 1})

    assert (stored.canonical_leads, stored.contributions) == (1, 2)
    with Session(engine) as session:
        lead = session.scalars(sa.select(m.CanonicalLeadRow)).one()
        assert lead.email == "ada@example.com"
        assert lead.full_name == "Ada Lovelace"  # alpha ranks higher
        assert lead.linkedin_url is not None
        assert lead.contributing_sources == ["alpha", "beta"]
        assert lead.projection_version == 1
        rows = session.scalars(sa.select(m.SourceContribution)).all()
        assert {r.lead_identity_id for r in rows} == {lead.lead_identity_id}
        assert session.scalars(sa.select(m.LeadIdentity)).one().id == (
            lead.lead_identity_id
        )


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_the_winning_field_agreeing_count_and_superseded_losers_are_kept(
    engine: Engine,
) -> None:
    with Session(engine) as session, session.begin():
        run_id = start_run(session, "alpha", "beta")
        merge(session, run_id, two_source_batches(), {"alpha": 2, "beta": 1})

    with Session(engine) as session:
        lead = session.scalars(sa.select(m.CanonicalLeadRow)).one()
        by_path = {
            p.canonical_path: p
            for p in session.scalars(
                sa.select(m.CanonicalFieldProvenance).where(
                    m.CanonicalFieldProvenance.canonical_lead_id == lead.id
                )
            )
        }

        def field(field_id: uuid.UUID) -> m.ContributionField:
            return session.get_one(m.ContributionField, field_id)

        email = by_path["person.email"]
        assert email.agreeing_source_count == 2
        assert email.superseded_field_ids == []
        assert field(email.winning_field_id).value == "ada@example.com"

        name = by_path["person.full_name"]
        assert name.agreeing_source_count == 1
        assert field(name.winning_field_id).value == "Ada Lovelace"
        assert [field(uuid.UUID(i)).value for i in name.superseded_field_ids] == [
            "Ada King"
        ]


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_each_batch_keeps_its_raw_response_and_contributions_point_at_it(
    engine: Engine,
) -> None:
    with Session(engine) as session, session.begin():
        run_id = start_run(session, "alpha", "beta")
        merge(session, run_id, two_source_batches(), {"alpha": 2, "beta": 1})

    with Session(engine) as session:
        raws = {
            r.endpoint_key: r
            for r in session.scalars(
                sa.select(m.RawResponse).options(sa.orm.undefer(m.RawResponse.payload))
            )
        }
        assert {k: r.payload for k, r in raws.items()} == {
            "discovery": {"raw": "a"},
            "enrichment": {"raw": "b"},
        }
        assert all(r.retention_until is None for r in raws.values())  # synthetic
        for row in session.scalars(sa.select(m.SourceContribution)):
            assert row.raw_response_id is not None
            raw = session.get_one(m.RawResponse, row.raw_response_id)
            source_run = session.get_one(m.SourceRun, raw.source_run_id)
            assert source_run.source_name == row.source_name


# Verifies: specs/lead-source-adapters/requirements.md#1.5
def test_evidence_that_names_no_person_is_stored_but_forms_no_canonical_lead(
    engine: Engine,
) -> None:
    web = contribution("alpha", company__web_evidence__url="https://x.example/p")
    batches = [SourceBatch("alpha", DataMode.SYNTHETIC, "discovery", {}, (web,))]

    with Session(engine) as session, session.begin():
        run_id = start_run(session, "alpha")
        stored = merge(session, run_id, batches, {"alpha": 0})

    assert (stored.canonical_leads, stored.contributions) == (0, 1)
    with Session(engine) as session:
        assert session.scalars(sa.select(m.CanonicalLeadRow)).all() == []
        assert len(session.scalars(sa.select(m.LeadIdentity)).all()) == 1
        assert len(session.scalars(sa.select(m.SourceContribution)).all()) == 1


# Verifies: specs/lead-source-adapters/requirements.md#9.6
def test_a_contribution_no_cluster_holds_is_refused_and_nothing_is_kept(
    engine: Engine,
) -> None:
    kept = contribution("alpha", person__email="ada@example.com")
    stray = contribution("alpha", person__email="grace@example.com")
    clusters = cluster_contributions([kept])
    batches = [SourceBatch("alpha", DataMode.SYNTHETIC, "discovery", {}, (kept, stray))]

    with (
        pytest.raises(ValueError, match="no cluster"),
        Session(engine) as session,
        session.begin(),
    ):
        persist_merge(
            session,
            run_id=start_run(session, "alpha"),
            batches=batches,
            merged=[(c, project_lead(c, {})) for c in clusters],
            computed_at=NOW,
            projection_version=1,
            retention=RetentionPolicy(),
        )

    with Session(engine) as session:
        assert session.scalars(sa.select(m.SourceContribution)).all() == []


# Verifies: specs/lead-source-adapters/requirements.md#9.6
def test_a_batch_for_a_source_the_run_never_listed_is_refused(engine: Engine) -> None:
    lone = contribution("ghost", person__email="ada@example.com")
    batches = [SourceBatch("ghost", DataMode.SYNTHETIC, "discovery", {}, (lone,))]

    with (
        pytest.raises(LookupError, match="ghost"),
        Session(engine) as session,
        session.begin(),
    ):
        merge(session, start_run(session, "alpha"), batches, {})
