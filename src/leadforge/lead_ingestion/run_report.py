"""The run report, built only from database queries (task 18.3; Requirement 21.5).

``build_run_report`` reads the ``ingestion_run`` and ``source_run`` rows of one run
through the caller's session; it takes no orchestrator, outcome or result, so a report
can be regenerated for any past run. ``render_run_report`` turns it into text.

Provisional decisions (see choices.md, task 18.3):

* No run id means the latest run (newest ``started_at``, ``id`` breaking a tie). An
  unknown id, or an empty store, raises ``RunNotFoundError``.
* A figure the store does not hold is ``None`` and renders ``not recorded``, never 0.
  Per-source counts are written only when a run completes, so a ``running`` or
  ``aborted`` run has none. ``fetched``, ``merged`` and Credits have no producer yet
  and render ``not recorded``; ``quota`` is ``not stated`` unless the provider stated
  one.
* ``failure_class`` NULL means ok or not run (an Enrichment source with no work). On a
  completed run a row with any recorded activity (leads, retries, throttle waits, 429s)
  certainly ran and renders ``ok``; an all-zero row cannot be told from a source that
  never ran and renders ``none recorded``.
* ``leads_normalized`` sums contributions across phases, so it is labelled as not
  distinct leads.
* Control characters are escaped in every stored string, long warnings are cut, and the
  only instants shown are the recorded ones. Counts, names and classes only: no PII.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.run_record import RunRecordError, RunStatus
from leadforge.lead_ingestion.store.models import IngestionRun, SourceRun

__all__ = [
    "RunNotFoundError",
    "RunReport",
    "SourceReport",
    "build_run_report",
    "render_run_report",
]

NOT_RECORDED = "not recorded"
_LIVE_ACCESS = ("available", "gated", "unavailable")
MAX_WARNING_CHARS = 200


class RunNotFoundError(RunRecordError):
    """The requested run (or any run, when none was named) is not in the store."""


@dataclass(frozen=True)
class SourceReport:
    source_name: str
    mode: str  # a DataMode value; kept a plain string so a future mode cannot crash
    reason: str
    live_access: str | None  # available / gated / unavailable; None: not recorded
    failure_class: str | None
    counts_recorded: bool  # False for a run that never completed
    leads_normalized: int | None
    retries: int | None
    throttle_waits: int | None
    http_429_count: int | None
    credits_consumed: int | None
    quota_remaining: Mapping[str, int] | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RunReport:
    run_id: uuid.UUID
    status: str
    started_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    pool_size: int | None
    sources: tuple[SourceReport, ...]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _live_access(row: SourceRun, snapshot: Mapping[str, Any] | None) -> str | None:
    sources = snapshot.get("sources") if isinstance(snapshot, Mapping) else None
    entry = sources.get(row.source_name) if isinstance(sources, Mapping) else None
    stated = entry.get("live_access") if isinstance(entry, Mapping) else None
    if stated in _LIVE_ACCESS:
        return str(stated)
    # Only "unavailable" is certain from the boolean: true is available or gated.
    return "unavailable" if row.live_access is False else None


def build_run_report(session: Session, run_id: uuid.UUID | None = None) -> RunReport:
    """Query one run's report. Raises ``RunNotFoundError`` when there is no such run."""
    if run_id is None:
        run = session.scalars(
            sa.select(IngestionRun)
            .order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc())
            .limit(1)
        ).first()
        if run is None:
            raise RunNotFoundError("no run recorded")
    else:
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise RunNotFoundError("unknown run")
    rows = session.scalars(
        sa.select(SourceRun)
        .where(SourceRun.run_id == run.id)
        .order_by(SourceRun.source_name)
    ).all()
    # Counts exist only once the run completed (they are written with the finish).
    recorded = run.status == RunStatus.COMPLETED.value
    return RunReport(
        run_id=run.id,
        status=run.status,
        started_at=_as_utc(run.started_at),
        finished_at=None if run.finished_at is None else _as_utc(run.finished_at),
        exit_code=run.exit_code,
        pool_size=run.pool_size,
        sources=tuple(
            SourceReport(
                source_name=r.source_name,
                mode=r.resolved_mode,
                reason=r.mode_reason or "",
                live_access=_live_access(r, run.config_snapshot),
                failure_class=r.failure_class if recorded else None,
                counts_recorded=recorded,
                leads_normalized=r.leads_found if recorded else None,
                retries=r.retries if recorded else None,
                throttle_waits=r.throttle_waits if recorded else None,
                http_429_count=r.http_429_count if recorded else None,
                credits_consumed=r.credits_consumed,
                quota_remaining=r.quota_remaining or None,
                warnings=tuple(str(w) for w in (r.warnings or ())),
            )
            for r in rows
        ),
    )


def _printable(text: str) -> str:
    """Escape control characters so stored text cannot add or overwrite lines."""
    return "".join(
        c if c.isprintable() else c.encode("unicode_escape").decode() for c in text
    )


def _cut(text: str) -> str:
    safe = _printable(text)
    if len(safe) <= MAX_WARNING_CHARS:
        return safe
    return safe[: MAX_WARNING_CHARS - 1] + "…"


def _figure(value: int | None) -> str:
    return NOT_RECORDED if value is None else str(value)


def _instant(value: datetime | None) -> str:
    return NOT_RECORDED if value is None else value.isoformat()


def _failure(report: RunReport, source: SourceReport) -> str:
    if source.failure_class is not None:
        return _printable(source.failure_class)
    if report.status == RunStatus.COMPLETED.value:
        activity = (
            source.leads_normalized,
            source.retries,
            source.throttle_waits,
            source.http_429_count,
        )
        if any(figure for figure in activity):
            return "ok"  # it ran, and no failure class was stored
        return "none recorded"  # ok, or not run: the row cannot tell which
    if report.status == RunStatus.ABORTED.value:
        return "not recorded (run aborted)"
    return "not recorded (run still running or crashed)"


def _quota(source: SourceReport) -> str:
    if not source.quota_remaining:
        return "not stated"
    return ",".join(
        f"{_printable(str(k))}:{_printable(str(v))}"
        for k, v in sorted(source.quota_remaining.items())
    )


def render_run_report(report: RunReport) -> str:
    """Deterministic text: one line per fact, sources by name."""
    lines = [
        f"run {report.run_id}",
        f"status: {_printable(report.status)}",
        f"started: {_instant(report.started_at)}",
        f"finished: {_instant(report.finished_at)}",
        f"exit code: {_figure(report.exit_code)}",
        f"pool size: {_figure(report.pool_size)}",
    ]
    if report.status == RunStatus.ABORTED.value:
        lines.append("aborted: no per-source counts recorded")
    live = [s for s in report.sources if s.live_access in ("available", "gated")]
    synthetic_only = [s for s in report.sources if s.live_access == "unavailable"]
    unknown = [s for s in report.sources if s.live_access is None]
    for label, group in (
        ("could run live", live),
        ("synthetic-only by necessity", synthetic_only),
        ("classification not recorded", unknown),
    ):
        if group:
            names = ", ".join(_printable(s.source_name) for s in group)
            lines.append(f"{label}: {names}")
    lines.append(
        "leads_normalized counts contributions across phases, not distinct leads"
    )
    # Nothing persists these yet (8.15, 8.18): say so rather than imply none occurred.
    lines.append("over-merge suspects: not recorded")
    lines.append("primary-domain tie fallbacks: not recorded")
    lines.append(f"sources: {len(report.sources)}")
    for s in report.sources:
        name = _printable(s.source_name)
        lines.append(
            f"{name}: mode={_printable(s.mode)} reason={_printable(s.reason)} "
            f"live_access={_printable(s.live_access or NOT_RECORDED)}"
        )
        lines.append(
            f"  failure={_failure(report, s)} fetched={NOT_RECORDED} "
            f"leads_normalized={_figure(s.leads_normalized)} merged={NOT_RECORDED}"
        )
        lines.append(
            f"  retries={_figure(s.retries)} "
            f"throttle_waits={_figure(s.throttle_waits)} "
            f"http_429={_figure(s.http_429_count)} "
            f"credits={_figure(s.credits_consumed)} quota={_quota(s)}"
        )
        lines.extend(f"  warning: {_cut(w)}" for w in s.warnings)
    return "\n".join(lines)
