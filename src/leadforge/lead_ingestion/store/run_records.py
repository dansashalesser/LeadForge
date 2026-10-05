"""Persist and read the ingestion run record (task 18.1; Requirement 21.1).

``RunRecordRepository`` takes the caller's session and does not commit; run it through
``StoreWriter.write_batch`` so the write is one shielded transaction. ``start`` writes
the ``ingestion_run`` row and one ``source_run`` row per enabled source (name, resolved
mode, reason) together, so a run never exists without its modes. ``finish`` is the only
update: it sets ``status``, ``exit_code`` and ``finished_at`` once, and refuses a second
completion, an unknown run, or a status and exit code that contradict each other. The
``source_run`` rows are not touched after ``start`` here; their counts are task 18.2.
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
                    r.source_name, DataMode(r.resolved_mode), r.mode_reason or ""
                )
                for r in rows
            ),
        )
