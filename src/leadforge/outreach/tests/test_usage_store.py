"""Usage evidence, classification cache and company grades: migration 0013 (Req 5.8)."""

# ruff: noqa: F811 - fixtures imported from support

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.store.migrate import (
    alembic_config,
    downgrade_to_base,
    upgrade_to_head,
)
from leadforge.lead_ingestion.store.models import AppendOnlyViolationError
from leadforge.outreach.tables import OutreachSearch
from leadforge.outreach.tests.support import (  # noqa: F401 - fixtures
    backend,
    blank,
    engine,
    postgres_url,
)
from leadforge.outreach.usage.records import (
    ClassifierStamp,
    EvidenceClass,
    EvidenceRecord,
    Relationship,
)
from leadforge.outreach.usage.store import (
    USAGE_TABLES,
    UsageEvidence,
    UsageStore,
    UsageStoreError,
    evidence_for,
)

NOW = datetime(2026, 10, 8, tzinfo=UTC)


def _names(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def _record(**over: object) -> EvidenceRecord:
    fields: dict[str, object] = {
        "company_key": "acme.example",
        "product_key": "prod_a",
        "evidence_class": EvidenceClass.JOB_POSTING,
        "source": "serp",
        "url": "https://acme.example/jobs/1",
        "observed_on": date(2026, 9, 1),
        "quote": "We run prod_a",
        "relationship": Relationship.USES_NOW,
        "confidence": Decimal("0.85"),
        "snippet_only": False,
        "classifier": ClassifierStamp(
            kind="llm", model="m-1", prompt_version="p-1", input_hash="h1"
        ),
    }
    return EvidenceRecord(**{**fields, **over})  # type: ignore[arg-type]


@pytest.fixture
def search_id(engine: sa.Engine) -> uuid.UUID:
    with Session(engine) as s:
        search = OutreachSearch(
            mode="users",
            query="q",
            plan={},
            compiler="offline",
            status="done",
            created_at=NOW,
        )
        s.add(search)
        s.commit()
        return search.id


# Verifies: specs/user-recognition/requirements.md#5.8
def test_migration_adds_the_usage_tables_and_downgrade_drops_them(
    engine: sa.Engine,
) -> None:
    assert _names(engine) >= USAGE_TABLES
    with engine.begin() as conn:
        downgrade_to_base(conn)
    assert _names(engine) == set()
    with engine.begin() as conn:
        upgrade_to_head(conn)
    assert _names(engine) >= USAGE_TABLES


# Verifies: specs/user-recognition/requirements.md#5.8
def test_down_one_revision_drops_only_the_usage_tables(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        command.downgrade(alembic_config(conn), "0012")
    names = _names(engine)
    assert not USAGE_TABLES & names
    assert "outreach_search" in names
    with engine.begin() as conn:
        upgrade_to_head(conn)


# Verifies: specs/user-recognition/requirements.md#5.8
def test_evidence_round_trips_and_lists_by_search_and_company(
    engine: sa.Engine, search_id: uuid.UUID
) -> None:
    store = UsageStore(engine)
    rec = _record()
    other = _record(company_key="other.example")
    store.append_evidence(search_id, rec)
    store.append_evidence(search_id, other)
    got = store.list_evidence(search_id, company_key="acme.example")
    assert [r.record for r in got] == [rec]
    assert got[0].record.confidence == Decimal("0.85")
    assert len(store.list_evidence(search_id)) == 2


# Verifies: specs/user-recognition/requirements.md#5.8
def test_evidence_for_reads_one_company_on_a_caller_s_session(
    engine: sa.Engine, search_id: uuid.UUID
) -> None:
    store = UsageStore(engine)
    rec = _record()
    store.append_evidence(search_id, rec)
    store.append_evidence(search_id, _record(company_key="other.example"))

    with Session(engine) as session:
        got = evidence_for(session, search_id, "acme.example")
        elsewhere = evidence_for(session, uuid.uuid4(), "acme.example")

    assert got == (rec,)
    assert elsewhere == ()


# Verifies: specs/user-recognition/requirements.md#5.8
def test_evidence_is_append_only(engine: sa.Engine, search_id: uuid.UUID) -> None:
    store = UsageStore(engine)
    store.append_evidence(search_id, _record())
    assert not hasattr(store, "update_evidence")
    assert not hasattr(store, "delete_evidence")
    with Session(engine) as s:
        row = s.scalars(sa.select(UsageEvidence)).one()
        row.quote = "changed"
        with pytest.raises(AppendOnlyViolationError):
            s.commit()
    with Session(engine) as s:
        s.delete(s.scalars(sa.select(UsageEvidence)).one())
        with pytest.raises(AppendOnlyViolationError):
            s.commit()
    with Session(engine) as s, pytest.raises(AppendOnlyViolationError):
        s.execute(sa.update(UsageEvidence).values(quote="x"))
    with Session(engine) as s, pytest.raises(AppendOnlyViolationError):
        s.execute(sa.delete(UsageEvidence))
    assert len(store.list_evidence(search_id)) == 1


# Verifies: specs/user-recognition/requirements.md#5.8
def test_cache_returns_the_stored_judgement_for_the_same_input_hash(
    engine: sa.Engine,
) -> None:
    store = UsageStore(engine)
    assert store.cache_get("h1") is None
    judgement = {"relationship": "uses_now", "confidence_milli": 850}
    store.cache_put("h1", judgement, model="m-1", prompt_version="p-1")
    assert store.cache_get("h1") == judgement
    # a second put for the same hash keeps the first answer
    store.cache_put(
        "h1", {"relationship": "unrelated"}, model="m-1", prompt_version="p-1"
    )
    assert store.cache_get("h1") == judgement
    assert store.cache_get("h2") is None


# Verifies: specs/user-recognition/requirements.md#5.8
def test_company_grade_upsert_replaces_the_row(
    engine: sa.Engine, search_id: uuid.UUID
) -> None:
    store = UsageStore(engine)
    store.upsert_company_grade(
        search_id, "acme.example", "prod_a", "likely", "r1", ["a"]
    )
    store.upsert_company_grade(
        search_id, "acme.example", "prod_a", "confirmed", "r2", ["a", "b"]
    )
    grade = store.get_company_grade(search_id, "acme.example", "prod_a")
    assert grade is not None
    assert (grade.grade, grade.reason, grade.record_ids) == (
        "confirmed",
        "r2",
        ["a", "b"],
    )
    assert store.get_company_grade(search_id, "none.example", "prod_a") is None


# Verifies: specs/user-recognition/requirements.md#5.8
def test_an_empty_input_hash_is_refused(engine: sa.Engine) -> None:
    with pytest.raises(UsageStoreError):
        UsageStore(engine).cache_get("")
