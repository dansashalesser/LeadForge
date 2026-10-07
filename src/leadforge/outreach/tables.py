"""The four outreach tables (outreach requirements 10.1, 10.2, 10.4, 9.4).

Mapped on the Lead Store's declarative ``Base``: one schema, one migration history
(``0012``), and the store's string-column checks apply to these rows too. Every key is
a plain id of an existing table (``ingestion_run``, ``lead_identity``); no provider
field is copied, and a Lead's own data is read through ingestion, never stored here.

``OutreachMessage`` and ``OutreachTriggerEvent`` are append-only: an ORM update or
delete, per object or bulk, raises ``AppendOnlyViolationError``. A Message ``state`` can
only be ``dry_run`` (a database CHECK as well): nothing is ever delivered.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, Mapper, ORMExecuteState, Session, mapped_column

from leadforge.lead_ingestion.store.models import AppendOnlyViolationError, Base

__all__ = [
    "OUTREACH_TABLES",
    "OutreachDecision",
    "OutreachMessage",
    "OutreachSearch",
    "OutreachTriggerEvent",
]


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _utc() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True))


class OutreachSearch(Base):
    __tablename__ = "outreach_search"
    __table_args__ = (Index("ix_outreach_search_ingestion_run_id", "ingestion_run_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    mode: Mapped[str] = mapped_column(String(32))
    query: Mapped[str] = mapped_column(String(2048))
    plan: Mapped[dict[str, Any]] = mapped_column(JSON)
    compiler: Mapped[str] = mapped_column(String(16))
    # NULL until the search's ingestion run starts (linked before its first request).
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_run.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _utc()


class OutreachDecision(Base):
    __tablename__ = "outreach_decision"
    __table_args__ = (
        UniqueConstraint("search_id", "lead_id", name="uq_outreach_decision_lead"),
        CheckConstraint(
            "status IN ('selected', 'rejected', 'needs_enrichment', 'manual_review')",
            name="ck_outreach_decision_status",
        ),
        Index("ix_outreach_decision_lead_id", "lead_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    search_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outreach_search.id"))
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lead_identity.id"))
    status: Mapped[str] = mapped_column(String(32))
    # The score in thousandths (0..1000): an exact integer on every engine.
    score_milli: Mapped[int] = mapped_column(Integer)
    reasons: Mapped[list[Any]] = mapped_column(JSON)
    decided_at: Mapped[datetime] = _utc()


class OutreachMessage(Base):
    __tablename__ = "outreach_message"
    __table_args__ = (
        CheckConstraint("state = 'dry_run'", name="ck_outreach_message_state"),
        CheckConstraint(
            "channel IN ('linkedin', 'email')", name="ck_outreach_message_channel"
        ),
        Index("ix_outreach_message_decision_id", "decision_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outreach_decision.id"))
    channel: Mapped[str] = mapped_column(String(16))
    variant: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    generator: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    checks: Mapped[dict[str, Any]] = mapped_column(JSON)
    # SQL NULL, not a JSON null, when no judge ran.
    judge: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    state: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = _utc()


class OutreachTriggerEvent(Base):
    __tablename__ = "outreach_trigger_event"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('invite', 'accepted', 'email', 'fallback_email', 'stalled',"
            " 'halted')",
            name="ck_outreach_trigger_event_kind",
        ),
        Index("ix_outreach_trigger_event_decision_id", "decision_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outreach_decision.id"))
    kind: Mapped[str] = mapped_column(String(32))
    at: Mapped[datetime] = _utc()
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)


_APPEND_ONLY = (OutreachMessage, OutreachTriggerEvent)
_APPEND_ONLY_TABLES = frozenset(c.__tablename__ for c in _APPEND_ONLY)
OUTREACH_TABLES = frozenset(
    {
        OutreachSearch.__tablename__,
        OutreachDecision.__tablename__,
        OutreachMessage.__tablename__,
        OutreachTriggerEvent.__tablename__,
    }
)


def _refuse_row_change(mapper: Mapper[Any], connection: Any, target: Any) -> None:
    raise AppendOnlyViolationError(
        f"{type(target).__name__} is append-only: no update or delete (10.4)"
    )


def _refuse_bulk_change(state: ORMExecuteState) -> None:
    if not (state.is_update or state.is_delete):
        return
    name = getattr(getattr(state.statement, "table", None), "name", None)
    if name in _APPEND_ONLY_TABLES:
        raise AppendOnlyViolationError(
            f"{name} is append-only: no bulk update or delete (10.4)"
        )


for _model in _APPEND_ONLY:
    event.listen(_model, "before_update", _refuse_row_change)
    event.listen(_model, "before_delete", _refuse_row_change)
event.listen(Session, "do_orm_execute", _refuse_bulk_change)
