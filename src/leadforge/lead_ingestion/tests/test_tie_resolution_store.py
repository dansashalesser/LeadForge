"""Persisting tie resolutions: migration 0004, repository, first write wins (8.18)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.migrate import (
    downgrade_to_base,
    upgrade_to_head,
)
from leadforge.lead_ingestion.store.tie_resolutions import TieResolutionRepository
from leadforge.lead_ingestion.tie_resolution import TieResolutionRecord, tie_key

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
CANDIDATES = ("a.com", "b.com")
KEY = tie_key(("a.com", "b.com", "c.com"), CANDIDATES)


def record(chosen: str = "b.com", when: datetime = NOW) -> TieResolutionRecord:
    return TieResolutionRecord(chosen, CANDIDATES, "fake-model", "p1", when)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
    with Session(engine) as s:
        yield s


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_put_record_reads_back_with_every_field(session: Session) -> None:
    repo = TieResolutionRepository(session)
    assert repo.get(KEY) is None
    assert repo.put(KEY, record()) == record()
    session.commit()
    session.expire_all()
    got = repo.get(KEY)
    assert got == record()
    assert got is not None
    assert got.resolved_at.tzinfo is not None
    assert got.candidates == CANDIDATES


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_the_instant_is_stored_in_utc_whatever_the_offset_written(
    session: Session,
) -> None:
    plus_two = NOW.astimezone(timezone(timedelta(hours=2)))
    TieResolutionRepository(session).put(KEY, record(when=plus_two))
    session.commit()
    session.expire_all()
    got = TieResolutionRepository(session).get(KEY)
    assert got is not None
    assert got.resolved_at == NOW


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_putting_the_same_answer_twice_is_idempotent(session: Session) -> None:
    repo = TieResolutionRepository(session)
    repo.put(KEY, record())
    assert repo.put(KEY, record()) == record()
    assert (
        session.scalar(
            sa.select(sa.func.count()).select_from(m.PrimaryDomainTieResolution)
        )
        == 1
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_second_put_with_a_different_answer_keeps_the_first(
    session: Session,
) -> None:
    repo = TieResolutionRepository(session)
    repo.put(KEY, record("b.com"))
    kept = repo.put(KEY, TieResolutionRecord("a.com", CANDIDATES, "other", "p2", NOW))
    assert kept == record("b.com")
    session.commit()
    session.expire_all()
    assert repo.get(KEY) == record("b.com")


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_row_inserted_behind_our_back_is_adopted_not_raised(
    session: Session,
) -> None:
    repo = TieResolutionRepository(session)
    original_get = repo.get
    misses = iter([None])  # the first look misses: we "lost the race"
    repo.get = lambda key: next(misses, None) or original_get(key)  # type: ignore[method-assign]
    session.add(
        m.PrimaryDomainTieResolution(
            tie_key=KEY,
            chosen_domain="a.com",
            candidates=list(CANDIDATES),
            model="x",
            prompt_version="p0",
            resolved_at=NOW,
        )
    )
    session.flush()
    kept = repo.put(KEY, record("b.com"))
    assert kept.chosen_domain == "a.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_stored_resolution_cannot_be_updated_or_deleted(session: Session) -> None:
    TieResolutionRepository(session).put(KEY, record())
    session.commit()
    row = session.scalars(sa.select(m.PrimaryDomainTieResolution)).one()
    row.chosen_domain = "a.com"
    with pytest.raises(m.AppendOnlyViolationError):
        session.flush()
    session.rollback()
    with pytest.raises(m.AppendOnlyViolationError):
        session.execute(sa.delete(m.PrimaryDomainTieResolution))


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_the_migration_creates_and_drops_the_table() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
        assert "primary_domain_tie_resolution" in sa.inspect(conn).get_table_names()
        uniques = sa.inspect(conn).get_unique_constraints(
            "primary_domain_tie_resolution"
        )
        assert [u["column_names"] for u in uniques] == [["tie_key"]]
        downgrade_to_base(conn)
        assert "primary_domain_tie_resolution" not in sa.inspect(conn).get_table_names()
