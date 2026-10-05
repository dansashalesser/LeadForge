"""The orchestrator persists the run record (task 18.1, Requirement 21.1).

Real ``IngestionOrchestrator`` over a real SQLite store; the sources are throwaway
subclasses. Wall time never matters: the recorder's clock is a counter.
"""

import asyncio
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

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
    RateBucket,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.errors import SourceError
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import IngestionOrchestrator, SourceResult
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.run_record import RunStatus, StoredRun
from leadforge.lead_ingestion.run_recorder import StoreRunRecorder
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.run_records import RunRecordRepository
from leadforge.lead_ingestion.store.transactions import StoreWriter

REQUEST = SourceRequest(kind="search")
T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
MODES = {
    "alpha": ModeResolution(DataMode.LIVE, "all declared credentials present"),
    "bravo": ModeResolution(DataMode.SYNTHETIC, "missing credentials: BRAVO_KEY"),
}


class Behaviour:
    """What each source does on its fetch, and what the store looked like then."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.actions: dict[str, Callable[[], None]] = {}
        self.async_actions: dict[str, Callable[[], object]] = {}
        self.calls: list[str] = []
        self.seen_at_call: list[StoredRun | None] = []

    def snapshot(self) -> StoredRun | None:
        with Session(self.engine) as s:
            run_id = s.scalar(sa.select(m.IngestionRun.id))
            return None if run_id is None else RunRecordRepository(s).get(run_id)


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

    def __init__(self, mode: DataMode, behaviour: Behaviour) -> None:
        super().__init__(mode)
        self.behaviour = behaviour

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        self.behaviour.calls.append(self.name)
        self.behaviour.seen_at_call.append(self.behaviour.snapshot())
        action = self.behaviour.actions.get(self.name)
        if action is not None:
            action()
        waiter = self.behaviour.async_actions.get(self.name)
        if waiter is not None:
            await waiter()  # type: ignore[misc]
        return RawBatch(source_name=self.name, payload={})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return []


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    with engine.begin() as conn:
        upgrade_to_head(conn)
    yield engine
    engine.dispose()


class Clock:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> datetime:
        self.n += 1
        return T0 + timedelta(seconds=self.n - 1)


def make(
    engine: Engine,
    behaviour: Behaviour,
    *,
    names: tuple[str, ...] = ("alpha", "bravo"),
    recorder: object | None = "default",
    global_mode: DataMode | None = None,
) -> IngestionOrchestrator:
    classes = [type(f"S_{n}", (Scripted,), {"name": n}) for n in names]

    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        assert issubclass(source_class, Scripted)
        return source_class(mode, behaviour)

    def resolve(
        source_class: type[BaseLeadSource], _s: SourceSettings
    ) -> ModeResolution:
        return MODES[source_class.name]

    if recorder == "default":
        recorder = StoreRunRecorder(
            StoreWriter(engine), clock=Clock(), global_mode=global_mode
        )
    return IngestionOrchestrator(
        SourceRegistry(classes),
        resolve_mode=resolve,
        build_source=build,
        max_concurrent_sources=3,
        run_timeout_s=30,
        run_recorder=recorder,  # type: ignore[arg-type]
    )


def stored_runs(engine: Engine) -> list[StoredRun]:
    with Session(engine) as s:
        ids = s.scalars(sa.select(m.IngestionRun.id)).all()
        runs = [RunRecordRepository(s).get(i) for i in ids]
    return [r for r in runs if r is not None]


def only_run(engine: Engine) -> StoredRun:
    runs = stored_runs(engine)
    assert len(runs) == 1
    return runs[0]


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_normal_run_persists_one_completed_record_with_every_mode(
    engine: Engine,
) -> None:
    behaviour = Behaviour(engine)
    await make(engine, behaviour).run(REQUEST)

    run = only_run(engine)
    assert run.status == RunStatus.COMPLETED
    assert run.exit_code == 0
    assert run.started_at == T0
    assert run.finished_at == T0 + timedelta(seconds=1)
    assert run.pool_size == 3
    assert run.config_snapshot is not None
    assert run.config_snapshot["run_timeout_s"] == 30
    assert [(s.source_name, s.mode, s.reason) for s in run.sources] == [
        ("alpha", DataMode.LIVE, "all declared credentials present"),
        ("bravo", DataMode.SYNTHETIC, "missing credentials: BRAVO_KEY"),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_the_record_is_committed_before_any_source_is_called(
    engine: Engine,
) -> None:
    behaviour = Behaviour(engine)
    await make(engine, behaviour).run(REQUEST)

    assert behaviour.calls == ["alpha", "bravo"]
    for seen in behaviour.seen_at_call:
        assert seen is not None
        assert seen.status == RunStatus.RUNNING
        assert [s.source_name for s in seen.sources] == ["alpha", "bravo"]


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_run_where_every_source_fails_is_completed_with_exit_code_1(
    engine: Engine,
) -> None:
    behaviour = Behaviour(engine)

    def fail() -> None:
        raise SourceError("x", "boom")

    behaviour.actions = {"alpha": fail, "bravo": fail}
    await make(engine, behaviour).run(REQUEST)

    run = only_run(engine)
    assert (run.status, run.exit_code) == (RunStatus.COMPLETED, 1)
    assert len(run.sources) == 2


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_run_with_no_enabled_source_still_leaves_a_record(
    engine: Engine,
) -> None:
    behaviour = Behaviour(engine)
    await make(engine, behaviour, names=()).run(REQUEST)

    run = only_run(engine)
    assert (run.status, run.exit_code, run.sources) == (RunStatus.COMPLETED, 1, ())


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_crash_mid_run_marks_the_record_aborted_and_propagates(
    engine: Engine,
) -> None:
    behaviour = Behaviour(engine)

    def crash() -> None:
        raise RuntimeError("programming error")

    behaviour.actions = {"alpha": crash}
    with pytest.raises(ExceptionGroup) as info:
        await make(engine, behaviour).run(REQUEST)
    assert info.group_contains(RuntimeError)

    run = only_run(engine)
    assert (run.status, run.exit_code) == (RunStatus.ABORTED, None)
    assert run.finished_at == T0 + timedelta(seconds=1)
    assert len(run.sources) == 2  # the modes survive the abort


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_cancelled_run_is_marked_aborted_and_stays_cancelled(
    engine: Engine,
) -> None:
    behaviour = Behaviour(engine)
    started = asyncio.Event()

    async def hang() -> None:
        started.set()
        await asyncio.Event().wait()

    behaviour.async_actions = {"alpha": hang}
    task = asyncio.ensure_future(make(engine, behaviour).run(REQUEST))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run = only_run(engine)
    assert (run.status, run.exit_code) == (RunStatus.ABORTED, None)


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_failing_start_stops_the_run_before_any_source_is_called(
    engine: Engine,
) -> None:
    behaviour = Behaviour(engine)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE source_run"))
        conn.execute(sa.text("DROP TABLE ingestion_run"))
    with pytest.raises(sa.exc.DBAPIError):
        await make(engine, behaviour).run(REQUEST)
    assert behaviour.calls == []


class FailingFinish(StoreRunRecorder):
    async def finish(
        self, run_id: uuid.UUID, results: tuple[SourceResult, ...] | None
    ) -> None:
        raise OSError("disk full")


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_failing_finish_is_not_swallowed(engine: Engine) -> None:
    behaviour = Behaviour(engine)
    recorder = FailingFinish(StoreWriter(engine), clock=Clock())
    with pytest.raises(OSError, match="disk full"):
        await make(engine, behaviour, recorder=recorder).run(REQUEST)
    assert only_run(engine).status == RunStatus.RUNNING


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_failing_abort_marker_is_noted_on_the_original_exception(
    engine: Engine,
) -> None:
    behaviour = Behaviour(engine)

    def crash() -> None:
        raise RuntimeError("programming error")

    behaviour.actions = {"alpha": crash}
    recorder = FailingFinish(StoreWriter(engine), clock=Clock())
    with pytest.raises(ExceptionGroup) as info:
        await make(engine, behaviour, recorder=recorder).run(REQUEST)
    assert any(
        "could not be marked aborted: OSError" in n for n in info.value.__notes__
    )
    assert "disk full" not in "".join(info.value.__notes__)


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_without_a_recorder_nothing_is_persisted(engine: Engine) -> None:
    behaviour = Behaviour(engine)
    await make(engine, behaviour, recorder=None).run(REQUEST)
    assert stored_runs(engine) == []
    assert behaviour.calls == ["alpha", "bravo"]


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_the_global_mode_is_in_the_snapshot(engine: Engine) -> None:
    behaviour = Behaviour(engine)
    await make(engine, behaviour, global_mode=DataMode.SYNTHETIC).run(REQUEST)
    snapshot = only_run(engine).config_snapshot
    assert snapshot is not None
    assert snapshot["global_mode"] == "synthetic"


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_two_runs_leave_two_distinct_records(engine: Engine) -> None:
    behaviour = Behaviour(engine)
    orchestrator = make(engine, behaviour)
    await asyncio.gather(orchestrator.run(REQUEST), orchestrator.run(REQUEST))
    runs = stored_runs(engine)
    assert len({r.run_id for r in runs}) == 2
    assert all(r.status == RunStatus.COMPLETED and len(r.sources) == 2 for r in runs)
