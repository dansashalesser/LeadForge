"""Write-transaction scoping for the Lead Store (task 6.4, requirement 9.6).

Three boundaries, per the design's "Transaction boundaries" table:

* the ``IngestionRun`` row is committed on its own at run start (`begin_run`), so it
  survives any later source failure;
* each source's contribution batch is exactly one transaction (`write_batch`): it
  commits whole or rolls back whole, and a failure there never touches the run row or
  another source's batch;
* that transaction scope is shielded from cancellation (``asyncio.timeout`` expiry or
  ``Task.cancel``), so a cancelled source is either fully written or fully rolled back.

Sync session in a worker thread, deliberately. The transports are async, but an async
SQLAlchemy engine needs a driver per backend (``aiosqlite``, ``asyncpg``), which would
put the backend name into the one place that must not know it (9.3, 9.4) and add
dependencies. A sync ``Session`` run through ``asyncio.to_thread`` keeps the engine
from ``create_store_engine`` as the only seam, and a thread cannot be interrupted
mid-flight, which is what makes the shield exact: once a batch has started it runs to
its own commit or rollback.

Cancellation semantics: when the awaiting task is cancelled, the batch is *not*
abandoned. It runs to completion (commit, or rollback if it raised), then
``CancelledError`` is re-raised so ``asyncio.timeout`` still turns it into
``TimeoutError`` and the caller still unwinds. The cancellation is never swallowed.
"""

import asyncio
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.store.models import IngestionRun

__all__ = ["StoreWriter"]


class StoreWriter:
    """Opens the store's write transactions. One instance per engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        # One write transaction at a time per store: an in-memory SQLite engine shares
        # a single connection, and file SQLite allows one writer anyway.
        self._write_lock = threading.Lock()

    async def begin_run(
        self,
        *,
        status: str,
        pool_size: int | None = None,
        config_snapshot: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """Insert and commit the run record, returning its id."""

        def write(session: Session) -> uuid.UUID:
            run = IngestionRun(
                started_at=datetime.now(UTC),
                status=status,
                pool_size=pool_size,
                config_snapshot=config_snapshot,
            )
            session.add(run)
            session.flush()
            return run.id

        return await self.write_batch(write)

    async def write_batch[T](self, write: Callable[[Session], T]) -> T:
        """Run `write` in one transaction, shielded from cancellation.

        `write` is synchronous and receives a fresh session already inside its
        transaction; it should not commit or roll back (a commit does not end the batch
        early; a rollback dooms it), and must return plain values, not ORM instances.
        Any exception, even a ``BaseException``, rolls it all back and propagates.
        """
        inner = asyncio.ensure_future(asyncio.to_thread(self._run, write))
        try:
            return await asyncio.shield(inner)
        except asyncio.CancelledError:
            if (
                inner.cancelled()
            ):  # defensive: to_thread futures are not cancelled by us
                raise
            # Cancelled while the batch is in flight: let it settle, then re-raise.
            await _settle(inner)
            raise

    def _run[T](self, write: Callable[[Session], T]) -> T:
        # The transaction belongs to the connection, not the session: with
        # ``rollback_only`` a ``session.commit()`` inside `write` cannot commit it
        # early (which would tear the batch), while a ``session.rollback()`` poisons it
        # so the batch cannot half-succeed. ``conn.begin()`` commits on success and
        # rolls back on any BaseException.
        with self._write_lock, self._engine.connect() as conn, conn.begin():
            with Session(
                bind=conn, expire_on_commit=False, join_transaction_mode="rollback_only"
            ) as session:
                result = write(session)
                session.flush()  # pending rows must not be dropped on close
            return result


async def _settle(future: "asyncio.Future[Any]") -> None:
    """Wait for `future` to finish, however many times we are cancelled meanwhile."""
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            continue
        except Exception:  # noqa: BLE001 - the batch's own error is rolled back already
            return
    if not future.cancelled():
        future.exception()  # mark retrieved; the cancellation is what the caller sees
