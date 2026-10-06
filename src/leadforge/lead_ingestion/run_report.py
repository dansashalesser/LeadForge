"""The run report, built only from database queries (task 18.3; Requirement 21.5).

``build_run_report`` reads the ``ingestion_run`` and ``source_run`` rows of one run
through the caller's session; it takes no orchestrator, outcome or result, so a report
can be regenerated for any past run. ``render_run_report`` turns it into text.

Provisional decisions (see choices.md, task 18.3):

* No run id means the latest run (newest ``started_at``, ``id`` breaking a tie). An
  unknown id, or an empty store, raises ``RunNotFoundError``.
* A figure the store does not hold is ``None`` and renders ``not recorded``, never 0.
  Per-source counts are written only when a run completes, so a ``running`` or
  ``aborted`` run has none. The per-source ``merged`` has no producer (a merge spans
  sources) and renders ``not recorded``; the run's ``leads: N merged, R retired``
  line (0006) is the merge's own figure: active leads it wrote and leads it
  retired. ``quota`` is ``not stated`` unless the provider stated one.
* Follow-up (2026-10-06, 0005): ``fetched`` (records fetched), Credits, the
  ``calls: attempted= succeeded= failed=`` line and ``contributions_written`` are read
  from the ``source_run`` row; ``fetched`` and Credits stay ``not recorded`` for an
  adapter that reports no such figure. An aborted run shows its ``abort reason`` (a
  stage and an exception class) when one was stored.
* ``failure_class`` NULL means ok or not run (an Enrichment source with no work). On a
  completed run a row with any recorded activity (leads, retries, throttle waits, 429s)
  certainly ran and renders ``ok``; an all-zero row cannot be told from a source that
  never ran and renders ``none recorded``.
* ``leads_normalized`` sums contributions across phases, so it is labelled as not
  distinct leads.
* Web evidence (task 14.2 completion): per source, the stored contributions whose
  ``company.web_evidence.attachment`` is ``unattached`` and those attached by
  agreement (``own_domain``, ``third_party_mention``). A source that stored none shows
  no such line. Counts only.
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
from leadforge.lead_ingestion.store.merged_leads import stale_projections
from leadforge.lead_ingestion.store.models import (
    ContributionField,
    IngestionRun,
    SourceContribution,
    SourceRun,
)

__all__ = [
    "RunNotFoundError",
    "RunReport",
    "SourceReport",
    "build_run_report",
    "render_run_report",
]

NOT_RECORDED = "not recorded"
_LIVE_ACCESS = ("available", "gated", "unavailable")
# Stated in the run record by the composition root (task 16.12 completion); any other
# stored value is shown as not recorded, never echoed.
_MATCH_KEY_DIGEST_LINES = {
    "keyed": "keyed, comparable across runs with the same secret",
    "per_run": (
        "per-run random key, not comparable across runs (set "
        "LEADFORGE_MATCH_KEY_SECRET)"
    ),
}
MAX_WARNING_CHARS = 200
_ATTACHMENT_PATH = "company.web_evidence.attachment"
_ATTACHED = frozenset({"own_domain", "third_party_mention"})


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
    # Task 14.2 completion: (attached, unattached) web evidence; None: none stored.
    web_evidence: tuple[int, int] | None = None
    # Follow-up (0005): call counts, records fetched and contributions stored; None:
    # not recorded (a run that never completed, or a row written before 0005).
    attempted: int | None = None
    succeeded: int | None = None
    failed: int | None = None
    records_fetched: int | None = None
    contributions_written: int | None = None


@dataclass(frozen=True)
class RunReport:
    run_id: uuid.UUID
    status: str
    started_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    pool_size: int | None
    sources: tuple[SourceReport, ...]
    # Task 16.12 completion: "keyed" / "per_run" from the snapshot; None: not recorded.
    match_key_digests: str | None = None
    # Why an aborted run ended (stage and exception class); None: not recorded.
    failure_reason: str | None = None
    # The projection stamp the run wrote under, its flagged primary-domain tie
    # fallbacks, and the stored canonical leads projected under another version
    # (stale, 8.13); None: not recorded.
    projection_version: int | None = None
    primary_domain_ties_flagged: int | None = None
    stale_projections: int | None = None
    # Active leads the merge wrote and leads it retired (0006); None: not recorded.
    leads_merged: int | None = None
    leads_retired: int | None = None
    # Clusters the merge re-projected and how: "incremental" or "full: <why>" (0007).
    clusters_reprojected: int | None = None
    reprojection: str | None = None


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


def _match_key_digests(snapshot: Mapping[str, Any] | None) -> str | None:
    stated = (
        snapshot.get("match_key_digests") if isinstance(snapshot, Mapping) else None
    )
    return stated if stated in _MATCH_KEY_DIGEST_LINES else None


def _web_evidence(session: Session, run_id: uuid.UUID) -> dict[str, tuple[int, int]]:
    """Per source: (attached, unattached) web evidence stored in this run."""
    rows = session.execute(
        sa.select(SourceRun.source_name, ContributionField.value)
        .join(SourceContribution, SourceContribution.source_run_id == SourceRun.id)
        .join(
            ContributionField,
            ContributionField.contribution_id == SourceContribution.id,
        )
        .where(
            SourceRun.run_id == run_id,
            ContributionField.canonical_path == _ATTACHMENT_PATH,
        )
    ).all()
    counts: dict[str, tuple[int, int]] = {}
    for source_name, value in rows:
        attached, unattached = counts.get(source_name, (0, 0))
        counts[source_name] = (
            attached + (value in _ATTACHED),
            unattached + (value == "unattached"),
        )
    return counts


def _reprojection(report: RunReport) -> str:
    if report.clusters_reprojected is None or report.reprojection is None:
        return NOT_RECORDED
    how = report.reprojection
    if how.startswith("full: "):
        how = f"full ({how.removeprefix('full: ')})"
    return f"{how}, {report.clusters_reprojected} clusters"


def _stale(session: Session, version: int | None) -> int | None:
    """Active canonical leads projected under another version than the run's."""
    if version is None:
        return None
    return len(stale_projections(session, current_version=version))


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
    # Counts exist once the run stored what it fetched (follow-up 2026-10-06: before
    # its merge, so an aborted merge keeps them); a completed run always has them.
    completed = run.status == RunStatus.COMPLETED.value
    web_evidence = _web_evidence(session, run.id)
    return RunReport(
        run_id=run.id,
        status=run.status,
        started_at=_as_utc(run.started_at),
        finished_at=None if run.finished_at is None else _as_utc(run.finished_at),
        exit_code=run.exit_code,
        pool_size=run.pool_size,
        match_key_digests=_match_key_digests(run.config_snapshot),
        failure_reason=run.failure_reason,
        projection_version=run.projection_version,
        primary_domain_ties_flagged=run.primary_domain_ties_flagged,
        stale_projections=_stale(session, run.projection_version),
        leads_merged=run.leads_merged,
        leads_retired=run.leads_retired,
        clusters_reprojected=run.clusters_reprojected,
        reprojection=run.reprojection,
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
                web_evidence=web_evidence.get(r.source_name),
                attempted=r.attempted if recorded else None,
                succeeded=r.succeeded if recorded else None,
                failed=r.failed if recorded else None,
                records_fetched=r.records_fetched if recorded else None,
                contributions_written=r.contributions_written if recorded else None,
            )
            for r in rows
            for recorded in (completed or r.attempted is not None,)
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
        if report.failure_reason is not None:
            lines.append(f"abort reason: {_cut(report.failure_reason)}")
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
    # Over-merge suspects are not persisted yet (8.15): say so rather than imply none.
    lines.append("over-merge suspects: not recorded")
    lines.append(
        "primary-domain tie fallbacks: "
        + (
            NOT_RECORDED
            if report.primary_domain_ties_flagged is None
            else f"{report.primary_domain_ties_flagged} flagged"
        )
    )
    lines.append(f"projection version: {_figure(report.projection_version)}")
    lines.append(f"stale projections: {_figure(report.stale_projections)}")
    lines.append(
        "leads: "
        + (
            NOT_RECORDED
            if report.leads_merged is None or report.leads_retired is None
            else f"{report.leads_merged} merged, {report.leads_retired} retired"
        )
    )
    lines.append(f"re-projection: {_reprojection(report)}")
    lines.append(
        "match-key digests: "
        + _MATCH_KEY_DIGEST_LINES.get(report.match_key_digests or "", NOT_RECORDED)
    )
    lines.append(f"sources: {len(report.sources)}")
    for s in report.sources:
        name = _printable(s.source_name)
        lines.append(
            f"{name}: mode={_printable(s.mode)} reason={_printable(s.reason)} "
            f"live_access={_printable(s.live_access or NOT_RECORDED)}"
        )
        lines.append(
            f"  failure={_failure(report, s)} fetched={_figure(s.records_fetched)} "
            f"leads_normalized={_figure(s.leads_normalized)} "
            f"contributions_written={_figure(s.contributions_written)} "
            f"merged={NOT_RECORDED}"
        )
        lines.append(
            f"  calls: attempted={_figure(s.attempted)} "
            f"succeeded={_figure(s.succeeded)} failed={_figure(s.failed)}"
        )
        lines.append(
            f"  retries={_figure(s.retries)} "
            f"throttle_waits={_figure(s.throttle_waits)} "
            f"http_429={_figure(s.http_429_count)} "
            f"credits={_figure(s.credits_consumed)} quota={_quota(s)}"
        )
        if s.web_evidence is not None:
            attached, unattached = s.web_evidence
            lines.append(f"  web_evidence: attached={attached} unattached={unattached}")
        lines.extend(f"  warning: {_cut(w)}" for w in s.warnings)
    return "\n".join(lines)
