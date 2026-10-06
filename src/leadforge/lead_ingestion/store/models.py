"""Typed ORM schema for the Lead Store (design.md, Logical Data Model).

Column types are the engine-portable set only: ``Uuid``, ``String``, ``Integer``,
``Float``, ``Boolean``, ``JSON`` and ``DateTime(timezone=True)``. There is no
dialect type, no dialect-conditional branch, no ``server_default`` and no raw SQL
here; the schema reaches a database through migrations (task 6.2).

``SourceContribution``, ``ContributionField`` and ``ContributionAbsence`` are
append-only (8.12), as are the tie resolutions and the lead succession log. Every
ORM-level update or delete of them, per-object or bulk, raises
``AppendOnlyViolationError``; the guard is registered when this module is imported.
Which Lead a contribution belongs to is DERIVED (``ContributionLead``, 0006): every
merge rebuilds it, so a cluster that splits or joins never relinks a contribution.
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
    "ColumnValueError",
    "ContributionAbsence",
    "ContributionField",
    "ContributionLead",
    "IdentityKey",
    "IngestionRun",
    "LeadIdentity",
    "LeadSuccession",
    "PrimaryDomainTieResolution",
    "RawResponse",
    "RunLock",
    "SourceContribution",
    "SourceRun",
    "StoreSecret",
]


class AppendOnlyViolationError(Exception):
    """An update or delete of an append-only table was attempted."""


class ColumnValueError(ValueError):
    """A String column value that the supported engines would not treat alike.

    The message names the table and column, never the value (it may be untrusted).
    """


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
    # Why an aborted run ended (0005): a stage and an exception class, never a value.
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The projection stamp the run wrote its canonical leads under (0005, 8.13): the
    # version and the keyed basis fingerprint (64 hex), and how many primary-domain
    # ties fell back to the flagged lowest-sorted candidate (8.18). NULL: not recorded.
    projection_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    projection_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    primary_domain_ties_flagged: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # Active canonical leads the run's merge wrote, and leads it retired (0006).
    leads_merged: Mapped[int | None] = mapped_column(Integer, nullable=True)
    leads_retired: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Clusters the run re-projected, and how (0007): ``incremental`` or ``full: <why>``.
    clusters_reprojected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reprojection: Mapped[str | None] = mapped_column(String(64), nullable=True)


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
    # Calls and records of the source (0005); NULL: not recorded (a run before 0005,
    # or one that never completed), never 0.
    attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    succeeded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    records_fetched: Mapped[int | None] = mapped_column(Integer, nullable=True)


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
    # An opaque random value since 0007: nothing looks a payload up by it.
    request_fingerprint: Mapped[str] = mapped_column(String(128))
    payload: Mapped[Any] = mapped_column(JSON, deferred=True)
    fetched_at: Mapped[datetime] = _utc()
    retention_until: Mapped[datetime | None] = _utc(nullable=True)


class LeadIdentity(Base):
    """Cluster root; a ``CanonicalLeadRow`` is a projection of it.

    ``retired_at`` is set when a merge no longer produces this lead (its cluster split
    or was absorbed, 0006); ``LeadSuccession`` points at what replaced it. A retired
    identity keeps its rows and is never active again.
    """

    __tablename__ = "lead_identity"

    id: Mapped[uuid.UUID] = _uuid_pk()
    created_at: Mapped[datetime] = _utc()
    primary_key_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retired_at: Mapped[datetime | None] = _utc(nullable=True)


class LeadSuccession(Base):
    """Append-only: a retired lead and one lead that replaced it (0006)."""

    __tablename__ = "lead_succession"
    __table_args__ = (
        UniqueConstraint(
            "predecessor_id", "successor_id", name="uq_lead_succession_pair"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    predecessor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lead_identity.id"))
    successor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lead_identity.id"))
    recorded_at: Mapped[datetime] = _utc()


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
    """Append-only: one source's view of one lead.

    Its lead is the derived ``ContributionLead`` (0007 dropped the first-seen
    ``lead_identity_id``: a contribution is stored before any lead exists).
    ``content_sha`` (0006) is the contribution's identity, keyed under the store's
    key since 0007 (``store.store_key``): the same observation is stored once. NULL on
    rows written before 0006.
    """

    __tablename__ = "source_contribution"
    __table_args__ = (
        UniqueConstraint("content_sha", name="uq_source_contribution_content_sha"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_run.id"))
    # NULL once the raw payload expired and was purged (9.8): the database detaches the
    # row (ON DELETE SET NULL), so the append-only guard never sees an UPDATE.
    raw_response_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_response.id", ondelete="SET NULL"), nullable=True
    )
    source_name: Mapped[str] = mapped_column(String(64))
    data_mode: Mapped[str] = mapped_column(String(16))
    fetched_at: Mapped[datetime] = _utc()
    lead_scope: Mapped[str] = mapped_column(String(32))
    content_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)


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
    # The rest of the field's provenance (0006), so a stored contribution reads back
    # whole. NULL on rows written before 0006.
    confidence_origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence_scale: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ContributionAbsence(Base):
    """Append-only: a source's explicit non-answer for one path (0006)."""

    __tablename__ = "contribution_absence"
    __table_args__ = (
        Index("ix_contribution_absence_contribution_id", "contribution_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    contribution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_contribution.id")
    )
    canonical_path: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))
    raw_field_path: Mapped[str | None] = mapped_column(String(512), nullable=True)


class ContributionLead(Base):
    """Derived: the lead a contribution belongs to now; rebuilt by every merge.

    One row per contribution (it is the primary key), so a contribution is never in
    two leads; only active leads are named.
    """

    __tablename__ = "contribution_lead"
    __table_args__ = (
        Index("ix_contribution_lead_lead_identity_id", "lead_identity_id"),
    )

    contribution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_contribution.id"), primary_key=True
    )
    lead_identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lead_identity.id"))


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
    # The display primary domain and how it was decided (8.17, 8.18; 0006), and the
    # keyed basis fingerprint the row was projected under (8.13). NULL before 0006.
    primary_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_domain_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    projection_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )


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


class PrimaryDomainTieResolution(Base):
    """Append-only: the stored answer to one exact primary-domain tie (8.18).

    ``tie_key`` is the sha256 of the company's domain set and the candidate set (see
    ``tie_resolution.tie_key``); the projection reads this row and never calls a model.
    """

    __tablename__ = "primary_domain_tie_resolution"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tie_key: Mapped[str] = mapped_column(String(64), unique=True)
    chosen_domain: Mapped[str] = mapped_column(String(255))
    candidates: Mapped[list[Any]] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    resolved_at: Mapped[datetime] = _utc()


class RunLock(Base):
    """The one-run-at-a-time lock (0007): one row per lock name, seeded by migration.

    ``holder`` is the run's random token (NULL: free); a holder whose ``expires_at``
    passed is stale and may be taken over (``store.run_lock``).
    """

    __tablename__ = "run_lock"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    holder: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    acquired_at: Mapped[datetime | None] = _utc(nullable=True)
    expires_at: Mapped[datetime | None] = _utc(nullable=True)


class StoreSecret(Base):
    """The store's own random HMAC key (0007), written once by migration.

    ``content_sha`` is keyed by it (``store.store_key``), so no plain hash of
    personal data is stored. Never logged or reported.
    """

    __tablename__ = "store_secret"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str] = mapped_column(String(128))


def _check_string_columns(mapper: Mapper[Any], connection: Any, target: Any) -> None:
    """Refuse String values that only some engines would refuse (task 6.7).

    SQLite ignores ``String(n)`` and stores NUL and, through Python, refuses lone
    surrogates; PostgreSQL enforces ``n`` and rejects NUL. Checking here, from the
    column metadata and with no backend branch, makes every engine behave like the
    strictest one, so a value that passes on the default SQLite file cannot fail
    later on Postgres. JSON columns are not affected: both engines keep NUL and
    lone surrogates there as escapes.
    """
    for attr in mapper.column_attrs:
        column = attr.columns[0]
        if not isinstance(column.type, String):
            continue
        value = getattr(target, attr.key, None)
        if not isinstance(value, str):
            continue
        where = f"{mapper.class_.__tablename__}.{column.name}"
        limit = column.type.length
        if limit is not None and len(value) > limit:
            raise ColumnValueError(f"{where} is longer than its {limit} characters")
        if "\x00" in value:
            raise ColumnValueError(f"{where} contains a NUL character")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ColumnValueError(
                f"{where} contains a lone surrogate (not valid UTF-8 text)"
            ) from None


event.listen(Base, "before_insert", _check_string_columns, propagate=True)
event.listen(Base, "before_update", _check_string_columns, propagate=True)

_APPEND_ONLY = (
    SourceContribution,
    ContributionField,
    ContributionAbsence,
    PrimaryDomainTieResolution,
    LeadSuccession,
)
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
