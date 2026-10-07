"""Revision 0006: the derived contribution-to-lead mapping and lead retirement.

Both engines (fixtures of ``test_persistence_both_engines``). The upgrade backfills the
mapping from each contribution's first-seen ``lead_identity_id`` so a store written
before 0006 re-merges without relinking; the downgrade drops only what 0006 added and
keeps every existing row.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Engine, inspect

from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    _alembic,
    blank,
    postgres_url,
)

T0 = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
# The table as it was before 0007 (which dropped the first-seen lead column).
CONTRIBUTION_0005 = sa.table(
    "source_contribution",
    *(sa.column(c.name, c.type) for c in m.SourceContribution.__table__.columns),
    sa.column("lead_identity_id", sa.Uuid()),
)
# canonical_lead without the columns 0009 added (and their app-side defaults).
CANONICAL_LEAD_BEFORE_0009 = sa.table(
    "canonical_lead",
    *(
        sa.column(c.name, c.type)
        for c in m.CanonicalLeadRow.__table__.columns
        if c.name not in {"email_is_role_address", "role_contact_emails"}
    ),
)
NEW_TABLES = {"contribution_lead", "lead_succession", "contribution_absence"}


def _seed_at_0005(engine: Engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A run, an identity, a linked contribution with a field and a canonical lead."""
    run, sr, identity, linked, loose, field = (uuid.uuid4() for _ in range(6))
    with engine.begin() as conn:
        conn.execute(
            sa.insert(m.IngestionRun).values(id=run, started_at=T0, status="completed")
        )
        conn.execute(
            sa.insert(m.SourceRun).values(
                id=sr, run_id=run, source_name="acme", resolved_mode="synthetic"
            )
        )
        conn.execute(sa.insert(m.LeadIdentity).values(id=identity, created_at=T0))
        for contribution, lead in ((linked, identity), (loose, None)):
            conn.execute(
                sa.insert(CONTRIBUTION_0005).values(
                    id=contribution,
                    source_run_id=sr,
                    lead_identity_id=lead,
                    source_name="acme",
                    data_mode="synthetic",
                    fetched_at=T0,
                    lead_scope="person",
                )
            )
        conn.execute(
            sa.insert(m.ContributionField).values(
                id=field,
                contribution_id=linked,
                canonical_path="person.email",
                value="ada@example.com",
                raw_field_path="email",
                confidence=0.5,
                untrusted=False,
                truncated=False,
            )
        )
        conn.execute(
            sa.insert(CANONICAL_LEAD_BEFORE_0009).values(
                id=uuid.uuid4(),
                lead_identity_id=identity,
                email="ada@example.com",
                opt_out=False,
                suppressed=False,
                contributing_sources=["acme"],
                computed_at=T0,
                projection_version=1,
            )
        )
    return identity, linked, loose


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _columns(engine: Engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            model.__tablename__: conn.scalar(
                sa.select(sa.func.count()).select_from(model.__table__)
            )
            or 0
            for model in (
                m.LeadIdentity,
                m.SourceContribution,
                m.ContributionField,
                m.CanonicalLeadRow,
            )
        }


# Verifies: specs/lead-source-adapters/requirements.md#8.12
# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_0006_backfills_the_mapping_and_downgrades_keeping_rows(blank: Backend) -> None:
    _alembic(blank, "upgrade", "0005")
    identity, linked, loose = _seed_at_0005(blank.engine)
    before = _counts(blank.engine)

    _alembic(blank, "upgrade", "0006")
    assert _tables(blank.engine) >= NEW_TABLES
    assert "retired_at" in _columns(blank.engine, "lead_identity")
    assert {
        "primary_domain",
        "primary_domain_source",
        "projection_fingerprint",
    } <= _columns(blank.engine, "canonical_lead")
    assert {"content_sha"} <= _columns(blank.engine, "source_contribution")
    assert {"confidence_origin", "confidence_raw", "confidence_scale"} <= _columns(
        blank.engine, "contribution_field"
    )
    assert {"leads_merged", "leads_retired"} <= _columns(blank.engine, "ingestion_run")
    with blank.engine.connect() as conn:
        mapped = dict(
            list(
                conn.execute(
                    sa.select(
                        m.ContributionLead.contribution_id,
                        m.ContributionLead.lead_identity_id,
                    )
                )
            )
        )
        retired = conn.scalar(
            sa.select(m.LeadIdentity.retired_at).where(m.LeadIdentity.id == identity)
        )
    assert mapped == {
        linked: identity
    }  # a contribution with no identity stays unmapped
    assert loose not in mapped
    assert retired is None
    assert _counts(blank.engine) == before

    _alembic(blank, "downgrade", "0005")
    assert not NEW_TABLES & _tables(blank.engine)
    assert "retired_at" not in _columns(blank.engine, "lead_identity")
    assert "content_sha" not in _columns(blank.engine, "source_contribution")
    assert _counts(blank.engine) == before

    _alembic(blank, "upgrade", "head")
    with blank.engine.connect() as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count()).select_from(m.ContributionLead.__table__)
            )
            == 1
        )
