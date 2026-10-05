"""Per-source counts and failure classes on the run record (task 18.2, Req 21.2).

Pure builder, repository, and the real orchestrator against a real SQLite store.
"""

import asyncio
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    LeadContribution,
    LiveAccess,
    RateBucket,
    RateWindow,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.errors import (
    NormalizationError,
    SourceComplianceRestricted,
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    Phase,
    SourceOutcome,
    SourceResult,
    SourceStatus,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.run_record import (
    RunRecordError,
    SourceCounts,
    build_source_counts,
)
from leadforge.lead_ingestion.run_recorder import StoreRunRecorder
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.run_records import RunRecordRepository
from leadforge.lead_ingestion.store.transactions import StoreWriter
from leadforge.lead_ingestion.throttle import ThrottleSnapshot

REQUEST = SourceRequest(kind="search")
T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
CANARY = "canary.person@example.com"


def outcome(
    name: str,
    status: SourceStatus = SourceStatus.OK,
    *,
    retries: int = 0,
    error: str | None = None,
) -> SourceOutcome:
    return SourceOutcome(name, status, 1, 1, 0, 0, retries, error)


def contribution(name: str) -> LeadContribution:
    return LeadContribution(source_name=name)


def result(
    name: str,
    *,
    phase: Phase = Phase.DISCOVERY,
    leads: int = 0,
    out: SourceOutcome | None = None,
    throttle: ThrottleSnapshot | None = None,
    allowances: dict[str, int] | None = None,
    contributions: bool = True,
) -> SourceResult:
    return SourceResult(
        name,
        DataMode.SYNTHETIC,
        "r",
        None,
        tuple(contribution(name) for _ in range(leads)) if contributions else None,
        out or outcome(name),
        phase,
        throttle=throttle,
        allowances=allowances,
    )


# -- pure builder ---------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_an_ok_source_has_no_failure_class_and_counts_its_leads() -> None:
    (counts,) = build_source_counts((result("alpha", leads=3),))
    assert counts == SourceCounts(
        source_name="alpha",
        failure_class=None,
        leads_found=3,
        retries=0,
        throttle_waits=0,
        http_429_count=0,
        quota_remaining=None,
        warnings=None,
    )


# Verifies: specs/lead-source-adapters/requirements.md#21.2
@pytest.mark.parametrize(
    "status", [s for s in SourceStatus if s is not SourceStatus.OK]
)
def test_a_failed_source_carries_its_class_and_its_outcome_message(
    status: SourceStatus,
) -> None:
    failed = result(
        "alpha",
        out=outcome("alpha", status, retries=2, error="[alpha] boom"),
        contributions=False,
    )
    (counts,) = build_source_counts((failed,))
    assert counts.failure_class == status.value
    assert counts.retries == 2
    assert counts.leads_found == 0
    assert counts.warnings == ["[alpha] boom"]


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_a_source_in_both_phases_is_one_row_summing_leads_from_its_latest_ledger() -> (
    None
):
    discovery = result("alpha", leads=2, out=outcome("alpha", retries=1))
    enrichment = result(
        "alpha", phase=Phase.ENRICHMENT, leads=3, out=outcome("alpha", retries=4)
    )
    (counts,) = build_source_counts((discovery, enrichment, result("bravo", leads=1)))[
        :1
    ]
    assert counts.source_name == "alpha"
    assert counts.leads_found == 5  # contributions of every phase
    assert counts.retries == 4  # the ledger is cumulative: the later one is the run's


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_throttle_figures_and_the_provider_stated_allowances_are_kept() -> None:
    snap = ThrottleSnapshot(
        "alpha",
        throttle_waits=3,
        retries=0,
        throttled_responses=2,
        retry_after_capped=0,
    )
    (counts,) = build_source_counts(
        (result("alpha", throttle=snap, allowances={"minute": 7, "day": 40}),)
    )
    assert (counts.throttle_waits, counts.http_429_count) == (3, 2)
    assert counts.quota_remaining == {"minute": 7, "day": 40}


# Verifies: specs/lead-source-adapters/requirements.md#21.2
@pytest.mark.parametrize("stated", [None, {}])
def test_no_provider_stated_allowance_is_unknown_not_zero(
    stated: dict[str, int] | None,
) -> None:
    (counts,) = build_source_counts((result("alpha", allowances=stated),))
    assert counts.quota_remaining is None


# Verifies: specs/lead-source-adapters/requirements.md#21.2
@pytest.mark.parametrize("status", list(SourceStatus))
def test_every_source_status_has_a_failure_class_and_only_ok_has_none(
    status: SourceStatus,
) -> None:
    (counts,) = build_source_counts((result("alpha", out=outcome("alpha", status)),))
    assert counts.failure_class == (None if status is SourceStatus.OK else status.value)


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_results_come_back_ordered_by_source_name() -> None:
    names = [
        c.source_name for c in build_source_counts((result("bravo"), result("alpha")))
    ]
    assert names == ["alpha", "bravo"]


# -- repository -----------------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    with engine.begin() as conn:
        upgrade_to_head(conn)
    yield engine
    engine.dispose()


def source_rows(engine: Engine) -> dict[str, m.SourceRun]:
    with Session(engine) as s:
        rows = s.scalars(sa.select(m.SourceRun)).all()
        s.expunge_all()
    return {r.source_name: r for r in rows}


def counts_of(name: str, **kw: Any) -> SourceCounts:
    args: dict[str, Any] = {
        "source_name": name,
        "failure_class": None,
        "leads_found": 0,
        "retries": 0,
        "throttle_waits": 0,
        "http_429_count": 0,
        "quota_remaining": None,
        "warnings": None,
    }
    args.update(kw)
    return SourceCounts(**args)


async def start_run(engine: Engine, *names: str) -> uuid.UUID:
    from leadforge.lead_ingestion.run_record import RunRecord, SourceMode

    record = RunRecord(
        T0, 2, {}, tuple(SourceMode(n, DataMode.SYNTHETIC, "r") for n in names)
    )
    return await StoreWriter(engine).write_batch(
        lambda s: RunRecordRepository(s).start(record)
    )


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_the_repository_writes_each_sources_counts_to_its_own_row(
    engine: Engine,
) -> None:
    run_id = await start_run(engine, "alpha", "bravo")
    await StoreWriter(engine).write_batch(
        lambda s: RunRecordRepository(s).record_source_counts(
            run_id,
            (
                counts_of(
                    "alpha",
                    failure_class="rate_limited",
                    leads_found=4,
                    retries=2,
                    throttle_waits=1,
                    http_429_count=3,
                    quota_remaining={"api": 5},
                    warnings=["w"],
                ),
                counts_of("bravo"),
            ),
        )
    )
    rows = source_rows(engine)
    a = rows["alpha"]
    assert (a.failure_class, a.leads_found, a.retries, a.throttle_waits) == (
        "rate_limited",
        4,
        2,
        1,
    )
    assert (a.http_429_count, a.quota_remaining, a.warnings) == (3, {"api": 5}, ["w"])
    assert a.credits_consumed is None  # no adapter reports Credits: unknown, not 0
    assert (rows["bravo"].failure_class, rows["bravo"].leads_found) == (None, 0)


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_counts_for_a_source_the_run_never_listed_are_refused(
    engine: Engine,
) -> None:
    run_id = await start_run(engine, "alpha")
    with pytest.raises(RunRecordError, match="unknown source"):
        await StoreWriter(engine).write_batch(
            lambda s: RunRecordRepository(s).record_source_counts(
                run_id, (counts_of("ghost"),)
            )
        )


# -- the real orchestrator ------------------------------------------------------


class Plan:
    """What each source does on fetch and normalize."""

    def __init__(self) -> None:
        self.fetch: dict[str, Callable[[BaseLeadSource], Any]] = {}
        self.leads: dict[str, int] = {}
        self.normalize_error: dict[str, SourceError] = {}


class Scripted(BaseLeadSource):
    name: ClassVar[str] = "scripted"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[dict[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[dict[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[dict[str, object]] = {}
    endpoints: ClassVar[dict[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()

    def __init__(self, mode: DataMode, plan: Plan, pacing: SourcePacing | None) -> None:
        super().__init__(mode)
        self.plan = plan
        self.pacing = pacing

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        action = self.plan.fetch.get(self.name)
        if action is not None:
            outcome_ = action(self)
            if asyncio.iscoroutine(outcome_):
                await outcome_
        return RawBatch(source_name=self.name, payload={})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        error = self.plan.normalize_error.get(self.name)
        if error is not None:
            raise error
        return [LeadContribution(source_name=self.name)] * self.plan.leads.get(
            self.name, 0
        )


def make(
    engine: Engine,
    plan: Plan,
    *,
    names: tuple[str, ...] = ("alpha", "bravo"),
    modes: dict[str, DataMode] | None = None,
    classes: dict[str, dict[str, Any]] | None = None,
    settings: dict[str, SourceSettings] | None = None,
    pool: int = 3,
    timeout: float = 30,
    retry: RetryPolicy | None = None,
    recorder: object | None = "default",
) -> IngestionOrchestrator:
    attrs = classes or {}
    built = [
        type(f"S_{n}", (Scripted,), {"name": n, **attrs.get(n, {})}) for n in names
    ]
    chosen = modes or {}

    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        assert issubclass(source_class, Scripted)
        return source_class(mode, plan, pacing)

    def resolve(
        source_class: type[BaseLeadSource], _s: SourceSettings
    ) -> ModeResolution:
        return ModeResolution(chosen.get(source_class.name, DataMode.SYNTHETIC), "why")

    if recorder == "default":
        recorder = StoreRunRecorder(StoreWriter(engine), clock=lambda: T0)
    return IngestionOrchestrator(
        SourceRegistry(built, config=settings),
        resolve_mode=resolve,
        build_source=build,
        max_concurrent_sources=pool,
        run_timeout_s=timeout,
        retry_policy=retry,
        run_recorder=recorder,  # type: ignore[arg-type]
    )


def raiser(error: SourceError) -> Callable[[BaseLeadSource], None]:
    def go(_s: BaseLeadSource) -> None:
        raise error

    return go


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_a_finished_run_persists_each_sources_leads_and_failure_class(
    engine: Engine,
) -> None:
    plan = Plan()
    plan.leads = {"alpha": 3}
    plan.fetch = {"bravo": raiser(SourceUnauthorized("bravo", endpoint="/x"))}
    await make(engine, plan).run(REQUEST)

    rows = source_rows(engine)
    assert (rows["alpha"].leads_found, rows["alpha"].failure_class) == (3, None)
    assert (rows["bravo"].leads_found, rows["bravo"].failure_class) == (
        0,
        "unauthorized",
    )
    assert rows["bravo"].warnings == ["[bravo] SourceUnauthorized: endpoint=/x"]


# Verifies: specs/lead-source-adapters/requirements.md#21.2
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SourceQuotaExhausted("alpha"), "quota_exhausted"),
        (SourceRateLimited("alpha", cause="http_429"), "rate_limited"),
        (
            NormalizationError("alpha", raw_field_path="a", canonical_path="b"),
            "normalization_failed",
        ),
        (SourceComplianceRestricted("alpha", subject=CANARY), "compliance_restricted"),
        (SourceError("alpha", "other"), "failed"),
    ],
)
async def test_each_failure_class_reaches_the_row(
    engine: Engine, error: SourceError, expected: str
) -> None:
    plan = Plan()
    plan.fetch = {"alpha": raiser(error)}
    await make(engine, plan, names=("alpha",)).run(REQUEST)
    assert source_rows(engine)["alpha"].failure_class == expected


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_a_failure_in_normalization_is_classed_and_counts_no_leads(
    engine: Engine,
) -> None:
    plan = Plan()
    plan.leads = {"alpha": 5}
    plan.normalize_error = {
        "alpha": NormalizationError("alpha", raw_field_path="a", canonical_path="b")
    }
    await make(engine, plan, names=("alpha",)).run(REQUEST)
    row = source_rows(engine)["alpha"]
    assert (row.failure_class, row.leads_found) == ("normalization_failed", 0)


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_a_source_that_never_started_before_the_run_timeout_is_recorded(
    engine: Engine,
) -> None:
    plan = Plan()

    async def hang(_s: BaseLeadSource) -> None:
        await asyncio.Event().wait()

    plan.fetch = {"alpha": hang}
    await make(engine, plan, pool=1, timeout=0.2).run(REQUEST)
    rows = source_rows(engine)
    assert rows["alpha"].failure_class == "timed_out"  # cancelled in flight
    assert rows["bravo"].failure_class == "timed_out"  # never got a slot
    assert rows["bravo"].leads_found == 0


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_a_halted_source_keeps_its_halting_class(engine: Engine) -> None:
    plan = Plan()
    plan.fetch = {"alpha": raiser(SourceQuotaExhausted("alpha"))}
    await make(engine, plan, names=("alpha",)).run(REQUEST)
    assert source_rows(engine)["alpha"].failure_class == "quota_exhausted"


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_retries_and_throttle_figures_of_a_live_source_are_persisted(
    engine: Engine,
) -> None:
    plan = Plan()
    calls = {"n": 0}

    async def paced(source: BaseLeadSource) -> None:
        assert isinstance(source, Scripted)
        assert source.pacing is not None
        bucket = source.pacing.throttle.bucket("api")
        await bucket.acquire()
        await bucket.acquire()  # the window holds one request: this one waits
        calls["n"] += 1
        if calls["n"] == 1:
            bucket.note_rate_limited(None)
            raise SourceRateLimited("alpha", cause="http_429")

    plan.fetch = {"alpha": paced}
    bucket = RateBucket("api", (RateWindow(1, 0.01),), False, "")
    await make(
        engine,
        plan,
        names=("alpha",),
        modes={"alpha": DataMode.LIVE},
        classes={"alpha": {"rate_limit": {"api": bucket}}},
        retry=RetryPolicy(max_attempts=2, base_delay_s=0.001, max_delay_s=0.001),
    ).run(REQUEST)

    row = source_rows(engine)["alpha"]
    assert row.failure_class is None  # the retry succeeded
    assert row.retries == 1
    assert row.http_429_count == 1
    assert row.throttle_waits >= 1
    # The local limiter's tokens are not a provider-stated quota: never reported as one.
    assert row.quota_remaining is None


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_a_synthetic_source_has_zero_throttle_figures_and_unknown_credits(
    engine: Engine,
) -> None:
    await make(engine, Plan(), names=("alpha",)).run(REQUEST)
    row = source_rows(engine)["alpha"]
    assert (row.throttle_waits, row.http_429_count, row.retries) == (0, 0, 0)
    assert row.quota_remaining is None
    assert row.credits_consumed is None


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_the_live_access_classification_is_recorded_for_every_source(
    engine: Engine,
) -> None:
    await make(
        engine,
        Plan(),
        names=("alpha", "bravo", "charlie"),
        classes={
            "alpha": {"live_access": LiveAccess.AVAILABLE},
            "bravo": {"live_access": LiveAccess.UNAVAILABLE},
            "charlie": {"live_access": LiveAccess.UNAVAILABLE},
        },
        settings={"charlie": SourceSettings(live_access=LiveAccess.GATED)},
    ).run(REQUEST)
    rows = source_rows(engine)
    assert rows["alpha"].live_access is True
    assert rows["bravo"].live_access is False  # synthetic-only by necessity
    assert rows["charlie"].live_access is True  # the configured override wins


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_an_enrichment_source_with_no_work_keeps_its_row_with_no_failure(
    engine: Engine,
) -> None:
    plan = Plan()
    await make(
        engine,
        plan,
        classes={"bravo": {"capabilities": frozenset({Capability.ENRICH})}},
    ).run(REQUEST)
    row = source_rows(engine)["bravo"]
    assert (row.failure_class, row.leads_found) == (None, 0)


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_an_error_carrying_a_person_never_reaches_the_database(
    engine: Engine,
) -> None:
    plan = Plan()
    plan.fetch = {"alpha": raiser(SourceComplianceRestricted("alpha", subject=CANARY))}
    await make(engine, plan, names=("alpha",)).run(REQUEST)
    row = source_rows(engine)["alpha"]
    assert row.failure_class == "compliance_restricted"
    with engine.connect() as conn:
        dump = "\n".join(conn.connection.driver_connection.iterdump())  # type: ignore[union-attr]
    assert CANARY not in dump
    assert "canary" not in dump.lower()


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_counts_and_the_finish_are_one_transaction(engine: Engine) -> None:
    run_id = await start_run(engine, "alpha")
    recorder = StoreRunRecorder(StoreWriter(engine), clock=lambda: T0)
    with pytest.raises(RunRecordError, match="unknown source"):
        await recorder.finish(run_id, (result("ghost"),))
    with Session(engine) as s:
        run = s.get(m.IngestionRun, run_id)
        assert run is not None
        assert (run.status, run.finished_at) == ("running", None)  # rolled back too


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_an_aborted_run_keeps_the_start_rows_without_counts(
    engine: Engine,
) -> None:
    plan = Plan()

    def crash(_s: BaseLeadSource) -> None:
        raise RuntimeError("programming error")

    plan.fetch = {"alpha": crash}
    with pytest.raises(ExceptionGroup):
        await make(engine, plan).run(REQUEST)
    rows = source_rows(engine)
    assert set(rows) == {"alpha", "bravo"}
    assert all(r.failure_class is None and r.leads_found == 0 for r in rows.values())
    with Session(engine) as s:
        assert s.scalar(sa.select(m.IngestionRun.status)) == "aborted"


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_the_finish_and_the_counts_commit_together(engine: Engine) -> None:
    plan = Plan()
    plan.leads = {"alpha": 2}
    await make(engine, plan, names=("alpha",)).run(REQUEST)
    with Session(engine) as s:
        run = s.scalars(sa.select(m.IngestionRun)).one()
        assert run.status == "completed"
        assert run.finished_at is not None
    assert source_rows(engine)["alpha"].leads_found == 2


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_a_provider_stated_allowance_is_persisted(engine: Engine) -> None:
    await make(
        engine,
        Plan(),
        names=("alpha", "bravo"),
        classes={
            "alpha": {"allowances": property(lambda _s: {"minute": 7, "day": 40})}
        },
    ).run(REQUEST)
    rows = source_rows(engine)
    assert rows["alpha"].quota_remaining == {"minute": 7, "day": 40}
    assert rows["bravo"].quota_remaining is None


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_the_three_valued_live_access_is_kept_in_the_run_snapshot(
    engine: Engine,
) -> None:
    await make(
        engine,
        Plan(),
        names=("alpha", "bravo", "charlie"),
        classes={
            "alpha": {"live_access": LiveAccess.AVAILABLE},
            "bravo": {"live_access": LiveAccess.GATED},
            "charlie": {"live_access": LiveAccess.UNAVAILABLE},
        },
    ).run(REQUEST)
    with Session(engine) as s:
        snapshot = s.scalars(sa.select(m.IngestionRun.config_snapshot)).one()
    assert {n: v["live_access"] for n, v in snapshot["sources"].items()} == {
        "alpha": "available",
        "bravo": "gated",
        "charlie": "unavailable",
    }
