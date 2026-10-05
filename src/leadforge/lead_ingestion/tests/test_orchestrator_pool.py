"""Orchestrator bounded worker pool (task 11.1, Requirements 6.7, 6.8).

The sources here are throwaway subclasses defined in this module. The orchestrator
only ever sees the ``BaseLeadSource`` contract, and the structure guard in
``test_slice_structure`` keeps it that way.
"""

import ast
import asyncio
import time
from collections.abc import Callable, Mapping
from itertools import pairwise
from pathlib import Path
from typing import ClassVar

import pytest

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    LeadContribution,
    RateBucket,
    RateWindow,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    SourceResult,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.source_settings import (
    DEFAULT_MAX_CONCURRENT_SOURCES,
    load_max_concurrent_sources,
)

REQUEST = SourceRequest(kind="search")
SLOW_S = 0.02


class Tracker:
    """Shared by the sources of one test: events and peak in-flight count."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0
        self.events: list[str] = []
        self.acquired_at: dict[str, list[float]] = {}


class Slow(BaseLeadSource):
    name: ClassVar[str] = "slow"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()
    delay: ClassVar[float] = SLOW_S

    def __init__(
        self,
        mode: DataMode,
        tracker: Tracker,
        pacing: SourcePacing | None = None,
        acquisitions: int = 0,
    ) -> None:
        super().__init__(mode)
        self.tracker = tracker
        self.pacing = pacing
        self.acquisitions = acquisitions

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        t = self.tracker
        t.events.append(f"fetch:{self.name}")
        t.in_flight += 1
        t.peak = max(t.peak, t.in_flight)
        try:
            if self.pacing is not None:
                for _ in range(self.acquisitions):
                    await self.pacing.throttle.bucket("api").acquire()
                    t.acquired_at.setdefault(self.name, []).append(time.monotonic())
            await asyncio.sleep(self.delay)
        finally:
            t.in_flight -= 1
        return RawBatch(source_name=self.name, payload={"kind": request.kind})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return []


def make_sources(
    count: int, rate_limit: Mapping[str, RateBucket] | None = None
) -> list[type[Slow]]:
    attrs: dict[str, object] = {}
    if rate_limit is not None:
        attrs["rate_limit"] = rate_limit
    return [
        type(f"Slow{i}", (Slow,), {"name": f"slow-{i}", **attrs}) for i in range(count)
    ]


def live(_cls: type[BaseLeadSource], _settings: SourceSettings) -> ModeResolution:
    return ModeResolution(DataMode.LIVE, "test: live")


def orchestrator(
    classes: list[type[Slow]],
    tracker: Tracker,
    *,
    bound: int,
    resolve: Callable[[type[BaseLeadSource], SourceSettings], ModeResolution] = live,
    config: Mapping[str, SourceSettings] | None = None,
    acquisitions: int = 0,
) -> IngestionOrchestrator:
    def build(
        source_class: type[BaseLeadSource],
        mode: DataMode,
        pacing: SourcePacing | None,
    ) -> BaseLeadSource:
        if not issubclass(source_class, Slow):
            raise TypeError(source_class)
        return source_class(mode, tracker, pacing, acquisitions)

    return IngestionOrchestrator(
        SourceRegistry(classes, config),
        resolve_mode=resolve,
        build_source=build,
        max_concurrent_sources=bound,
    )


# Verifies: specs/lead-source-adapters/requirements.md#6.7
async def test_eight_sources_never_exceed_a_bound_read_from_configuration(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "sources.yaml"
    config_file.write_text("max_concurrent_sources: 2\n")
    bound = load_max_concurrent_sources(config_file)
    tracker = Tracker()

    results = await orchestrator(make_sources(8), tracker, bound=bound).run(REQUEST)

    assert bound == 2
    assert len(results) == 8
    assert tracker.peak == 2  # at the bound, never above it


# Verifies: specs/lead-source-adapters/requirements.md#6.7
async def test_unconfigured_bound_defaults_to_four(tmp_path: Path) -> None:
    config_file = tmp_path / "sources.yaml"
    config_file.write_text("sources: {}\n")
    bound = load_max_concurrent_sources(config_file)
    tracker = Tracker()

    await orchestrator(make_sources(8), tracker, bound=bound).run(REQUEST)

    assert DEFAULT_MAX_CONCURRENT_SOURCES == 4
    assert bound == 4
    assert tracker.peak == 4


# Verifies: specs/lead-source-adapters/requirements.md#6.7
def test_the_bound_is_required_and_never_a_literal_in_the_orchestrator() -> None:
    with pytest.raises(TypeError):
        IngestionOrchestrator(  # type: ignore[call-arg]
            SourceRegistry(),
            resolve_mode=live,
            build_source=lambda c, m, p: Slow(m, Tracker()),
        )
    source = Path(__file__).parent.parent / "orchestrator.py"
    numbers = [
        n.value
        for n in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
        if isinstance(n, ast.Constant)
        and isinstance(n.value, int | float)
        and not isinstance(n.value, bool)
        and n.value not in (0, 1)
    ]
    assert numbers == []


# Verifies: specs/lead-source-adapters/requirements.md#6.7
@pytest.mark.parametrize("bad", [0, -1, True, 2.5, "4", None])
def test_a_non_positive_or_non_integer_bound_is_rejected(bad: object) -> None:
    with pytest.raises(ValueError, match="max_concurrent_sources"):
        IngestionOrchestrator(
            SourceRegistry(),
            resolve_mode=live,
            build_source=lambda c, m, p: Slow(m, Tracker()),
            max_concurrent_sources=bad,  # type: ignore[arg-type]
        )


# Verifies: specs/lead-source-adapters/requirements.md#6.7
async def test_the_slot_is_released_when_a_source_fails() -> None:
    tracker = Tracker()

    class Boom(Slow):
        name: ClassVar[str] = "boom"

        async def fetch_raw(self, request: SourceRequest) -> RawBatch:
            raise RuntimeError("provider down")

    classes: list[type[Slow]] = [Boom, *make_sources(3)]
    with pytest.raises(ExceptionGroup):
        # bound 1: a leaked slot would hang the remaining sources forever
        await asyncio.wait_for(
            orchestrator(classes, tracker, bound=1).run(REQUEST), timeout=5
        )
    assert tracker.in_flight == 0


# Verifies: specs/lead-source-adapters/requirements.md#6.7
async def test_results_follow_the_active_order_and_carry_mode_and_reason() -> None:
    classes = make_sources(3)
    config = {
        "slow-0": SourceSettings(trust_rank=1),
        "slow-2": SourceSettings(trust_rank=5),
    }

    results = await orchestrator(classes, Tracker(), bound=3, config=config).run(
        REQUEST
    )

    assert [r.source_name for r in results] == ["slow-2", "slow-0", "slow-1"]
    assert all(isinstance(r, SourceResult) for r in results)
    assert {(r.resolved_mode, r.mode_reason) for r in results} == {
        (DataMode.LIVE, "test: live")
    }
    assert [r.batch.source_name for r in results if r.batch] == [
        "slow-2",
        "slow-0",
        "slow-1",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#6.7
async def test_disabled_sources_are_neither_resolved_nor_run() -> None:
    tracker = Tracker()
    resolved: list[str] = []

    def resolve(cls: type[BaseLeadSource], settings: SourceSettings) -> ModeResolution:
        resolved.append(cls.name)
        return live(cls, settings)

    config = {"slow-1": SourceSettings(enabled=False)}
    results = await orchestrator(
        make_sources(3), tracker, bound=3, resolve=resolve, config=config
    ).run(REQUEST)

    assert sorted(resolved) == ["slow-0", "slow-2"]
    assert sorted(r.source_name for r in results) == ["slow-0", "slow-2"]
    assert "fetch:slow-1" not in tracker.events


# Verifies: specs/lead-source-adapters/requirements.md#6.8
async def test_every_mode_is_resolved_before_any_slot_is_taken() -> None:
    tracker = Tracker()

    def resolve(cls: type[BaseLeadSource], settings: SourceSettings) -> ModeResolution:
        tracker.events.append(f"resolve:{cls.name}")
        return live(cls, settings)

    await orchestrator(make_sources(5), tracker, bound=1, resolve=resolve).run(REQUEST)

    kinds = [e.split(":")[0] for e in tracker.events]
    assert kinds == ["resolve"] * 5 + ["fetch"] * 5


# Verifies: specs/lead-source-adapters/requirements.md#6.8
async def test_a_synthetic_source_gets_no_pacing_and_a_live_one_its_own() -> None:
    tracker = Tracker()
    seen: dict[str, SourcePacing | None] = {}
    bucket = RateBucket(
        name="api", windows=(RateWindow(1, 1.0),), documented=True, doc_url="x"
    )
    classes = make_sources(2, {"api": bucket})

    def resolve(cls: type[BaseLeadSource], settings: SourceSettings) -> ModeResolution:
        if cls.name == "slow-0":
            return ModeResolution(DataMode.SYNTHETIC, "test: synthetic")
        return live(cls, settings)

    def build(
        source_class: type[BaseLeadSource],
        mode: DataMode,
        pacing: SourcePacing | None,
    ) -> BaseLeadSource:
        seen[source_class.name] = pacing
        assert issubclass(source_class, Slow)
        return source_class(mode, tracker)

    orch = IngestionOrchestrator(
        SourceRegistry(classes),
        resolve_mode=resolve,
        build_source=build,
        max_concurrent_sources=2,
    )
    results = await orch.run(REQUEST)

    assert seen["slow-0"] is None  # synthetic: nothing constructed
    live_pacing = seen["slow-1"]
    assert live_pacing is not None
    assert live_pacing.throttle.source_name == "slow-1"
    assert live_pacing.throttle.bucket_names() == ("api",)
    assert {r.source_name: r.resolved_mode for r in results} == {
        "slow-0": DataMode.SYNTHETIC,
        "slow-1": DataMode.LIVE,
    }


# Verifies: specs/lead-source-adapters/requirements.md#6.8
async def test_parallelism_leaves_each_providers_own_pacing_intact() -> None:
    per_seconds = 0.05
    bucket = RateBucket(
        name="api",
        windows=(RateWindow(1, per_seconds),),
        documented=True,
        doc_url="x",
    )
    tracker = Tracker()
    classes = make_sources(4, {"api": bucket})

    await orchestrator(classes, tracker, bound=4, acquisitions=3).run(REQUEST)

    assert tracker.peak == 4  # the sources really did run in parallel
    assert sorted(tracker.acquired_at) == [c.name for c in classes]
    for stamps in tracker.acquired_at.values():
        assert len(stamps) == 3
        gaps = [b - a for a, b in pairwise(stamps)]
        assert min(gaps) >= per_seconds * 0.8  # loose lower bound, never faster


# Verifies: specs/lead-source-adapters/requirements.md#6.7
@pytest.mark.parametrize(("bound", "count", "expected_peak"), [(1, 3, 1), (10, 3, 3)])
async def test_peak_is_the_smaller_of_the_bound_and_the_source_count(
    bound: int, count: int, expected_peak: int
) -> None:
    tracker = Tracker()

    results = await orchestrator(make_sources(count), tracker, bound=bound).run(REQUEST)

    assert len(results) == count
    assert tracker.peak == expected_peak


# Verifies: specs/lead-source-adapters/requirements.md#6.7
async def test_no_enabled_source_returns_an_empty_result() -> None:
    assert await orchestrator([], Tracker(), bound=2).run(REQUEST) == ()


# Verifies: specs/lead-source-adapters/requirements.md#6.7
async def test_results_follow_active_order_even_when_completion_order_differs() -> None:
    classes = [
        type("Late", (Slow,), {"name": "a-late", "delay": 0.1}),
        type("Early", (Slow,), {"name": "b-early", "delay": 0.0}),
    ]

    results = await orchestrator(classes, Tracker(), bound=2).run(REQUEST)

    assert [r.source_name for r in results] == ["a-late", "b-early"]


# Verifies: specs/lead-source-adapters/requirements.md#6.7
async def test_cancelling_a_run_releases_slots_and_leaks_no_tasks() -> None:
    tracker = Tracker()
    started = asyncio.Event()

    class Hang(Slow):
        name: ClassVar[str] = "hang"

        async def fetch_raw(self, request: SourceRequest) -> RawBatch:
            tracker.in_flight += 1
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                tracker.in_flight -= 1
            raise AssertionError("unreachable")

    classes: list[type[Slow]] = [Hang, *make_sources(2)]
    before = asyncio.all_tasks()
    run = asyncio.ensure_future(orchestrator(classes, tracker, bound=1).run(REQUEST))
    await started.wait()

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert tracker.in_flight == 0
    assert asyncio.all_tasks() == before
