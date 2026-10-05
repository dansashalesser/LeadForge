"""Typed ORM schema for the Lead Store (design.md, Logical Data Model).

Column types are the engine-portable set only: ``Uuid``, ``String``, ``Integer``,
``Float``, ``Boolean``, ``JSON`` and ``DateTime(timezone=True)``. There is no
dialect type, no dialect-conditional branch, no ``server_default`` and no raw SQL
here; the schema reaches a database through migrations (task 6.2).

``SourceContribution`` and ``ContributionField`` are append-only (8.12). Every
ORM-level update or delete of either, per-object or bulk, raises
``AppendOnlyViolationError``; the guard is registered when this module is imported.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Mapper,
    ORMExecuteState,
    Session,
    mapped_column,
)

__all__ = [
    "AppendOnlyViolationError",
    "Base",
    "CanonicalFieldProvenance",
    "CanonicalLeadRow",
    "ContributionField",
    "IdentityKey",
    "IngestionRun",
    "LeadIdentity",
    "RawResponse",
    "SourceContribution",
    "SourceRun",
]


class AppendOnlyViolationError(Exception):
    """An update or delete of an append-only table was attempted."""


def _utc(**kw: Any) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), **kw)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Base(DeclarativeBase):
    """Declarative base for every Lead Store table."""

    type_annotation_map = {  # noqa: RUF012
        dict[str, Any]: JSON,
        list[Any]: JSON,
    }


class IngestionRun(Base):
    __tablename__ = "ingestion_run"

    id: Mapped[uuid.UUID] = _uuid_pk()
    started_at: Mapped[datetime] = _utc()
    finished_at: Mapped[datetime | None] = _utc(nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pool_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class SourceRun(Base):
    __tablename__ = "source_run"
    __table_args__ = (
        Index("ix_source_run_run_id_source_name", "run_id", "source_name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_run.id"))
    source_name: Mapped[str] = mapped_column(String(64))
    resolved_mode: Mapped[str] = mapped_column(String(16))
    mode_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    live_access: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    credential_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    leads_found: Mapped[int] = mapped_column(Integer, default=0)
    contributions_written: Mapped[int] = mapped_column(Integer, default=0)
    failure_class: Mapped[str | None] = mapped_column(String(128), nullable=True)
    throttle_waits: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    http_429_count: Mapped[int] = mapped_column(Integer, default=0)
    credits_consumed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quota_remaining: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    warnings: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)


class RawResponse(Base):
    """Raw provider payloads live only here (9.8).

    ``payload`` is deferred, so even a direct ``select(RawResponse)`` does not load it;
    the only intended access is ``store.raw_responses.RawResponseRepository``, which
    also owns retention and purge. Nothing else may name this model.
    """

    __tablename__ = "raw_response"
    __table_args__ = (Index("ix_raw_response_retention_until", "retention_until"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_run.id"))
    endpoint_key: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(128))
    payload: Mapped[Any] = mapped_column(JSON, deferred=True)
    fetched_at: Mapped[datetime] = _utc()
    retention_until: Mapped[datetime | None] = _utc(nullable=True)


class LeadIdentity(Base):
    """Cluster root; a ``CanonicalLeadRow`` is a projection of it."""

    __tablename__ = "lead_identity"

    id: Mapped[uuid.UUID] = _uuid_pk()
    created_at: Mapped[datetime] = _utc()
    primary_key_type: Mapped[str | None] = mapped_column(String(32), nullable=True)


class IdentityKey(Base):
    """A Match Key. UNIQUE(key_type, key_value) is the dedupe mechanism (8.9)."""

    __tablename__ = "identity_key"
    __table_args__ = (
        UniqueConstraint("key_type", "key_value", name="uq_identity_key_type_value"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lead_identity.id"))
    key_type: Mapped[str] = mapped_column(String(32))
    key_value: Mapped[str] = mapped_column(String(512))


class SourceContribution(Base):
    """Append-only: one source's view of one lead."""

    __tablename__ = "source_contribution"
    __table_args__ = (
        Index("ix_source_contribution_lead_identity_id", "lead_identity_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_run.id"))
    lead_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_identity.id"), nullable=True
    )
    raw_response_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_response.id"))
    source_name: Mapped[str] = mapped_column(String(64))
    data_mode: Mapped[str] = mapped_column(String(16))
    fetched_at: Mapped[datetime] = _utc()
    lead_scope: Mapped[str] = mapped_column(String(32))


class ContributionField(Base):
    """Append-only: one provenance-tagged field value inside a contribution.

    ``value`` is JSON, so untrusted text has no byte limit to size against (a
    4000-character bound can reach 16000 UTF-8 bytes).
    """

    __tablename__ = "contribution_field"
    __table_args__ = (
        Index(
            "ix_contribution_field_contribution_id_path",
            "contribution_id",
            "canonical_path",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    contribution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_contribution.id")
    )
    canonical_path: Mapped[str] = mapped_column(String(255))
    value: Mapped[Any] = mapped_column(JSON)
    raw_field_path: Mapped[str] = mapped_column(String(512))
    # NULL when the provider stated no certainty (confidence_origin "none").
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The classification lives in these three columns, never inside ``value``:
    # ``untrusted`` marks provider free text, ``truncated`` and ``original_length``
    # describe any cut to the configured bound (``original_length`` is NULL for
    # trusted values). ``value`` holds the text verbatim as a plain JSON string.
    untrusted: Mapped[bool] = mapped_column(Boolean, default=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    original_length: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CanonicalLeadRow(Base):
    """Derived projection; fully recomputable from the contribution log."""

    __tablename__ = "canonical_lead"

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_identity.id"), unique=True
    )
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    employments: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    tech_signals: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    intent_signals: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    contributing_sources: Mapped[list[Any]] = mapped_column(JSON)
    computed_at: Mapped[datetime] = _utc()
    projection_version: Mapped[int] = mapped_column(Integer)


class CanonicalFieldProvenance(Base):
    """Derived: which contribution field won a canonical path, and which lost."""

    __tablename__ = "canonical_field_provenance"

    id: Mapped[uuid.UUID] = _uuid_pk()
    canonical_lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("canonical_lead.id")
    )
    canonical_path: Mapped[str] = mapped_column(String(255))
    winning_field_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contribution_field.id")
    )
    agreeing_source_count: Mapped[int] = mapped_column(Integer)
    superseded_field_ids: Mapped[list[Any]] = mapped_column(JSON)


_APPEND_ONLY = (SourceContribution, ContributionField)
_APPEND_ONLY_TABLES = frozenset(c.__tablename__ for c in _APPEND_ONLY)


def _refuse_row_change(mapper: Mapper[Any], connection: Any, target: Any) -> None:
    raise AppendOnlyViolationError(
        f"{type(target).__name__} is append-only: no update or delete (8.12)"
    )


for _model in _APPEND_ONLY:
    event.listen(_model, "before_update", _refuse_row_change)
    event.listen(_model, "before_delete", _refuse_row_change)


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_change(state: ORMExecuteState) -> None:
    if not (state.is_update or state.is_delete):
        return
    name = getattr(getattr(state.statement, "table", None), "name", None)
    if name in _APPEND_ONLY_TABLES:
        raise AppendOnlyViolationError(
            f"{name} is append-only: no bulk update or delete (8.12)"
        )
