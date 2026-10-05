"""Orchestrator per-run wall-clock timeout (task 11.4, Requirement 6.6).

Sources are throwaway subclasses. A "hanging" source awaits an event that is never
set, so it can only end by cancellation; the timeout is a short real one, and what
is asserted does not depend on how long the run actually took.
"""

import asyncio
from collections.abc import Mapping
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
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    SourceStatus,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.run_exit import EXIT_ALL_FAILED, EXIT_OK, map_run_exit

REQUEST = SourceRequest(kind="search")
SHORT_S = 0.05


class Probe:
    def __init__(self, hang: set[str], explode: set[str] | None = None) -> None:
        self.hang = hang
        self.explode = explode or set()
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.first_start = asyncio.Event()


class Source(BaseLeadSource):
    name: ClassVar[str] = "source"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()

    def __init__(self, mode: DataMode, probe: Probe) -> None:
        super().__init__(mode)
        self.probe = probe

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        self.probe.started.append(self.name)
        self.probe.first_start.set()
        if self.name in self.probe.explode:
            raise RuntimeError("programming error")
        if self.name in self.probe.hang:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.probe.cancelled.append(self.name)
                raise
        return RawBatch(source_name=self.name, payload={})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return []


def live(_c: type[BaseLeadSource], _s: SourceSettings) -> ModeResolution:
    return ModeResolution(DataMode.LIVE, "test: live")


def orchestrator(
    names: tuple[str, ...],
    probe: Probe,
    *,
    run_timeout_s: float = SHORT_S,
    max_concurrent_sources: int = 4,
) -> IngestionOrchestrator:
    classes = [type(f"S_{n}", (Source,), {"name": n}) for n in names]

    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        assert issubclass(source_class, Source)
        return source_class(mode, probe)

    return IngestionOrchestrator(
        SourceRegistry(classes),
        resolve_mode=live,
        build_source=build,
        max_concurrent_sources=max_concurrent_sources,
        run_timeout_s=run_timeout_s,
    )


# Verifies: specs/lead-source-adapters/requirements.md#6.6
async def test_an_in_flight_source_is_cancelled_and_recorded_timed_out() -> None:
    probe = Probe(hang={"slow"})
    results = await orchestrator(("a", "slow", "b"), probe).run(REQUEST)

    assert [r.source_name for r in results] == ["a", "b", "slow"]
    by_name = {r.source_name: r for r in results}
    slow = by_name["slow"]
    assert probe.cancelled == ["slow"]  # really cancelled, not abandoned
    assert slow.outcome.status is SourceStatus.TIMED_OUT
    assert slow.batch is None
    assert slow.contributions is None
    assert (slow.outcome.attempted, slow.outcome.succeeded, slow.outcome.failed) == (
        1,
        0,
        1,
    )
    assert slow.outcome.error is not None
    assert "in flight" in slow.outcome.error


# Verifies: specs/lead-source-adapters/requirements.md#6.6
async def test_sources_that_finished_before_the_clock_ran_out_keep_their_results() -> (
    None
):
    probe = Probe(hang={"slow"})
    results = await orchestrator(("a", "slow", "b"), probe).run(REQUEST)

    by_name = {r.source_name: r for r in results}
    for done in ("a", "b"):
        assert by_name[done].outcome.status is SourceStatus.OK
        assert by_name[done].batch is not None
        assert by_name[done].contributions == ()
        assert by_name[done].outcome.succeeded == 1


# Verifies: specs/lead-source-adapters/requirements.md#6.6
async def test_a_source_not_yet_started_is_never_called_and_is_recorded_timed_out() -> (
    None
):
    probe = Probe(hang={"a"})
    results = await orchestrator(("a", "b", "c"), probe, max_concurrent_sources=1).run(
        REQUEST
    )

    assert probe.started == ["a"]  # b and c never got a pool slot
    assert [r.source_name for r in results] == ["a", "b", "c"]  # all accounted for
    for waiting in results[1:]:
        assert waiting.outcome.status is SourceStatus.TIMED_OUT
        assert waiting.outcome.attempted == 0
        assert waiting.batch is None
        assert waiting.outcome.error is not None
        assert "not started" in waiting.outcome.error
        assert waiting.resolved_mode is DataMode.LIVE


# Verifies: specs/lead-source-adapters/requirements.md#6.6
async def test_no_timed_out_record_when_every_source_finishes_in_time() -> None:
    probe = Probe(hang=set())
    results = await orchestrator(("a", "b"), probe, run_timeout_s=30).run(REQUEST)

    assert [r.outcome.status for r in results] == [SourceStatus.OK] * 2


# Verifies: specs/lead-source-adapters/requirements.md#6.6
async def test_the_callers_own_cancellation_is_not_taken_for_the_timeout() -> None:
    probe = Probe(hang={"slow"})
    task = asyncio.create_task(
        orchestrator(("slow",), probe, run_timeout_s=30).run(REQUEST)
    )
    await probe.first_start.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert probe.cancelled == ["slow"]


# Verifies: specs/lead-source-adapters/requirements.md#6.6
async def test_a_programming_error_propagates_and_is_not_recorded_as_timed_out() -> (
    None
):
    probe = Probe(hang={"slow"}, explode={"bad"})
    with pytest.raises(ExceptionGroup) as caught:
        await orchestrator(("slow", "bad"), probe, run_timeout_s=30).run(REQUEST)

    assert caught.group_contains(RuntimeError)
    assert probe.cancelled == ["slow"]


# Verifies: specs/lead-source-adapters/requirements.md#6.6
@pytest.mark.parametrize(
    "bad", [0, -1, 0.0, float("nan"), float("inf"), True, "5", None]
)
def test_a_non_positive_or_non_finite_timeout_is_rejected(bad: object) -> None:
    with pytest.raises(ValueError, match="run_timeout_s"):
        IngestionOrchestrator(
            SourceRegistry([]),
            resolve_mode=live,
            build_source=lambda c, m, p: pytest.fail("no source expected"),
            max_concurrent_sources=1,
            run_timeout_s=bad,  # type: ignore[arg-type]
        )


# Verifies: specs/lead-source-adapters/requirements.md#6.6
# Verifies: specs/lead-source-adapters/requirements.md#6.5
async def test_a_timeout_with_one_success_still_exits_zero() -> None:
    probe = Probe(hang={"slow"})
    results = await orchestrator(("a", "slow"), probe).run(REQUEST)

    run_exit = map_run_exit(results)
    assert run_exit.exit_code == EXIT_OK
    assert "slow: timed_out attempted=1 succeeded=0 failed=1" in run_exit.summary
    assert "a: attempted=1 succeeded=1 failed=0" in run_exit.summary


# Verifies: specs/lead-source-adapters/requirements.md#6.6
# Verifies: specs/lead-source-adapters/requirements.md#6.4
async def test_a_timeout_that_leaves_no_success_exits_non_zero() -> None:
    probe = Probe(hang={"a"})
    results = await orchestrator(("a", "b"), probe, max_concurrent_sources=1).run(
        REQUEST
    )

    run_exit = map_run_exit(results)
    assert run_exit.exit_code == EXIT_ALL_FAILED
    assert "a: timed_out" in run_exit.summary
    assert "b: timed_out attempted=0 succeeded=0 failed=0" in run_exit.summary


# Verifies: specs/lead-source-adapters/requirements.md#6.6
def test_an_integer_too_large_for_a_float_is_rejected_as_a_value_error() -> None:
    with pytest.raises(ValueError, match="run_timeout_s"):
        orchestrator(("a",), Probe(hang=set()), run_timeout_s=10**400)
