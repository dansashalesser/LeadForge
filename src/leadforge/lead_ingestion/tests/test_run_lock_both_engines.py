"""One ingestion run at a time, and an aborted merge never loses what was spent.

Follow-up (2026-10-06) to Requirements 6.4, 8.12, 21.1 and 21.2. Two simultaneous runs
saving the same record used to abort the later one AFTER it had spent credits, losing
its raw payloads, contributions and spend counts. Now:

* a run takes the store's run lock BEFORE any provider call; a second run refuses to
  start (``RunInProgressError``, no value in the message), records nothing and calls
  no source; a lock whose lease expired (a crashed run) is taken over;
* raw payloads, contributions and per-source counts commit in their own transaction
  BEFORE the merge, so a merge that fails (or a run that lost its lock) keeps them,
  and the next run merges them.

Every test runs on SQLite and on PostgreSQL, with two real runs in two threads.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from leadforge.lead_ingestion import cli, ingest_runner
from leadforge.lead_ingestion.ingest_runner import IngestionOutcome, run_ingestion
from leadforge.lead_ingestion.orchestrator import IngestionOrchestrator
from leadforge.lead_ingestion.store import merged_leads
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


# ------------------------------------------------------------------ the lock itself


def _acquire(backend: Backend, holder: uuid.UUID, now: datetime) -> Any:
    with Session(backend.engine) as s, s.begin():
        return try_acquire_run_lock(s, holder, now=now, lease_s=LEASE)


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_the_lock_admits_one_holder_until_released_or_stale(backend: Backend) -> None:
    first, second = uuid.uuid4(), uuid.uuid4()

    assert _acquire(backend, first, T) is None  # acquired
    held = _acquire(backend, second, T + timedelta(seconds=LEASE - 1))
    assert held is not None
    assert (held.acquired_at, held.expires_at) == (T, T + timedelta(seconds=LEASE))
    # Re-entry by the holder itself is refused too: one run, one acquisition.
    assert _acquire(backend, first, T) is not None

    with Session(backend.engine) as s, s.begin():
        release_run_lock(s, first)
    assert _acquire(backend, second, T) is None

    # A crashed holder never releases: once its lease ran out it is taken over.
    third = uuid.uuid4()
    assert _acquire(backend, third, T + timedelta(seconds=LEASE - 1)) is not None
    assert _acquire(backend, third, T + timedelta(seconds=LEASE + 1)) is None


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_a_holder_whose_lock_was_taken_over_cannot_renew_or_release_it(
    backend: Backend,
) -> None:
    crashed, taker = uuid.uuid4(), uuid.uuid4()
    assert _acquire(backend, crashed, T) is None
    assert _acquire(backend, taker, T + timedelta(seconds=LEASE + 1)) is None

    with Session(backend.engine) as s, s.begin():
        with pytest.raises(RunLockLostError):
            renew_run_lock(s, crashed, now=T, lease_s=LEASE)
        release_run_lock(s, crashed)  # a no-op: it is not the holder
    assert _acquire(backend, uuid.uuid4(), T + timedelta(seconds=LEASE)) is not None


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_racing_acquirers_in_threads_get_exactly_one_lock(backend: Backend) -> None:
    holders = [uuid.uuid4() for _ in range(6)]
    won: list[uuid.UUID] = []
    start = threading.Barrier(len(holders))

    def contend(holder: uuid.UUID) -> None:
        start.wait()
        if _acquire(backend, holder, T) is None:
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
    now = datetime.now(UTC)
    with Session(composed.engine) as s, s.begin():
        try_acquire_run_lock(
            s, uuid.uuid4(), now=now - timedelta(hours=3), lease_s=LEASE
        )
    outcome = await run_ingestion(
        registry=registry_of(scripted_source("alpha", Script([ADA])))
    )
    assert outcome.exit.exit_code == 0

    with Session(composed.engine) as s, s.begin():
        try_acquire_run_lock(s, uuid.uuid4(), now=now, lease_s=LEASE)
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
        later = datetime.now(UTC) + timedelta(hours=6)  # A's lease has run out by then
        await run_ingestion(
            registry=registry_of(scripted_source("beta", Script([ADA]))),
            clock=lambda: later,
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
