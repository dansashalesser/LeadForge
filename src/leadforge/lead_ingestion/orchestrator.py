"""Ingestion Orchestrator: bounded concurrent run of the enabled sources (task 11.1).

Requirements 6.7 and 6.8. The orchestrator touches adapters only through the
``BaseLeadSource`` contract and never names a concrete adapter (2.4).

Flow of ``run``:

1. Resolve the data mode of every enabled source, and build its pacing, *before* any
   pool slot exists, so a synthetic source can never reserve throttle capacity.
   Pacing is ``None`` for synthetic (``build_pacing`` constructs nothing).
2. Construct the adapters through ``SourceRegistry.active``, the one construction
   point, handing each its resolved mode and its own pacing.
3. Run them concurrently behind an ``asyncio.Semaphore`` of ``max_concurrent_sources``.

The semaphore is a cross-source bound only. It never touches a token bucket, and a
bucket never touches it, so neither mechanism can relax the other (6.8): each adapter
paces its own provider calls through the throttle it was handed.

Provisional decisions (see choices.md, task 11.1):

* The bound is a required constructor argument with no default here; the default of
  four lives in ``source_settings`` next to the key it belongs to.
* Mode resolution and adapter construction are injected (``resolve_mode``,
  ``build_source``); this module does not read the environment or build transports.
* Failures are not isolated yet (task 11.2): a failing source cancels its siblings and
  surfaces as an ``ExceptionGroup``. Phases, run timeout and outcome counts are later
  tasks (11.3 to 11.7).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.pacing import SourcePacing, build_pacing
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings

__all__ = [
    "AdapterFactory",
    "IngestionOrchestrator",
    "ModeResolverWithReason",
    "SourceResult",
]

ModeResolverWithReason = Callable[
    [type[BaseLeadSource], SourceSettings], ModeResolution
]
AdapterFactory = Callable[
    [type[BaseLeadSource], DataMode, SourcePacing | None], BaseLeadSource
]


@dataclass(frozen=True)
class SourceResult:
    """What one source returned in a run, with the mode it ran in and why."""

    source_name: str
    resolved_mode: DataMode
    mode_reason: str
    batch: RawBatch


class IngestionOrchestrator:
    def __init__(
        self,
        registry: SourceRegistry,
        *,
        resolve_mode: ModeResolverWithReason,
        build_source: AdapterFactory,
        max_concurrent_sources: int,
    ) -> None:
        if (
            not isinstance(max_concurrent_sources, int)
            or isinstance(max_concurrent_sources, bool)
            or max_concurrent_sources < 1
        ):
            raise ValueError("max_concurrent_sources must be an integer >= 1")
        self._registry = registry
        self._resolve_mode = resolve_mode
        self._build_source = build_source
        self._max_concurrent_sources = max_concurrent_sources

    async def run(self, request: SourceRequest) -> tuple[SourceResult, ...]:
        """Fetch from every enabled source, at most the bound in flight at once.

        Results follow the registry's active order, not completion order.
        """
        resolutions = {
            name: self._resolve_mode(
                self._registry.source_class(name), self._registry.settings(name)
            )
            for name in self._registry.enabled_names()
        }

        def build(source_class: type[BaseLeadSource]) -> BaseLeadSource:
            resolution = resolutions[source_class.name]
            pacing = build_pacing(
                source_class.name, source_class.rate_limit, resolution.mode
            )
            return self._build_source(source_class, resolution.mode, pacing)

        sources = self._registry.active(build)
        slots = asyncio.Semaphore(self._max_concurrent_sources)

        async def run_one(source: BaseLeadSource) -> SourceResult:
            async with slots:
                batch = await source.fetch_raw(request)
            resolution = resolutions[source.name]
            return SourceResult(source.name, resolution.mode, resolution.reason, batch)

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(run_one(source)) for source in sources]
        return tuple(task.result() for task in tasks)
