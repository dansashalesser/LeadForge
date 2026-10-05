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

Per-source counts (task 18.2; Requirement 21.2). Provisional decisions (choices.md,
18.2):

* ``live_access`` (boolean column) is whether the source's effective classification (the
  configured override, else the adapter's declaration) is anything but ``unavailable``:
  a gated source "could run live", only an unavailable one is synthetic-only by
  necessity. Being boolean it cannot tell gated from available, so the three-valued
  classification is also kept in ``config_snapshot["sources"][name]["live_access"]``
  (3.6). Both are written at start with the modes. ``credential_present`` stays
  unset: the orchestrator reads no environment.
* ``leads_found`` is the number of Leads normalized (contributions) across the source's
  phases. The schema has no column for raw records fetched or for Leads merged into
  existing records, and ``run`` merges nothing; neither is invented.
  ``contributions_written`` is not touched: ``run`` persists no contribution.
* ``failure_class`` is the source's final ``SourceStatus`` value, or ``None`` when it
  ended ``ok``. A source with no result at all (Enrichment with an empty work list)
  keeps the start row's zeros and no class.
* ``retries`` is the call ledger's count, ``throttle_waits`` and ``http_429_count`` come
  from the source's throttle snapshot (zero for a synthetic source, which has none).
  ``quota_remaining`` is only what the provider itself stated (per-window response
  headers, 12.5; last response) and ``NULL`` when none was: the local limiter's tokens
  are never reported as a quota. Only an adapter exposing ``allowances`` reports any.
  ``credits_consumed`` stays ``NULL``: no adapter reports Credits to the run.
* ``warnings`` holds the source's PII-safe outcome message when it has one, nothing
  else; a person named by an error never reaches it (``SourceOutcome.error``).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from leadforge.lead_ingestion.base_source import LiveAccess
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import SourceResult, SourceStatus
from leadforge.lead_ingestion.registry import SourceSettings

__all__ = [
    "MAX_REASON_CHARS",
    "RunRecord",
    "RunRecordError",
    "RunStatus",
    "SourceCounts",
    "SourceMode",
    "StoredRun",
    "build_run_record",
    "build_source_counts",
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
    live_access: bool | None = None


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


@dataclass(frozen=True)
class SourceCounts:
    """One source's figures for a finished run, as written to its ``source_run`` row."""

    source_name: str
    failure_class: str | None
    leads_found: int
    retries: int
    throttle_waits: int
    http_429_count: int
    quota_remaining: dict[str, int] | None
    warnings: list[str] | None


def build_source_counts(results: tuple[SourceResult, ...]) -> tuple[SourceCounts, ...]:
    """One ``SourceCounts`` per source, ordered by name. Pure.

    A source run in both phases has one result per phase, each carrying the cumulative
    ledger and throttle figures, so the later result is the whole run's; Leads are
    summed over the phases.
    """
    latest: dict[str, SourceResult] = {}
    leads: dict[str, int] = {}
    for r in results:
        latest[r.outcome.source_name] = r
        leads[r.outcome.source_name] = leads.get(r.outcome.source_name, 0) + len(
            r.contributions or ()
        )
    counts = []
    for name in sorted(latest):
        r = latest[name]
        status = r.outcome.status
        counts.append(
            SourceCounts(
                source_name=name,
                failure_class=None if status is SourceStatus.OK else status.value,
                leads_found=leads[name],
                retries=r.outcome.retries,
                throttle_waits=0 if r.throttle is None else r.throttle.throttle_waits,
                http_429_count=(
                    0 if r.throttle is None else r.throttle.throttled_responses
                ),
                quota_remaining=dict(r.allowances) if r.allowances else None,
                warnings=None if r.outcome.error is None else [r.outcome.error],
            )
        )
    return tuple(counts)


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
    live_access: Mapping[str, LiveAccess] | None = None,
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
        # The effective three-valued classification (3.6): the boolean column cannot
        # tell a gated source from an available one.
        if live_access is not None and name in live_access:
            sources[name]["live_access"] = live_access[name].value
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
            SourceMode(
                name,
                resolutions[name].mode,
                _cut(resolutions[name].reason),
                (
                    None
                    if live_access is None or name not in live_access
                    else live_access[name] is not LiveAccess.UNAVAILABLE
                ),
            )
            for name in ordered
        ),
    )
