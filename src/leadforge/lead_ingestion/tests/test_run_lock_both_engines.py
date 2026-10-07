"""One ingestion run at a time, and an aborted merge never loses what was spent.

Follow-up (2026-10-06) to Requirements 6.4, 8.12, 21.1 and 21.2. Two simultaneous runs
saving the same record used to abort the later one AFTER it had spent credits, losing
its raw payloads, contributions and spend counts. Now:

* a run takes the store's run lock BEFORE any provider call; a second run refuses to
  start (``RunInProgressError``, no value in the message), records nothing and calls
  no source; a lock whose lease expired (a crashed run) is taken over;
* raw payloads, contributions and per-source counts commit in their own transaction
  BEFORE the merge, so a merge that fails (or a run that lost its lock) keeps them,
  and the next run merges them;
* the lease is on the DATABASE server's clock (follow-up fu3): every lease instant is
  written and compared in the statement itself, so a host whose clock is skewed can
  neither steal a live lock nor hold a dead one.

Every test runs on SQLite and on PostgreSQL, with two real runs in two threads.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

import asyncio
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from leadforge.lead_ingestion import cli, ingest_runner
from leadforge.lead_ingestion.database import database_now
from leadforge.lead_ingestion.ingest_runner import IngestionOutcome, run_ingestion
from leadforge.lead_ingestion.orchestrator import IngestionOrchestrator
from leadforge.lead_ingestion.store import merged_leads, run_lock
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.run_lock import (
    LOCK_STALE_GRACE_S,
    RunInProgressError,
    RunLockLostError,
    release_run_lock,
    renew_run_lock,
    try_acquire_run_lock,
)
from leadforge.lead_ingestion.tests.store_run_support import (
    Script,
    active_leads,
    count,
    person,
    registry_of,
    scripted_source,
)
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)

ADA = person("ada@example.com", "Ada Lovelace")
GRACE = person("grace@example.com", "Grace Hopper")
T = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
LEASE = 600.0
SKEW = timedelta(hours=6)


# ------------------------------------------------------------------ the lock itself


def _acquire(backend: Backend, holder: uuid.UUID) -> Any:
    with Session(backend.engine) as s, s.begin():
        return try_acquire_run_lock(s, holder, lease_s=LEASE)


def _plant(backend: Backend, expires_in: timedelta) -> None:
    """Another run's lock, its lease ending ``expires_in`` from now (this machine is
    also the test database's host, so its clock is the database's)."""
    now = datetime.now(UTC)
    with Session(backend.engine) as s, s.begin():
        s.execute(
            sa.update(m.RunLock).values(
                holder=uuid.uuid4(), acquired_at=now, expires_at=now + expires_in
            )
        )


def _expire(backend: Backend) -> None:
    """The holder's lease ran out on the database clock (it crashed or stalled)."""
    with Session(backend.engine) as s, s.begin():
        s.execute(sa.update(m.RunLock).values(expires_at=T - timedelta(days=3650)))


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_the_lock_admits_one_holder_until_released_or_stale(backend: Backend) -> None:
    first, second = uuid.uuid4(), uuid.uuid4()

    assert _acquire(backend, first) is None  # acquired
    held = _acquire(backend, second)
    assert held is not None
    assert held.acquired_at is not None
    assert held.expires_at is not None
    assert held.expires_at - held.acquired_at == timedelta(seconds=LEASE)
    # Stamped by the database server, which is this test's host here.
    assert abs(held.acquired_at - datetime.now(UTC)) < timedelta(minutes=1)
    # Re-entry by the holder itself is refused too: one run, one acquisition.
    assert _acquire(backend, first) is not None

    with Session(backend.engine) as s, s.begin():
        release_run_lock(s, first)
    assert _acquire(backend, second) is None

    # A crashed holder never releases: once its lease ran out it is taken over.
    third = uuid.uuid4()
    assert _acquire(backend, third) is not None
    _expire(backend)
    assert _acquire(backend, third) is None


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_a_holder_whose_lock_was_taken_over_cannot_renew_or_release_it(
    backend: Backend,
) -> None:
    crashed, taker = uuid.uuid4(), uuid.uuid4()
    assert _acquire(backend, crashed) is None
    _expire(backend)
    assert _acquire(backend, taker) is None

    with Session(backend.engine) as s, s.begin():
        with pytest.raises(RunLockLostError):
            renew_run_lock(s, crashed, lease_s=LEASE)
        release_run_lock(s, crashed)  # a no-op: it is not the holder
    assert _acquire(backend, uuid.uuid4()) is not None


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_renewing_extends_the_lease_on_the_database_clock(backend: Backend) -> None:
    holder = uuid.uuid4()
    assert _acquire(backend, holder) is None
    _expire(backend)
    with Session(backend.engine) as s, s.begin():
        renew_run_lock(s, holder, lease_s=LEASE)
    assert _acquire(backend, uuid.uuid4()) is not None  # live again


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_the_lease_clock_is_the_statements_instant_not_the_transactions_start(
    backend: Backend,
) -> None:
    """A lock statement late in a long transaction still reads the current instant
    (PostgreSQL's ``CURRENT_TIMESTAMP`` would be the transaction's start)."""
    with Session(backend.engine) as s, s.begin():
        first = s.scalar(sa.select(database_now()))
        time.sleep(0.3)
        later = s.scalar(sa.select(database_now()))
    assert first is not None
    assert later is not None
    assert later - first >= timedelta(seconds=0.25)


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_racing_acquirers_in_threads_get_exactly_one_lock(backend: Backend) -> None:
    holders = [uuid.uuid4() for _ in range(6)]
    won: list[uuid.UUID] = []
    start = threading.Barrier(len(holders))

    def contend(holder: uuid.UUID) -> None:
        start.wait()
        if _acquire(backend, holder) is None:
            won.append(holder)

    threads = [threading.Thread(target=contend, args=(h,)) for h in holders]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(won) == 1


def test_the_stale_grace_is_documented_and_generous() -> None:
    assert LOCK_STALE_GRACE_S >= 15 * 60
    assert "another run in progress" in str(
        RunInProgressError(acquired_at=T, expires_at=T + timedelta(hours=1))
    )


# -------------------------------------------------------- two real runs, two threads


def _in_thread(coro_factory: Any) -> tuple[threading.Thread, list[Any]]:
    box: list[Any] = []

    def target() -> None:
        try:
            box.append(asyncio.run(coro_factory()))
        except BaseException as error:  # noqa: BLE001 - handed to the test
            box.append(error)

    thread = threading.Thread(target=target)
    thread.start()
    return thread, box


# Verifies: specs/lead-source-adapters/requirements.md#6.4
# Verifies: specs/lead-source-adapters/requirements.md#8.12
async def test_a_second_concurrent_run_refuses_to_start_and_spends_nothing(
    composed: Backend,
) -> None:
    gate = threading.Event()
    first = Script([ADA], gate=gate)
    second = Script([ADA])
    thread, box = _in_thread(
        lambda: run_ingestion(registry=registry_of(scripted_source("alpha", first)))
    )
    try:
        assert first.entered.wait(30)  # the first run is inside its provider call

        with pytest.raises(RunInProgressError) as refused:
            await run_ingestion(registry=registry_of(scripted_source("alpha", second)))
    finally:
        gate.set()
        thread.join(60)

    assert second.calls == []  # no provider call: nothing spent
    message = str(refused.value)
    assert "another run in progress" in message
    assert "ada" not in message.casefold()
    [outcome] = box
    assert isinstance(outcome, IngestionOutcome)
    assert (
        count(composed.engine, m.IngestionRun) == 1
    )  # the refused run recorded nothing
    assert len(active_leads(composed.engine)) == 1
    # Released: the next run starts and adds no duplicate lead.
    await run_ingestion(registry=registry_of(scripted_source("alpha", Script([ADA]))))
    assert len(active_leads(composed.engine)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#6.4
async def test_a_crashed_runs_stale_lock_is_taken_over(composed: Backend) -> None:
    _acquire(composed, uuid.uuid4())
    _expire(composed)
    outcome = await run_ingestion(
        registry=registry_of(scripted_source("alpha", Script([ADA])))
    )
    assert outcome.exit.exit_code == 0

    _acquire(composed, uuid.uuid4())
    with pytest.raises(RunInProgressError):
        await run_ingestion(
            registry=registry_of(scripted_source("alpha", Script([ADA])))
        )


# Verifies: specs/lead-source-adapters/requirements.md#21.2
# Verifies: specs/lead-source-adapters/requirements.md#8.12
async def test_a_run_that_lost_its_lock_keeps_its_spend_and_the_next_run_merges_it(
    composed: Backend,
) -> None:
    """Run A stalls past its lease; run B takes the lock over and completes; A then
    finishes its calls: its payloads, contributions and counts commit, its merge is
    refused (the lock is B's now), and run C merges A's records with no duplicate."""
    gate = threading.Event()
    stalled = Script([ADA, GRACE], gate=gate)
    thread, box = _in_thread(
        lambda: run_ingestion(registry=registry_of(scripted_source("alpha", stalled)))
    )
    try:
        assert stalled.entered.wait(30)
        _expire(composed)  # A's lease has run out on the database clock
        await run_ingestion(
            registry=registry_of(scripted_source("beta", Script([ADA])))
        )
    finally:
        gate.set()
        thread.join(60)

    [lost] = box
    assert isinstance(lost, RunLockLostError)
    with Session(composed.engine) as s:
        runs = s.scalars(sa.select(m.IngestionRun)).all()
        [a_run] = [r for r in runs if r.status == "aborted"]
        assert a_run.failure_reason == "merge: RunLockLostError"
        [a_source] = s.scalars(
            sa.select(m.SourceRun).where(m.SourceRun.run_id == a_run.id)
        ).all()
        assert (a_source.attempted, a_source.succeeded) == (1, 1)  # spend kept
        assert a_source.contributions_written == 2
        assert (
            s.scalar(
                sa.select(sa.func.count(m.RawResponse.id)).where(
                    m.RawResponse.source_run_id == a_source.id
                )
            )
            == 1
        )
    assert {lead.email for lead in active_leads(composed.engine)} == {"ada@example.com"}

    await run_ingestion(registry=registry_of(scripted_source("alpha", Script([]))))
    leads = active_leads(composed.engine)
    assert sorted(lead.email or "" for lead in leads) == [
        "ada@example.com",
        "grace@example.com",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#6.4
@pytest.mark.parametrize("skew", [SKEW, -SKEW], ids=["host-ahead", "host-behind"])
async def test_a_skewed_host_clock_changes_no_lease_decision(
    composed: Backend, monkeypatch: pytest.MonkeyPatch, skew: timedelta
) -> None:
    """The host's Python clock is off by hours; only the database clock decides."""
    real_now = datetime.now

    class Skewed(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return real_now(tz) + skew

    for module in (ingest_runner, run_lock):  # the run's clock and the lock's module
        monkeypatch.setattr(module, "datetime", Skewed)

    # Live on the database clock for ten more minutes: never stolen, even by a host
    # that believes it expired hours ago.
    _plant(composed, timedelta(minutes=10))
    with pytest.raises(RunInProgressError):
        await run_ingestion(registry=registry_of(scripted_source("a", Script([ADA]))))

    # Dead on the database clock a minute ago: taken over, even by a host that
    # believes it has hours to run.
    _plant(composed, -timedelta(minutes=1))
    outcome = await run_ingestion(
        registry=registry_of(scripted_source("a", Script([ADA])))
    )
    assert outcome.exit.exit_code == 0


def _lock_holder(backend: Backend) -> uuid.UUID | None:
    with Session(backend.engine) as s:
        return s.scalars(sa.select(m.RunLock.holder)).one()


# Verifies: specs/lead-source-adapters/requirements.md#6.4
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, asyncio.CancelledError])
async def test_an_interrupted_run_releases_the_lock(
    composed: Backend, monkeypatch: pytest.MonkeyPatch, interrupt: type[BaseException]
) -> None:
    async def interrupted(*_: Any, **__: Any) -> Any:
        assert _lock_holder(composed) is not None  # held while the sources run
        raise interrupt

    monkeypatch.setattr(IngestionOrchestrator, "run", interrupted)
    with pytest.raises(interrupt):
        await run_ingestion(registry=registry_of(scripted_source("alpha", Script([]))))
    assert _lock_holder(composed) is None


_SPEND_STAGES = ("persist_merge", "record_projection", "finish")


# Verifies: specs/lead-source-adapters/requirements.md#21.2
@pytest.mark.parametrize("stage", _SPEND_STAGES)
async def test_a_failed_merge_keeps_payloads_contributions_and_counts(
    composed: Backend, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    from leadforge.lead_ingestion.run_record import RunStatus
    from leadforge.lead_ingestion.store.run_records import RunRecordRepository

    def boom(*_: Any, **kwargs: Any) -> Any:
        if stage == "finish" and kwargs.get("status") is not RunStatus.COMPLETED:
            return real(*_, **kwargs)
        raise RuntimeError("merge broke near ada@example.com")

    target: Any = ingest_runner if stage == "persist_merge" else RunRecordRepository
    real = (
        merged_leads.persist_merge
        if stage == "persist_merge"
        else getattr(RunRecordRepository, stage)
    )
    monkeypatch.setattr(target, stage, boom)
    with pytest.raises(RuntimeError):
        await run_ingestion(
            registry=registry_of(scripted_source("alpha", Script([ADA])))
        )
    monkeypatch.setattr(target, stage, real)  # only this patch: the store stays

    with Session(composed.engine) as s:
        run = s.scalars(sa.select(m.IngestionRun)).one()
        source = s.scalars(sa.select(m.SourceRun)).one()
    assert (run.status, run.failure_reason) == ("aborted", "merge_write: RuntimeError")
    assert (source.attempted, source.contributions_written) == (1, 1)
    assert count(composed.engine, m.SourceContribution) == 1
    assert count(composed.engine, m.RawResponse) == 1
    assert count(composed.engine, m.CanonicalLeadRow) == 0

    again = await run_ingestion(
        registry=registry_of(scripted_source("alpha", Script([])))
    )
    assert again.stored.leads_created == 1
    assert [lead.email for lead in active_leads(composed.engine)] == ["ada@example.com"]
    assert count(composed.engine, m.SourceContribution) == 1


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_the_cli_reports_another_run_in_progress_and_exits_three(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def refused(**_: object) -> IngestionOutcome:
        raise RunInProgressError(acquired_at=T, expires_at=T + timedelta(hours=1))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_ingestion", refused)
    result = CliRunner().invoke(cli.app, ["ingest"])
    assert result.exit_code == cli.EXIT_RUN_IN_PROGRESS == 3
    assert "another run in progress" in result.output
