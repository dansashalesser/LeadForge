"""The store-backed ``RunRecorder`` the orchestrator is handed (task 18.1).

Requirement 21.1. ``IngestionOrchestrator`` knows only the ``RunRecorder`` protocol;
this is the implementation that writes through ``StoreWriter.write_batch``. It does not
use ``StoreWriter.begin_run``: that stamps ``datetime.now`` itself (the clock must be
injectable) and cannot write the per-source mode rows in the same transaction. The
start write is one committed transaction, so it is durable before any source is called
and independent of every source's own batch (6.4).

A record still ``running`` after its process is gone is a crash. No sweep resolves it
yet; it stays visible as ``running`` rather than being guessed at.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import LiveAccess
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import SourceResult
from leadforge.lead_ingestion.registry import SourceSettings
from leadforge.lead_ingestion.run_exit import map_run_exit
from leadforge.lead_ingestion.run_record import (
    RunStatus,
    build_run_record,
    build_source_counts,
)
from leadforge.lead_ingestion.store.run_records import RunRecordRepository
from leadforge.lead_ingestion.store.transactions import StoreWriter

__all__ = ["StoreRunRecorder"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class StoreRunRecorder:
    def __init__(
        self,
        writer: StoreWriter,
        *,
        clock: Callable[[], datetime] = _utc_now,
        global_mode: DataMode | None = None,
    ) -> None:
        self._writer = writer
        self._clock = clock
        self._global_mode = global_mode

    async def start(
        self,
        resolutions: Mapping[str, ModeResolution],
        settings: Mapping[str, SourceSettings],
        *,
        pool_size: int,
        run_timeout_s: float,
        live_access: Mapping[str, LiveAccess],
    ) -> uuid.UUID:
        record = build_run_record(
            resolutions,
            settings,
            started_at=self._clock(),
            max_concurrent_sources=pool_size,
            run_timeout_s=run_timeout_s,
            global_mode=self._global_mode,
            live_access=live_access,
        )
        return await self._writer.write_batch(
            lambda session: RunRecordRepository(session).start(record)
        )

    async def finish(
        self, run_id: uuid.UUID, results: tuple[SourceResult, ...] | None
    ) -> None:
        """Complete the record: ``results`` is ``None`` when the run was aborted.

        A completed run's per-source counts are written in the same transaction as the
        finish (Requirement 21.2): both commit or neither does. An aborted run has no
        results, so its source rows keep their start values (no counts, no class).
        """
        finished_at = self._clock()
        if results is None:
            status, exit_code = RunStatus.ABORTED, None
            counts = None
        else:
            status, exit_code = RunStatus.COMPLETED, map_run_exit(results).exit_code
            counts = build_source_counts(results)

        def write(session: Session) -> None:
            repo = RunRecordRepository(session)
            repo.finish(
                run_id, status=status, exit_code=exit_code, finished_at=finished_at
            )
            if counts is not None:
                repo.record_source_counts(run_id, counts)

        await self._writer.write_batch(write)
