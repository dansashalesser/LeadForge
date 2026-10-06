"""The composition root of an ingestion run (task 20).

``run_ingestion`` is the one place that builds a full run from configuration and
returns what happened; the ``ingest`` command only calls it and prints. It names no
concrete adapter: sources come from ``SourceRegistry`` and are built through
``BaseLeadSource.from_run``.

The run, in order: read the optional configuration and ``.env``; resolve every source's
mode (an empty environment resolves every source to ``synthetic``); migrate the local
store to head and build the orchestrator over a store-backed run recorder; run both
phases; merge the contributions of every source (cluster, then project with the Source
Trust Ranks and the run's compliance reports); persist the merge; map the run to an exit
code; render the run report from the database.

Provisional decisions (see choices.md, task 20):

* Configuration is all optional: a missing ``config/sources.yaml``, target profile or
  identity-exclusion file is "no configuration". An explicit path that is missing is an
  error, as the loaders already treat it.
* The store is the one ``create_store_engine`` resolves (``DATABASE_URL``, else the
  local SQLite file). The migration to head is an explicit step of every run
  (idempotent); nothing creates tables implicitly.
* One run at a time (follow-up 2026-10-06): the run takes the store's run lock
  (``store.run_lock``) after migrating and BEFORE its run record or any provider
  call; a second run raises ``RunInProgressError`` ("another run in progress", no
  value) having recorded and spent nothing. The lease is the run timeout plus
  ``LOCK_STALE_GRACE_S``; a crashed run's lock is taken over once it expired. The
  merge transaction renews the lease first, so a run whose lock was taken over is
  refused there (``merge: RunLockLostError``). The lock is released at the end.
* Run lifecycle (follow-up 2026-10-06): the orchestrator's completion is deferred.
  The run's raw payloads, contributions and per-source counts (calls, records,
  credits, ``contributions_written``) commit FIRST in their own transaction
  (``persist_observations`` + ``StoreRunRecorder.spend``; idempotent, so a repeated
  record is stored once). The merge and the completion (status, exit code) are a
  second transaction, so the record says ``completed`` only once the merge
  committed. A failure after the sources ran rolls back only its own transaction,
  marks the run ``aborted`` with the reason ``<stage>: <exception class>``
  (``observe``, ``merge`` or ``merge_write``; never the exception's text) and
  propagates; contributions an aborted merge left unmerged are merged by the next
  run. ``aborted`` is the existing vocabulary for "ended by an exception".
* Projection version (16.6 wiring, follow-up 2026-10-06): the basis
  (``ProjectionBasis.of``: rules revision, Identity Exclusions as keyed digests, Source
  Trust Ranks) is built with the rest of the configuration, so exclusions without a
  stable ``LEADFORGE_MATCH_KEY_SECRET`` are a ``ConfigurationError`` before any run
  record. In the merge transaction the stamp is ``stamp_projection`` over the newest
  completed run's stored stamp (kept when the basis is unchanged, else bumped), the
  canonical leads are written under its version, and the run stores the stamp and the
  count of flagged primary-domain tie fallbacks. Two runs racing for the stamp are
  not serialized.
* Re-merge (follow-up, user option A, 2026-10-06): in the merge transaction the run
  identifies the whole stored log and re-projects only the clusters that need it
  (``remerge.reproject``: incremental, or every cluster when the projection basis
  changed, flagged ``full: <why>`` on the run and in the report), and saves them
  through the one ``persist_merge`` path, so a repeated run adds no contribution and
  no lead and re-projects nothing, and a split or join retires the replaced leads.
  An exact primary-domain tie is asked for through
  ``tie_resolution.resolve_primary_domain`` (stored answer first; a model only in
  live mode and only when ``tie_resolver`` is given, none by default); the run
  records the leads merged and retired.
* The exit code is ``map_run_exit`` over the orchestrator's results only: whether the
  merge persisted anything does not change it (6.4, 6.5).
* Configuration is read and validated before the run record exists (follow-up
  2026-10-06): the sources and limits files, the profile, the exclusions, the global
  mode, the Match Key secret and each live source's plan-dependent rate limits
  (``live_rate_limits``, each adapter's ``optional_env`` plan settings) are all read
  before the store is touched, so a bad value is a ``ConfigurationError`` (variable
  named, value never echoed) with no run recorded. The orchestrator is handed the
  limits and reads no environment.
* Merge log (task 16.12 completion, 21.4): the Match Key digester is built from the
  environment with the rest of the configuration, so a too-short
  ``LEADFORGE_MATCH_KEY_SECRET`` fails the run before any record or source; an absent
  one gives a per-run key, one warning, and ``per_run`` in the run record and report.
  One ``lead_merge`` line per lead the merge created or changed is logged after the
  merge has persisted (an unchanged lead is never re-logged); the
  logger-failure count (``MergeLogOutcome.failed``) is not surfaced yet (18.x).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    LiveAccess,
    RateBucket,
    SourceRequest,
)
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.env_file import load_env_file_into_process
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.exclusion_settings import load_identity_exclusions
from leadforge.lead_ingestion.match_key_digest import (
    MATCH_KEY_SECRET_ENV,
    match_key_digester_from_environ,
)
from leadforge.lead_ingestion.merge_log import log_merges
from leadforge.lead_ingestion.mode_resolution import ModeResolution, resolve_data_mode
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    SourceResult,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.projection import (
    ProjectionBasis,
    ProjectionStamp,
    stamp_projection,
)
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.remerge import Reprojection, reproject
from leadforge.lead_ingestion.run_exit import RunExit, map_run_exit
from leadforge.lead_ingestion.run_recorder import StoreRunRecorder
from leadforge.lead_ingestion.run_report import build_run_report, render_run_report
from leadforge.lead_ingestion.source_settings import (
    load_max_concurrent_sources,
    load_run_timeout_s,
    load_source_settings,
)
from leadforge.lead_ingestion.store.merged_leads import (
    MergeStored,
    Observed,
    SourceBatch,
    persist_merge,
    persist_observations,
)
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.raw_responses import RetentionPolicy
from leadforge.lead_ingestion.store.run_lock import (
    LOCK_STALE_GRACE_S,
    RunInProgressError,
    release_run_lock,
    renew_run_lock,
    try_acquire_run_lock,
)
from leadforge.lead_ingestion.store.run_records import RunRecordRepository
from leadforge.lead_ingestion.store.transactions import StoreWriter
from leadforge.lead_ingestion.target_profile import (
    DEFAULT_TARGET_PROFILE_PATH,
    TargetProfile,
    check_against_registry,
    effective_vocabulary,
    load_target_profile,
)
from leadforge.lead_ingestion.tie_resolution import TieResolver

__all__ = [
    "IngestionOutcome",
    "RunInProgressError",
    "live_rate_limits",
    "run_ingestion",
]
GLOBAL_MODE_VARIABLE = "LEADFORGE_MODE"

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class IngestionOutcome:
    """What a run did: the exit it maps to, its results, the merge and its report."""

    run_id: uuid.UUID
    exit: RunExit
    results: tuple[SourceResult, ...]
    stored: MergeStored
    # Rendered from the database rows of this run (21.5), not from ``results``.
    report_text: str


class _DeferredCompletion:
    """A ``RunRecorder`` that keeps the run id and defers the completion.

    The orchestrator's ``finish`` with results writes nothing: the root completes the
    record from the results ``run`` returned, in the merge's own transaction
    (``StoreRunRecorder.completion``), so a run is never ``completed`` before its
    merge committed. An abort (``None``) is written at once, as the orchestrator
    expects.
    """

    def __init__(self, inner: StoreRunRecorder) -> None:
        self._inner = inner
        self.run_id: uuid.UUID | None = None

    async def start(
        self,
        resolutions: Mapping[str, ModeResolution],
        settings: Mapping[str, SourceSettings],
        *,
        pool_size: int,
        run_timeout_s: float,
        live_access: Mapping[str, LiveAccess],
    ) -> uuid.UUID:
        self.run_id = await self._inner.start(
            resolutions,
            settings,
            pool_size=pool_size,
            run_timeout_s=run_timeout_s,
            live_access=live_access,
        )
        return self.run_id

    async def finish(
        self, run_id: uuid.UUID, results: tuple[SourceResult, ...] | None
    ) -> None:
        if results is None:
            await self._inner.finish(run_id, None)


async def _abort(
    recorder: StoreRunRecorder, run_id: uuid.UUID, stage: str, error: BaseException
) -> None:
    """Mark the run aborted at ``stage``; a failing marker is noted, never raised.

    The reason is the stage and the exception's class only: an exception's text may
    carry a value (an address, a payload fragment) and is never stored.
    """
    try:
        await recorder.abort(run_id, reason=f"{stage}: {type(error).__name__}")
    except Exception as write_error:  # noqa: BLE001 - noted and logged, the original wins
        _log.error(
            "run_record_abort_failed",
            run_id=str(run_id),
            error=type(write_error).__name__,
        )
        error.add_note(
            f"run record {run_id} could not be marked aborted: "
            f"{type(write_error).__name__}"
        )


async def _release(writer: StoreWriter, holder: uuid.UUID) -> None:
    """Free the run lock; a failure is logged (the lease expires anyway)."""
    try:
        await writer.write_batch(lambda session: release_run_lock(session, holder))
    except Exception as error:  # noqa: BLE001 - the run's own outcome wins
        _log.error("run_lock_release_failed", error=type(error).__name__)


def _global_mode() -> DataMode | None:
    text = os.environ.get(GLOBAL_MODE_VARIABLE, "").strip().casefold()
    if not text:
        return None
    try:
        return DataMode(text)
    except ValueError:
        raise ConfigurationError(
            GLOBAL_MODE_VARIABLE,
            key_path="",
            detail=f"must be one of {', '.join(m.value for m in DataMode)}",
        ) from None


def live_rate_limits(
    registry: SourceRegistry,
    resolve: Callable[[type[BaseLeadSource], SourceSettings], ModeResolution],
    environ: Mapping[str, str],
) -> dict[str, Mapping[str, RateBucket]]:
    """Each live source's plan-dependent buckets (``run_rate_limit``), read now.

    Called before the run record exists, so an unusable plan setting raises its
    ``ConfigurationError`` (variable named, value never echoed) before any write. A
    synthetic source is never asked (7.5).
    """
    limits: dict[str, Mapping[str, RateBucket]] = {}
    for name in registry.enabled_names():
        source_class = registry.source_class(name)
        if resolve(source_class, registry.settings(name)).mode is DataMode.LIVE:
            limits[name] = source_class.run_rate_limit(environ)
    return limits


def _read_profile(registry: SourceRegistry, path: Path | None) -> TargetProfile | None:
    """The Target Profile, or ``None`` when no path was given and none is in reach."""
    if path is None:
        if not DEFAULT_TARGET_PROFILE_PATH.is_file():
            return None
        path = DEFAULT_TARGET_PROFILE_PATH
    profile = load_target_profile(path)
    unregistered = check_against_registry(profile, registry, path=path)
    if unregistered:
        _log.warning("target_profile_names_unregistered_sources", sources=unregistered)
    return profile


async def run_ingestion(
    *,
    registry: SourceRegistry | None = None,
    sources_path: Path | None = None,
    target_profile_path: Path | None = None,
    exclusions_path: Path | None = None,
    env_file_path: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    tie_resolver: Callable[[], TieResolver] | None = None,
) -> IngestionOutcome:
    """Run every enabled source, merge and persist, and report. Raises on bad config.

    ``registry`` replaces discovery (tests register scripted sources through it); its
    own settings then replace ``sources_path``'s per-source entries, but the pool bound
    and the run timeout are still read from ``sources_path``. ``tie_resolver`` builds
    the port that may answer an exact primary-domain tie in live mode (8.18); none by
    default, so such a tie stays the flagged fallback.
    """
    load_env_file_into_process(env_file_path)
    if registry is None:
        registry = SourceRegistry.discover(config=load_source_settings(sources_path))
    pool = load_max_concurrent_sources(sources_path)
    timeout = load_run_timeout_s(sources_path)
    profile = _read_profile(registry, target_profile_path)
    exclusions = load_identity_exclusions(exclusions_path)
    global_mode = _global_mode()
    digester = match_key_digester_from_environ(os.environ)

    def resolve(
        source_class: type[BaseLeadSource], settings: SourceSettings
    ) -> ModeResolution:
        return resolve_data_mode(source_class, settings, global_override=global_mode)

    rate_limits = live_rate_limits(registry, resolve, os.environ)
    ranks = {name: registry.settings(name).trust_rank for name in registry.names()}
    try:
        basis = ProjectionBasis.of(exclusions, ranks, digester=digester)
    except ValueError:
        # Never echoes an exclusion: the message names the variable only.
        raise ConfigurationError(
            MATCH_KEY_SECRET_ENV,
            key_path="",
            detail="must be set when Identity Exclusions are configured, so the "
            "projection version is stable across runs",
        ) from None

    engine = create_store_engine()
    try:
        upgrade_to_head(engine.url.render_as_string(hide_password=False))
        writer = StoreWriter(engine)
        # The run lock comes before the run record and any provider call: a run
        # that cannot take it records nothing and spends nothing.
        holder = uuid.uuid4()
        lease = timeout + LOCK_STALE_GRACE_S
        held = await writer.write_batch(
            lambda session: try_acquire_run_lock(
                session, holder, now=clock(), lease_s=lease
            )
        )
        if held is not None:
            raise RunInProgressError(
                acquired_at=held.acquired_at, expires_at=held.expires_at
            )
        try:
            store_recorder = StoreRunRecorder(
                writer,
                global_mode=global_mode,
                match_key_digests_comparable=digester.comparable_across_runs,
            )
            recorder = _DeferredCompletion(store_recorder)

            def build(
                source_class: type[BaseLeadSource],
                mode: DataMode,
                pacing: SourcePacing | None,
            ) -> BaseLeadSource:
                return source_class.from_run(
                    mode,
                    transport=source_class.build_transport(mode),
                    pacing=pacing,
                    vocabulary=(
                        None
                        if profile is None
                        else effective_vocabulary(profile, source_class)
                    ),
                )

            orchestrator = IngestionOrchestrator(
                registry,
                resolve_mode=resolve,
                build_source=build,
                max_concurrent_sources=pool,
                run_timeout_s=timeout,
                run_recorder=recorder,
                live_rate_limits=rate_limits,
                identity_exclusions=exclusions,
            )
            results = await orchestrator.run(SourceRequest(kind="discovery"))
            run_id = recorder.run_id
            if run_id is None:  # the orchestrator starts the record before any source
                raise RuntimeError("the run record was not started")

            stage = "observe"
            try:
                batches = tuple(
                    SourceBatch(
                        r.source_name,
                        r.resolved_mode,
                        r.phase.value,
                        r.batch.payload,
                        tuple(r.contributions),
                    )
                    for r in results
                    if r.batch is not None and r.contributions is not None
                )
                computed_at = clock()
                spend = store_recorder.spend(run_id, results)

                def observe(session: Session) -> Observed:
                    # Committed on its own, before the merge: what the run fetched
                    # and spent survives a merge that fails.
                    observed = persist_observations(
                        session,
                        run_id=run_id,
                        batches=batches,
                        computed_at=computed_at,
                        retention=RetentionPolicy(),
                    )
                    spend(session)
                    return observed

                observed = await writer.write_batch(observe)
                stage = "merge"
                complete = store_recorder.completion(run_id, results)
                plan = Reprojection((), None, 0)

                def merge_and_complete(session: Session) -> MergeStored:
                    # One transaction, under the renewed lock: the clusters that
                    # need it are re-projected and saved through the one path.
                    nonlocal stage, plan
                    renew_run_lock(session, holder, now=clock(), lease_s=lease)
                    runs = RunRecordRepository(session)
                    previous = runs.latest_projection_stamp()
                    stamp = stamp_projection(
                        None if previous is None else ProjectionStamp(*previous), basis
                    )
                    plan = reproject(
                        session,
                        (),
                        exclusions=exclusions,
                        trust_ranks=ranks,
                        now=computed_at,
                        tie_resolver=tie_resolver,
                        projection_version=stamp.version,
                        full_reason=(
                            None
                            if previous is None or previous[0] == stamp.version
                            else "projection basis changed"
                        ),
                    )
                    stage = "merge_write"
                    stored = persist_merge(
                        session,
                        run_id=run_id,
                        batches=(),
                        merged=plan.merged,
                        computed_at=computed_at,
                        projection_version=stamp.version,
                        projection_fingerprint=stamp.fingerprint,
                        retention=RetentionPolicy(),
                    )
                    runs.record_projection(
                        run_id,
                        version=stamp.version,
                        fingerprint=stamp.fingerprint,
                        ties_flagged=sum(
                            1 for _, r in plan.merged if r.primary_domain_flagged
                        ),
                        leads_merged=stored.canonical_leads,
                        leads_retired=stored.leads_retired,
                        clusters_reprojected=len(plan.merged),
                        reprojection=plan.label,
                    )
                    complete(session)
                    return replace(
                        stored,
                        contributions=observed.contributions,
                        raw_responses=observed.raw_responses,
                    )

                stored = await writer.write_batch(merge_and_complete)
            except BaseException as error:
                await _abort(store_recorder, run_id, stage, error)
                raise
            # Only the leads this merge created or changed are logged.
            log_merges((plan.merged[i][1] for i in stored.changed), digester=digester)
            with Session(engine) as session:
                report_text = render_run_report(build_run_report(session, run_id))
            return IngestionOutcome(
                run_id, map_run_exit(results), results, stored, report_text
            )
        finally:
            await _release(writer, holder)
    finally:
        engine.dispose()
