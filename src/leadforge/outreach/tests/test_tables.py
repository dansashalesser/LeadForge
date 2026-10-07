"""The four outreach tables: migration 0012, append-only, dry_run only (10.x, 9.4).

Migration up and down runs on SQLite here; the Postgres leg is task 10.4.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.migrate import (
    alembic_config,
    downgrade_to_base,
    upgrade_to_head,
)
from leadforge.lead_ingestion.tests.test_vendor_neutrality import DENYLIST
from leadforge.outreach.tables import (
    OUTREACH_TABLES,
    OutreachDecision,
    OutreachMessage,
    OutreachSearch,
    OutreachTriggerEvent,
)

NOW = datetime(2026, 10, 7, tzinfo=UTC)


@pytest.fixture
def engine() -> Iterator[sa.Engine]:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
    yield engine
    engine.dispose()


def _names(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def _seed(session: Session) -> OutreachDecision:
    identity = m.LeadIdentity(created_at=NOW)
    run = m.IngestionRun(started_at=NOW, status="completed")
    session.add_all([identity, run])
    session.flush()
    search = OutreachSearch(
        mode="free_text",
        query="q",
        plan={},
        compiler="offline",
        ingestion_run_id=run.id,
        status="done",
        created_at=NOW,
    )
    session.add(search)
    session.flush()
    decision = OutreachDecision(
        search_id=search.id,
        lead_id=identity.id,
        status="selected",
        score_milli=700,
        reasons=[{"name": "icp_fit"}],
        decided_at=NOW,
    )
    session.add(decision)
    session.flush()
    return decision


def _message(decision: OutreachDecision, **over: object) -> OutreachMessage:
    fields: dict[str, object] = {
        "decision_id": decision.id,
        "channel": "linkedin",
        "variant": "invite",
        "subject": None,
        "body": "Hi",
        "generator": "offline",
        "model": "offline",
        "prompt_version": "template-1",
        "checks": {},
        "state": "dry_run",
        "created_at": NOW,
    }
    return OutreachMessage(**{**fields, **over})


# Verifies: outreach requirements 10.1
def test_migration_adds_the_four_tables_and_downgrade_drops_them(
    engine: sa.Engine,
) -> None:
    assert _names(engine) >= OUTREACH_TABLES

    with engine.begin() as conn:
        downgrade_to_base(conn)
    assert _names(engine) == set()

    with engine.begin() as conn:
        upgrade_to_head(conn)
    assert _names(engine) >= OUTREACH_TABLES


# Verifies: outreach requirements 10.1
def test_down_to_the_previous_revision_keeps_the_ingestion_tables(
    engine: sa.Engine,
) -> None:
    with engine.begin() as conn:
        command.downgrade(alembic_config(conn), "0011")
    names = _names(engine)

    assert not OUTREACH_TABLES & names
    assert {"ingestion_run", "lead_identity", "canonical_lead"} <= names


# Verifies: outreach requirements 10.1
def test_a_full_graph_round_trips(engine: sa.Engine) -> None:
    with Session(engine) as session:
        decision = _seed(session)
        session.add_all(
            [
                _message(decision),
                OutreachTriggerEvent(
                    decision_id=decision.id, kind="invite", at=NOW, detail={}
                ),
            ]
        )
        session.commit()

    with Session(engine) as session:
        assert session.scalar(sa.select(sa.func.count(OutreachMessage.id))) == 1
        row = session.scalars(sa.select(OutreachDecision)).one()
        assert (row.status, row.score_milli) == ("selected", 700)


# Verifies: outreach requirements 10.4
def test_updating_or_deleting_a_stored_message_raises_a_named_error(
    engine: sa.Engine,
) -> None:
    with Session(engine) as session:
        decision = _seed(session)
        message = _message(decision)
        session.add(message)
        session.commit()

        message.body = "changed"
        with pytest.raises(m.AppendOnlyViolationError):
            session.flush()
        session.rollback()

        session.delete(session.get_one(OutreachMessage, message.id))
        with pytest.raises(m.AppendOnlyViolationError):
            session.flush()
        session.rollback()

        with pytest.raises(m.AppendOnlyViolationError):
            session.execute(sa.update(OutreachMessage).values(body="x"))
        with pytest.raises(m.AppendOnlyViolationError):
            session.execute(sa.delete(OutreachMessage))


# Verifies: outreach requirements 10.4
def test_updating_or_deleting_a_trigger_event_raises_a_named_error(
    engine: sa.Engine,
) -> None:
    with Session(engine) as session:
        decision = _seed(session)
        event = OutreachTriggerEvent(
            decision_id=decision.id, kind="invite", at=NOW, detail={}
        )
        session.add(event)
        session.commit()

        event.kind = "halted"
        with pytest.raises(m.AppendOnlyViolationError):
            session.flush()
        session.rollback()
        with pytest.raises(m.AppendOnlyViolationError):
            session.execute(sa.delete(OutreachTriggerEvent))


# Verifies: outreach requirements 9.4
@pytest.mark.parametrize("state", ["sent", "delivered", "queued", ""])
def test_a_message_state_other_than_dry_run_is_refused_by_the_database(
    engine: sa.Engine, state: str
) -> None:
    with Session(engine) as session:
        decision = _seed(session)
        session.add(_message(decision, state=state))
        with pytest.raises(sa.exc.IntegrityError):
            session.flush()


# Verifies: outreach requirements 10.1
def test_a_lead_has_one_decision_per_search_and_known_statuses_only(
    engine: sa.Engine,
) -> None:
    with Session(engine) as session:
        first = _seed(session)
        session.add(
            OutreachDecision(
                search_id=first.search_id,
                lead_id=first.lead_id,
                status="selected",
                score_milli=1,
                reasons=[],
                decided_at=NOW,
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            session.flush()
        session.rollback()

        session.add(
            OutreachTriggerEvent(
                decision_id=first.id, kind="delivered", at=NOW, detail={}
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            session.flush()


# Verifies: outreach requirements 10.2
def test_no_outreach_column_is_named_like_a_provider_or_a_copied_lead_field() -> None:
    lead_fields = {
        c.name
        for c in m.CanonicalLeadRow.__table__.columns
        if c.name not in {"id", "computed_at"}
    }
    provider_words = ("lifecycle", "deal", "contact", "organization", "hs_", "person")
    for name in OUTREACH_TABLES:
        for column in m.Base.metadata.tables[name].columns:
            assert column.name not in lead_fields, (name, column.name)
            assert not any(v in column.name for v in DENYLIST), (name, column.name)
            assert not any(w in column.name for w in provider_words), (
                name,
                column.name,
            )
