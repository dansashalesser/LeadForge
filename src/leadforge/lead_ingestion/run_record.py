"""The run record and the builder that makes it from resolved modes (task 18.1).

Requirement 21.1: when an ingestion run starts, one persisted record carries the run
identifier, the start time and the resolved mode of every enabled source. The pure
``build_run_record`` assembles it from what the orchestrator already holds before any
slot is taken; ``store.run_records.RunRecordRepository`` persists it.

Provisional decisions (see choices.md, task 18.1):

* Status vocabulary: ``running`` from the start, then ``completed`` (``run`` returned
  and an exit code was mapped, whatever its value: a run whose every source failed is
  completed with exit code 1) or ``aborted`` (``run`` ended by an exception or by
  cancellation, so there is no exit code). A record still ``running`` after its process
  is gone is a crash; nothing else is ever left ambiguous.
* The configuration snapshot holds the pool bound, the run timeout, the global mode
  override and, per enabled source, its trust rank and its mode and live-access
  overrides. It is built from typed settings only: no environment value, path or
  credential is read into it. Env var *names* appear only in a mode reason.
* A mode reason is cut to its column (255 characters, ending in an ellipsis so the cut
  shows) so a long variable list cannot make the start-of-run write fail; reasons name
  variables, never values (8.1). Sources are listed by name, as ``get`` returns them.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceSettings

__all__ = [
    "MAX_REASON_CHARS",
    "RunRecord",
    "RunRecordError",
    "RunStatus",
    "SourceMode",
    "StoredRun",
    "build_run_record",
]

MAX_REASON_CHARS = 255  # source_run.mode_reason


class RunRecordError(Exception):
    """A run record was started, finished or read in a way that is not allowed."""


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"


@dataclass(frozen=True)
class SourceMode:
    """One enabled source's resolved mode and why."""

    source_name: str
    mode: DataMode
    reason: str


@dataclass(frozen=True)
class RunRecord:
    """What is written at run start."""

    started_at: datetime
    pool_size: int
    config_snapshot: dict[str, Any]
    sources: tuple[SourceMode, ...]


@dataclass(frozen=True)
class StoredRun:
    """A run record as read back from the store."""

    run_id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    status: str
    exit_code: int | None
    pool_size: int | None
    config_snapshot: dict[str, Any] | None
    sources: tuple[SourceMode, ...]


def _cut(reason: str) -> str:
    """Cut to the column by characters (never half a character), marking the cut."""
    if len(reason) <= MAX_REASON_CHARS:
        return reason
    return reason[: MAX_REASON_CHARS - 1] + "…"


def build_run_record(
    resolutions: Mapping[str, ModeResolution],
    settings: Mapping[str, SourceSettings],
    *,
    started_at: datetime,
    max_concurrent_sources: int,
    run_timeout_s: float,
    global_mode: DataMode | None,
) -> RunRecord:
    """Assemble the start-of-run record. Pure: no I/O, no environment read."""
    if started_at.tzinfo is None:
        raise ValueError("started_at must carry a timezone")
    missing = [name for name in resolutions if name not in settings]
    if missing:
        raise ValueError(f"no settings for enabled source: {', '.join(missing)}")
    for name, r in resolutions.items():
        if not isinstance(r.mode, DataMode):
            raise ValueError(f"unknown data mode for source {name}: {r.mode!r}")
    ordered = sorted(resolutions)
    sources = {}
    for name in ordered:
        s = settings[name]
        sources[name] = {
            "trust_rank": s.trust_rank,
            "mode_override": None if s.mode is None else s.mode.value,
            "live_access_override": (
                None if s.live_access is None else s.live_access.value
            ),
        }
    return RunRecord(
        started_at=started_at,
        pool_size=max_concurrent_sources,
        config_snapshot={
            "max_concurrent_sources": max_concurrent_sources,
            "run_timeout_s": run_timeout_s,
            "global_mode": None if global_mode is None else global_mode.value,
            "sources": sources,
        },
        sources=tuple(
            SourceMode(name, resolutions[name].mode, _cut(resolutions[name].reason))
            for name in ordered
        ),
    )
