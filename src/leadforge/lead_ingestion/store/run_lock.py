"""One ingestion run at a time (follow-up 2026-10-06; Requirements 6.4, 8.12, 21.2).

Two runs saving the same record used to race: the later one aborted after it had
spent credits. A run now takes this lock BEFORE any provider call, and a second run
refuses to start (``RunInProgressError``) having called nothing.

Design (engine-neutral, one code path): ``run_lock`` has one row per lock name,
seeded free by migration 0007. Acquiring is ONE conditional UPDATE: set the holder
where the row is free or its lease expired, then read the row count. PostgreSQL
serialises concurrent UPDATEs of the row (the loser re-checks the condition on the
winner's committed row and matches nothing); SQLite serialises writers outright. No
advisory lock, no ``NOWAIT`` and no backend branch. Every function takes the
caller's session and does not commit; run each through its own
``StoreWriter.write_batch`` so the lock commits before the run goes on.

Provisional decisions:

* Stale lock: a holder that crashed never releases. The lease is the run timeout
  plus ``LOCK_STALE_GRACE_S`` (30 minutes) for persisting and merging; once
  ``expires_at`` has passed, the next run takes the lock over. The holder renews the
  lease in its merge transaction (``renew_run_lock``); a holder whose lock was taken
  over gets ``RunLockLostError`` there, so its merge is refused (its fetched records
  are already committed and the next run merges them). The renewal's row lock is
  held until the merge commits, so a takeover cannot interleave with it.
* The holder is a random uuid per run, never a run id or a host name. Messages carry
  only the lock's instants: no value of any record.
* Releasing a lock someone else holds is a no-op.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.store.models import RunLock

__all__ = [
    "LOCK_NAME",
    "LOCK_STALE_GRACE_S",
    "LockHeld",
    "RunInProgressError",
    "RunLockLostError",
    "release_run_lock",
    "renew_run_lock",
    "try_acquire_run_lock",
]

LOCK_NAME = "ingestion"
LOCK_STALE_GRACE_S = 30 * 60.0


class RunInProgressError(Exception):
    """Another ingestion run holds the lock; this run did not start."""

    def __init__(self, *, acquired_at: datetime | None, expires_at: datetime | None):
        self.acquired_at = acquired_at
        self.expires_at = expires_at
        super().__init__(
            "another run in progress: started "
            f"{_text(acquired_at)}, its lock is stale after {_text(expires_at)}; "
            "nothing was fetched or spent"
        )


class RunLockLostError(Exception):
    """This run's lock lease expired and another run took the lock over."""

    def __init__(self) -> None:
        super().__init__("the run lock was taken over by another run")


@dataclass(frozen=True)
class LockHeld:
    """The current holder's instants (aware UTC)."""

    acquired_at: datetime | None
    expires_at: datetime | None


def _text(value: datetime | None) -> str:
    return "unknown" if value is None else value.isoformat()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(
        UTC
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("a lock instant must carry a timezone")
    return value.astimezone(UTC)


def try_acquire_run_lock(
    session: Session, holder: uuid.UUID, *, now: datetime, lease_s: float
) -> LockHeld | None:
    """Take the lock for ``holder``: ``None`` when taken, else the current holder."""
    at = _utc(now)
    taken = session.execute(
        sa.update(RunLock)
        .where(
            RunLock.name == LOCK_NAME,
            sa.or_(RunLock.holder.is_(None), RunLock.expires_at < at),
        )
        .values(
            holder=holder, acquired_at=at, expires_at=at + timedelta(seconds=lease_s)
        )
        .execution_options(synchronize_session=False)
    )
    if taken.rowcount == 1:  # type: ignore[attr-defined]
        return None
    row = session.execute(
        sa.select(RunLock.acquired_at, RunLock.expires_at).where(
            RunLock.name == LOCK_NAME
        )
    ).first()
    if row is None:
        raise LookupError("the store has no run lock row: migrate it to head")
    return LockHeld(_aware(row[0]), _aware(row[1]))


def renew_run_lock(
    session: Session, holder: uuid.UUID, *, now: datetime, lease_s: float
) -> None:
    """Extend ``holder``'s lease; ``RunLockLostError`` when it no longer holds it."""
    at = _utc(now)
    renewed = session.execute(
        sa.update(RunLock)
        .where(RunLock.name == LOCK_NAME, RunLock.holder == holder)
        .values(expires_at=at + timedelta(seconds=lease_s))
        .execution_options(synchronize_session=False)
    )
    if renewed.rowcount != 1:  # type: ignore[attr-defined]
        raise RunLockLostError


def release_run_lock(session: Session, holder: uuid.UUID) -> None:
    """Free the lock if ``holder`` still holds it."""
    session.execute(
        sa.update(RunLock)
        .where(RunLock.name == LOCK_NAME, RunLock.holder == holder)
        .values(holder=None, acquired_at=None, expires_at=None)
        .execution_options(synchronize_session=False)
    )
