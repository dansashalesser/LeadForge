"""Persist and read the ingestion run record (task 18.1; Requirement 21.1).

``RunRecordRepository`` takes the caller's session and does not commit; run it through
``StoreWriter.write_batch`` so the write is one shielded transaction. ``start`` writes
the ``ingestion_run`` row and one ``source_run`` row per enabled source (name, resolved
mode, reason) together, so a run never exists without its modes. ``finish`` is the only
update: it sets ``status``, ``exit_code`` and ``finished_at`` once, and refuses a second
completion, an unknown run, or a status and exit code that contradict each other. The
``record_source_counts`` (task 18.2) is the only other write: it sets each source's
figures on its own ``source_run`` row, and refuses a source the run never listed. Call
it in the same ``write_batch`` as ``finish`` so a run is never finished without its
counts, nor counted without being finished.
Instants are normalised to aware UTC before binding and re-tagged UTC on read, as
``tie_resolutions`` does.
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.run_record import (
    RunRecord,
    RunRecordError,
    RunStatus,
    SourceCounts,
    SourceMode,
    StoredRun,
)
from leadforge.lead_ingestion.store.models import IngestionRun, SourceRun

__all__ = ["RunRecordRepository"]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _aware_utc(value: datetime, what: str) -> datetime:
    # A naive instant would be read as local time by astimezone: refuse it instead.
    if value.tzinfo is None:
        raise RunRecordError(f"{what} must carry a timezone")
    return value.astimezone(UTC)


class RunRecordRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def start(self, record: RunRecord) -> uuid.UUID:
        """Insert the running record and every source's mode; return the run id."""
        run = IngestionRun(
            started_at=_aware_utc(record.started_at, "started_at"),
            status=RunStatus.RUNNING.value,
            pool_size=record.pool_size,
            config_snapshot=record.config_snapshot,
        )
        self._session.add(run)
        self._session.flush()
        for source in record.sources:
            self._session.add(
                SourceRun(
                    run_id=run.id,
                    source_name=source.source_name,
                    resolved_mode=source.mode.value,
                    mode_reason=source.reason,
                    live_access=source.live_access,
                )
            )
        self._session.flush()
        return run.id

    def finish(
        self,
        run_id: uuid.UUID,
        *,
        status: RunStatus,
        exit_code: int | None,
        finished_at: datetime,
    ) -> None:
        """Complete a running record once. Raises ``RunRecordError`` otherwise."""
        if status is RunStatus.RUNNING:
            raise RunRecordError("a run is finished as completed or aborted")
        if status is RunStatus.COMPLETED and exit_code is None:
            raise RunRecordError("a completed run has an exit code")
        if status is RunStatus.ABORTED and exit_code is not None:
            raise RunRecordError("an aborted run has no exit code")
        done_at = _aware_utc(finished_at, "finished_at")
        # One conditional UPDATE, not read-then-write: two finishers (two processes on
        # PostgreSQL) cannot both see ``running`` and both complete it.
        updated = self._session.execute(
            sa.update(IngestionRun)
            .where(
                IngestionRun.id == run_id,
                IngestionRun.status == RunStatus.RUNNING.value,
            )
            .values(status=status.value, exit_code=exit_code, finished_at=done_at)
            .execution_options(synchronize_session="fetch")
        )
        if updated.rowcount == 1:  # type: ignore[attr-defined]
            return
        if self._session.get(IngestionRun, run_id, populate_existing=True) is None:
            raise RunRecordError("unknown run")
        raise RunRecordError("run is already finished")

    def record_source_counts(
        self, run_id: uuid.UUID, counts: tuple[SourceCounts, ...]
    ) -> None:
        """Write each source's figures to its row. Raises ``RunRecordError`` for a
        source the run did not list."""
        for c in counts:
            updated = self._session.execute(
                sa.update(SourceRun)
                .where(
                    SourceRun.run_id == run_id, SourceRun.source_name == c.source_name
                )
                .values(
                    failure_class=c.failure_class,
                    leads_found=c.leads_found,
                    retries=c.retries,
                    throttle_waits=c.throttle_waits,
                    http_429_count=c.http_429_count,
                    quota_remaining=c.quota_remaining,
                    warnings=c.warnings,
                )
                .execution_options(synchronize_session="fetch")
            )
            if updated.rowcount != 1:  # type: ignore[attr-defined]
                raise RunRecordError("unknown source for this run")

    def get(self, run_id: uuid.UUID) -> StoredRun | None:
        run = self._session.get(IngestionRun, run_id)
        if run is None:
            return None
        rows = self._session.scalars(
            sa.select(SourceRun)
            .where(SourceRun.run_id == run_id)
            .order_by(SourceRun.source_name)
        ).all()
        return StoredRun(
            run_id=run.id,
            started_at=_as_utc(run.started_at),
            finished_at=None if run.finished_at is None else _as_utc(run.finished_at),
            status=run.status,
            exit_code=run.exit_code,
            pool_size=run.pool_size,
            config_snapshot=run.config_snapshot,
            sources=tuple(
                SourceMode(
                    r.source_name,
                    DataMode(r.resolved_mode),
                    r.mode_reason or "",
                    r.live_access,
                )
                for r in rows
            ),
        )
