"""Orchestrator two-phase run (task 11.5, Requirement 6.9).

Discovery (``search``-capable sources) runs first; Enrichment (``enrich``-capable
sources) then runs over a mechanical work list built from what Discovery produced. The
sources are throwaway subclasses; the orchestrator sees only the ``BaseLeadSource``
contract.
"""

import asyncio
import inspect
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import ClassVar

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    EnrichmentRequest,
    LeadContribution,
    RateBucket,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.errors import SourceUnauthorized
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
)
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    Phase,
    SourceOutcome,
    SourceResult,
    SourceStatus,
    enrichment_work_list,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings

REQUEST = SourceRequest(kind="search")
SEARCH = frozenset({Capability.SEARCH})
ENRICH = frozenset({Capability.ENRICH})
BOTH = SEARCH | ENRICH


def contribution(source: str, label: str) -> LeadContribution:
    path = "lead.label"
    return LeadContribution(
        source_name=source,
        values={path: label},
        provenance=(
            FieldProvenance(
                canonical_path=path,
                source_name=source,
                data_mode=DataMode.SYNTHETIC,
                fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
                raw_field_path="raw.label",
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            ),
        ),
    )


class Probe:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.requests: dict[str, SourceRequest] = {}
        self.in_flight = 0
        self.peak = 0
        self.cancelled: list[str] = []


class Source(BaseLeadSource):
    name: ClassVar[str] = "source"
    capabilities: ClassVar[frozenset[Capability]] = SEARCH
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()
    # How many contributions the source's Discovery call yields (labels "<name>-<i>").
    yields: ClassVar[int] = 1
    fail_unauthorized: ClassVar[bool] = False
    fail_phase: ClassVar[str] = "search"
    hang_phase: ClassVar[str | None] = None
    # Event-loop turns a Discovery call yields before finishing (no wall-clock timing).
    ticks: ClassVar[int] = 0

    def __init__(self, mode: DataMode, probe: Probe) -> None:
        super().__init__(mode)
        self.probe = probe

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        probe = self.probe
        probe.events.append(f"start:{self.name}:{request.kind}")
        probe.requests[f"{self.name}:{request.kind}"] = request
        probe.in_flight += 1
        probe.peak = max(probe.peak, probe.in_flight)
        try:
            if self.fail_unauthorized and request.kind == self.fail_phase:
                raise SourceUnauthorized(self.name, endpoint="/x")
            if self.hang_phase == request.kind:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    probe.cancelled.append(self.name)
                    raise
            await asyncio.sleep(0.01)
            for _ in range(self.ticks if request.kind == "search" else 0):
                await asyncio.sleep(0)
        finally:
            probe.in_flight -= 1
        probe.events.append(f"end:{self.name}:{request.kind}")
        return RawBatch(source_name=self.name, payload={"kind": request.kind})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        if raw.payload["kind"] != "search":
            return []
        return [contribution(self.name, f"{self.name}-{i}") for i in range(self.yields)]


def make(name: str, capabilities: frozenset[Capability], **attrs: object) -> type:
    return type(
        f"S_{name}", (Source,), {"name": name, "capabilities": capabilities, **attrs}
    )


def live(_c: type[BaseLeadSource], _s: SourceSettings) -> ModeResolution:
    return ModeResolution(DataMode.LIVE, "test: live")


def synthetic(_c: type[BaseLeadSource], _s: SourceSettings) -> ModeResolution:
    return ModeResolution(DataMode.SYNTHETIC, "test: synthetic")


def orchestrator(
    classes: list[type],
    probe: Probe,
    *,
    resolve: object = live,
    bound: int = 4,
    run_timeout_s: float = 30,
) -> IngestionOrchestrator:
    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        assert issubclass(source_class, Source)
        return source_class(mode, probe)

    return IngestionOrchestrator(
        SourceRegistry(classes),
        resolve_mode=resolve,  # type: ignore[arg-type]
        build_source=build,
        max_concurrent_sources=bound,
        run_timeout_s=run_timeout_s,
    )


def labels(request: SourceRequest) -> list[str]:
    assert isinstance(request, EnrichmentRequest)
    return [str(c.values["lead.label"]) for c in request.work_list]


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_every_discovery_source_finishes_before_any_enrichment_starts() -> None:
    probe = Probe()
    classes = [
        make("a-search", SEARCH),
        make("b-enrich", ENRICH),
        make("c-search", SEARCH),
        make("d-enrich", ENRICH),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    last_discovery_end = max(
        i for i, e in enumerate(probe.events) if e.startswith("end:") and ":search" in e
    )
    first_enrichment_start = min(
        i for i, e in enumerate(probe.events) if e.endswith(":enrich")
    )
    assert last_discovery_end < first_enrichment_start


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_each_source_runs_only_in_the_phases_its_capabilities_declare() -> None:
    probe = Probe()
    classes = [
        make("s", SEARCH),
        make("e", ENRICH),
        make("both", BOTH),
        make("neither", frozenset()),
    ]

    results = await orchestrator(classes, probe).run(REQUEST)

    started = sorted(e for e in probe.events if e.startswith("start:"))
    assert started == [
        "start:both:enrich",
        "start:both:search",
        "start:e:enrich",
        "start:s:search",
    ]
    assert [(r.source_name, r.phase) for r in results] == [
        ("both", Phase.DISCOVERY),
        ("s", Phase.DISCOVERY),
        ("both", Phase.ENRICHMENT),
        ("e", Phase.ENRICHMENT),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_enrichment_receives_every_lead_discovery_produced() -> None:
    probe = Probe()
    classes = [
        make("a", SEARCH, yields=2),
        make("b", SEARCH, yields=1),
        make("e", ENRICH),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert labels(probe.requests["e:enrich"]) == ["a-0", "a-1", "b-0"]


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_a_failed_discovery_source_contributes_nothing_to_the_work_list() -> None:
    probe = Probe()
    classes = [
        make("a", SEARCH, fail_unauthorized=True),
        make("b", SEARCH),
        make("e", ENRICH),
    ]

    results = await orchestrator(classes, probe).run(REQUEST)

    assert labels(probe.requests["e:enrich"]) == ["b-0"]
    by = {(r.source_name, r.phase): r for r in results}
    assert by[("a", Phase.DISCOVERY)].outcome.status is SourceStatus.UNAUTHORIZED
    assert by[("e", Phase.ENRICHMENT)].outcome.status is SourceStatus.OK


# Verifies: specs/lead-source-adapters/requirements.md#6.9
def test_the_work_list_is_a_pure_function_of_the_discovery_results_alone() -> None:
    assert list(inspect.signature(enrichment_work_list).parameters) == ["results"]
    low = contribution("a", "low-score")
    high = contribution("a", "high-score")

    def result(name: str, items: tuple[LeadContribution, ...] | None) -> SourceResult:
        outcome = _outcome(name)
        return SourceResult(
            name, DataMode.LIVE, "t", None, items, outcome, Phase.DISCOVERY
        )

    results = (result("a", (low, high)), result("b", None), result("c", (low,)))

    # Every contribution, in order, none filtered or ranked, failed sources add none.
    assert enrichment_work_list(results) == (low, high, low)
    assert enrichment_work_list(()) == ()


def _outcome(name: str) -> SourceOutcome:
    return SourceOutcome(name, SourceStatus.OK, 1, 1, 0, 0, 0, None)


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_the_work_list_ignores_any_score_a_contribution_carries() -> None:
    probe = Probe()

    class Scored(Source):
        def normalize(self, raw: RawBatch) -> list[LeadContribution]:
            if raw.payload["kind"] != "search":
                return []
            return [contribution(self.name, label) for label in ("zero", "ninety")]

    classes = [type("S_scored", (Scored,), {"name": "scored", "capabilities": SEARCH})]
    classes.append(make("e", ENRICH))

    await orchestrator(classes, probe).run(REQUEST)

    assert labels(probe.requests["e:enrich"]) == ["zero", "ninety"]


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_an_empty_work_list_makes_enrichment_a_no_op_not_an_error() -> None:
    probe = Probe()
    classes = [make("s", SEARCH, yields=0), make("e", ENRICH)]

    results = await orchestrator(classes, probe).run(REQUEST)

    assert "start:e:enrich" not in probe.events  # no enrichment call was made
    assert [(r.source_name, r.phase) for r in results] == [("s", Phase.DISCOVERY)]


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_a_run_with_no_discovery_source_is_a_no_op_enrichment() -> None:
    probe = Probe()

    results = await orchestrator([make("e", ENRICH)], probe).run(REQUEST)

    assert results == ()
    assert probe.events == []


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_a_zero_credential_run_exercises_enrich_only_adapters() -> None:
    probe = Probe()
    classes = [
        make("search-only", SEARCH),
        make("enrich-only", ENRICH, cost_class=CostClass.PAID),
        make("dual", BOTH),
    ]

    results = await orchestrator(classes, probe, resolve=synthetic).run(REQUEST)

    assert {r.resolved_mode for r in results} == {DataMode.SYNTHETIC}
    called = {r.source_name for r in results if r.outcome.succeeded > 0}
    assert called == {"search-only", "enrich-only", "dual"}
    assert labels(probe.requests["enrich-only:enrich"]) == ["dual-0", "search-only-0"]


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_an_enrichment_failure_is_isolated_and_keeps_discovery_results() -> None:
    probe = Probe()
    classes = [
        make("s", SEARCH),
        make("bad", ENRICH, fail_unauthorized=True, fail_phase="enrich"),
        make("good", ENRICH),
    ]

    results = await orchestrator(classes, probe).run(REQUEST)

    by = {(r.source_name, r.phase): r for r in results}
    assert by[("s", Phase.DISCOVERY)].outcome.status is SourceStatus.OK
    assert by[("s", Phase.DISCOVERY)].contributions is not None
    assert by[("bad", Phase.ENRICHMENT)].outcome.status is SourceStatus.UNAUTHORIZED
    assert by[("good", Phase.ENRICHMENT)].outcome.status is SourceStatus.OK


# Verifies: specs/lead-source-adapters/requirements.md#6.2
async def test_a_source_halted_in_discovery_is_skipped_not_called_in_enrichment() -> (
    None
):
    probe = Probe()
    classes = [
        make("dual", BOTH, fail_unauthorized=True, fail_phase="search"),
        make("s", SEARCH),
    ]

    results = await orchestrator(classes, probe).run(REQUEST)

    assert "start:dual:enrich" not in probe.events
    assert "start:dual:search" in probe.events
    skipped = next(
        r for r in results if (r.source_name, r.phase) == ("dual", Phase.ENRICHMENT)
    )
    assert skipped.outcome.status is SourceStatus.UNAUTHORIZED
    assert skipped.outcome.skipped == 1
    assert skipped.batch is None


# Verifies: specs/lead-source-adapters/requirements.md#6.7
async def test_the_pool_bound_holds_across_both_phases() -> None:
    probe = Probe()
    classes = [make(f"s{i}", SEARCH) for i in range(4)] + [
        make(f"e{i}", ENRICH) for i in range(4)
    ]

    await orchestrator(classes, probe, bound=2).run(REQUEST)

    assert probe.peak == 2


# Verifies: specs/lead-source-adapters/requirements.md#6.6
async def test_one_deadline_spans_both_phases_unreached_enrichment_times_out() -> None:
    probe = Probe()
    classes = [
        make("slow", SEARCH, hang_phase="search"),
        make("fast", SEARCH),
        make("e", ENRICH),
    ]

    results = await orchestrator(classes, probe, run_timeout_s=0.1).run(REQUEST)

    by = {(r.source_name, r.phase): r for r in results}
    assert probe.cancelled == ["slow"]
    assert by[("slow", Phase.DISCOVERY)].outcome.status is SourceStatus.TIMED_OUT
    assert by[("fast", Phase.DISCOVERY)].outcome.status is SourceStatus.OK
    enrichment = by[("e", Phase.ENRICHMENT)]
    assert enrichment.outcome.status is SourceStatus.TIMED_OUT
    assert enrichment.outcome.attempted == 0
    assert "start:e:enrich" not in probe.events


# Verifies: specs/lead-source-adapters/requirements.md#6.6
async def test_an_enrichment_source_in_flight_at_the_deadline_is_cancelled() -> None:
    probe = Probe()
    classes = [make("s", SEARCH), make("e", ENRICH, hang_phase="enrich")]

    results = await orchestrator(classes, probe, run_timeout_s=0.1).run(REQUEST)

    by = {(r.source_name, r.phase): r for r in results}
    assert probe.cancelled == ["e"]
    assert by[("s", Phase.DISCOVERY)].outcome.status is SourceStatus.OK
    assert by[("e", Phase.ENRICHMENT)].outcome.status is SourceStatus.TIMED_OUT


# Verifies: specs/lead-source-adapters/requirements.md#6.6
async def test_a_halted_source_stays_halted_when_the_deadline_hits_enrichment() -> None:
    probe = Probe()
    classes = [
        make("dual", BOTH, fail_unauthorized=True, fail_phase="search"),
        make("s", SEARCH),
        make("a-hang", ENRICH, hang_phase="enrich"),
    ]

    # One slot: "a-hang" holds it, so "dual" is still waiting for it at the deadline.
    results = await orchestrator(classes, probe, bound=1, run_timeout_s=0.1).run(
        REQUEST
    )

    dual = [r for r in results if r.source_name == "dual"]
    assert [r.outcome.status for r in dual] == [SourceStatus.UNAUTHORIZED] * 2
    assert dual[-1].outcome.failed == 1


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_enrichment_waits_for_a_slow_and_a_failing_discovery_source() -> None:
    probe = Probe()
    classes = [
        make("a-fail", SEARCH, fail_unauthorized=True),
        make("b-slow", SEARCH, ticks=200),
        make("c-fast", SEARCH),
        make("d-enrich", ENRICH),
    ]

    await orchestrator(classes, probe, bound=1).run(REQUEST)

    assert probe.events.index("end:b-slow:search") < probe.events.index(
        "start:d-enrich:enrich"
    )
    assert probe.events.index("end:c-fast:search") < probe.events.index(
        "start:d-enrich:enrich"
    )
    assert labels(probe.requests["d-enrich:enrich"]) == ["b-slow-0", "c-fast-0"]
