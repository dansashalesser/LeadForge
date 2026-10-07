"""Raw provider payload retention and the only access path to it (task 6.5, 9.8).

Raw payloads live only in the ``raw_response`` table, never on canonical tables, and are
reached only through `RawResponseRepository`: no canonical model has a relationship to
``RawResponse``, its ``payload`` column is deferred, and a structural test pins that
no other module names the model.

Retention: indefinite (``retention_until`` NULL) for synthetic data, and
``fetched_at + live_window`` for live data (default 30 days, configurable). A row is
expired when ``retention_until <= now``: at the expiry instant it is already gone.

Time handling is portable by normalising in Python, with no backend branch. Every
instant is converted to aware UTC before it is bound, so the stored wall-clock value
is UTC on any engine (SQLite drops the offset and returns naive values; Postgres keeps
it), and the purge compares like with like. Values read back are re-tagged as UTC.
Naive inputs are refused rather than guessed at.

The clock is a parameter (`purge_expired(now=...)`), never read here.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.store.models import RawResponse, SourceContribution

__all__ = [
    "DEFAULT_LIVE_RETENTION",
    "RAW_RETENTION_DAYS_ENV",
    "PurgeResult",
    "RawResponseRepository",
    "RetentionConfigError",
    "RetentionPolicy",
]

DEFAULT_LIVE_RETENTION = timedelta(days=30)
RAW_RETENTION_DAYS_ENV = "RAW_RETENTION_DAYS"


class RetentionConfigError(ValueError):
    """The retention configuration is unusable."""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must carry a timezone (naive datetimes are refused)")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class RetentionPolicy:
    """How long raw payloads are kept. Synthetic data is always kept."""

    live_window: timedelta = DEFAULT_LIVE_RETENTION

    def __post_init__(self) -> None:
        if self.live_window <= timedelta(0):
            raise RetentionConfigError("live retention window must be positive")

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> "RetentionPolicy":
        """Window in whole days from ``RAW_RETENTION_DAYS``; unset or blank: 30."""
        raw = environ.get(RAW_RETENTION_DAYS_ENV, "").strip()
        if not raw:
            return cls()
        try:
            days = int(raw)
        except ValueError:
            raise RetentionConfigError(
                f"{RAW_RETENTION_DAYS_ENV} must be a whole number of days"
            ) from None
        if days <= 0:
            raise RetentionConfigError(f"{RAW_RETENTION_DAYS_ENV} must be positive")
        return cls(live_window=timedelta(days=days))

    def retention_until(self, mode: DataMode, fetched_at: datetime) -> datetime | None:
        """Expiry instant (aware UTC), or None for indefinite retention."""
        fetched = _as_utc(fetched_at)
        if mode is DataMode.SYNTHETIC:
            return None
        return fetched + self.live_window


@dataclass(frozen=True)
class PurgeResult:
    deleted: int
    # Contributions whose raw payload was deleted and whose link the database set to
    # NULL (ON DELETE SET NULL). The contributions themselves are kept (8.12).
    detached_contributions: int


class RawResponseRepository:
    """Explicit access to raw payloads. All methods take the caller's session.

    Write paths are meant to run inside ``StoreWriter.write_batch``: plain return
    values, no commit.
    """

    @staticmethod
    def add(
        session: Session,
        *,
        source_run_id: uuid.UUID,
        endpoint_key: str,
        request_fingerprint: str,
        payload: Any,
        fetched_at: datetime,
        mode: DataMode,
        policy: RetentionPolicy,
    ) -> uuid.UUID:
        fetched = _as_utc(fetched_at)
        row = RawResponse(
            source_run_id=source_run_id,
            endpoint_key=endpoint_key,
            request_fingerprint=request_fingerprint,
            payload=payload,
            fetched_at=fetched,
            retention_until=policy.retention_until(mode, fetched),
        )
        session.add(row)
        session.flush()
        return row.id

    @staticmethod
    def get_payload(session: Session, response_id: uuid.UUID) -> Any | None:
        return session.scalar(
            sa.select(RawResponse.payload).where(RawResponse.id == response_id)
        )

    @staticmethod
    def retention_until(session: Session, response_id: uuid.UUID) -> datetime | None:
        value = session.scalar(
            sa.select(RawResponse.retention_until).where(RawResponse.id == response_id)
        )
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else _as_utc(value)

    @staticmethod
    def purge_expired(session: Session, *, now: datetime) -> PurgeResult:
        """Delete rows with ``retention_until <= now``; never rows with NULL.

        A contribution that references a deleted row is kept and detached: the
        database sets its ``raw_response_id`` to NULL (ON DELETE SET NULL), so no
        contribution row is updated through the ORM (append-only, 8.12). That relies
        on the engine enforcing foreign keys; `create_store_engine` turns SQLite's
        enforcement on, and PostgreSQL always enforces them. Contributions already
        loaded in the session are expired so they re-read the detached link.
        """
        cutoff = _as_utc(now)
        expired = sa.and_(
            RawResponse.retention_until.is_not(None),
            RawResponse.retention_until <= cutoff,
        )
        session.flush()
        deleted = (
            session.scalar(
                sa.select(sa.func.count()).select_from(RawResponse).where(expired)
            )
            or 0
        )
        detached = (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(SourceContribution)
                .where(
                    SourceContribution.raw_response_id.in_(
                        sa.select(RawResponse.id).where(expired)
                    )
                )
            )
            or 0
        )
        if deleted:
            session.execute(sa.delete(RawResponse).where(expired))
            for obj in list(session.identity_map.values()):
                if isinstance(obj, SourceContribution):
                    session.expire(obj)
        return PurgeResult(deleted=deleted, detached_contributions=detached)
