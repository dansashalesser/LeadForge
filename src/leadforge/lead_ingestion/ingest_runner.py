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
* The merge runs after the orchestrator has finished the run record, so a failure of
  the merge write raises out of ``run_ingestion`` while the record already says
  ``completed`` with the exit code of the sources. Not hidden: the exception propagates.
* ``projection_version`` is a constant 1: bumping it when exclusions or the rules change
  is not wired (16.6).
* The exit code is ``map_run_exit`` over the orchestrator's results only: whether the
  merge persisted anything does not change it (6.4, 6.5).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    LeadContribution,
    LiveAccess,
    SourceRequest,
)
from leadforge.lead_ingestion.clustering import cluster_contributions
from leadforge.lead_ingestion.compliance import blocked_identities
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.env_file import load_env_file_into_process
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.exclusion_settings import load_identity_exclusions
from leadforge.lead_ingestion.mode_resolution import ModeResolution, resolve_data_mode
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    RunRecorder,
    SourceResult,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.projection import project_lead
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
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
    SourceBatch,
    persist_merge,
)
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.raw_responses import RetentionPolicy
from leadforge.lead_ingestion.store.transactions import StoreWriter
from leadforge.lead_ingestion.target_profile import (
    DEFAULT_TARGET_PROFILE_PATH,
    TargetProfile,
    check_against_registry,
    effective_vocabulary,
    load_target_profile,
)

__all__ = ["PROJECTION_VERSION", "IngestionOutcome", "run_ingestion"]

PROJECTION_VERSION = 1
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


class _RememberRunId:
    """A ``RunRecorder`` that keeps the id of the run it started."""

    def __init__(self, inner: RunRecorder) -> None:
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
        await self._inner.finish(run_id, results)


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
) -> IngestionOutcome:
    """Run every enabled source, merge and persist, and report. Raises on bad config.

    ``registry`` replaces discovery (tests register scripted sources through it); its
    own settings then replace ``sources_path``'s per-source entries, but the pool bound
    and the run timeout are still read from ``sources_path``.
    """
    load_env_file_into_process(env_file_path)
    if registry is None:
        registry = SourceRegistry.discover(config=load_source_settings(sources_path))
    pool = load_max_concurrent_sources(sources_path)
    timeout = load_run_timeout_s(sources_path)
    profile = _read_profile(registry, target_profile_path)
    exclusions = load_identity_exclusions(exclusions_path)
    global_mode = _global_mode()

    engine = create_store_engine()
    try:
        upgrade_to_head(engine.url.render_as_string(hide_password=False))
        writer = StoreWriter(engine)
        recorder = _RememberRunId(StoreRunRecorder(writer, global_mode=global_mode))

        def resolve(
            source_class: type[BaseLeadSource], settings: SourceSettings
        ) -> ModeResolution:
            return resolve_data_mode(
                source_class, settings, global_override=global_mode
            )

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
        )
        results = await orchestrator.run(SourceRequest(kind="discovery"))
        run_id = recorder.run_id
        if run_id is None:  # the orchestrator starts the record before any source
            raise RuntimeError("the run record was not started")

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
        contributions: list[LeadContribution] = [
            c for b in batches for c in b.contributions
        ]
        ranks = {name: registry.settings(name).trust_rank for name in registry.names()}
        blocked = blocked_identities(contributions)
        merged = [
            (cluster, project_lead(cluster, ranks, blocked=blocked))
            for cluster in cluster_contributions(contributions, exclusions)
        ]
        computed_at = clock()
        stored = await writer.write_batch(
            lambda session: persist_merge(
                session,
                run_id=run_id,
                batches=batches,
                merged=merged,
                computed_at=computed_at,
                projection_version=PROJECTION_VERSION,
                retention=RetentionPolicy(),
            )
        )
        with Session(engine) as session:
            report_text = render_run_report(build_run_report(session, run_id))
        return IngestionOutcome(
            run_id, map_run_exit(results), results, stored, report_text
        )
    finally:
        engine.dispose()
