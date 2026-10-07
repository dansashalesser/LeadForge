"""Write-transaction scoping for the Lead Store (task 6.4)."""

import asyncio
import contextlib
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.transactions import StoreWriter


class BoomError(Exception):
    pass


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    upgrade_to_head(eng.url.render_as_string(hide_password=False))
    yield eng
    eng.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


def _count(engine: Engine, model: type[m.Base]) -> int:
    with Session(engine) as s:
        return s.scalar(sa.select(sa.func.count()).select_from(model)) or 0


def _add_source_run(s: Session, run_id: uuid.UUID, name: str) -> uuid.UUID:
    sr = m.SourceRun(run_id=run_id, source_name=name, resolved_mode="synthetic")
    s.add(sr)
    s.flush()
    return sr.id


def _add_contribution(s: Session, source_run_id: uuid.UUID, name: str) -> None:
    raw = m.RawResponse(
        source_run_id=source_run_id,
        endpoint_key="e",
        request_fingerprint="f",
        payload={},
        fetched_at=_now(),
    )
    s.add(raw)
    s.flush()
    s.add(
        m.SourceContribution(
            source_run_id=source_run_id,
            raw_response_id=raw.id,
            source_name=name,
            data_mode="synthetic",
            fetched_at=_now(),
            lead_scope="person",
        )
    )


def _batch(
    name: str, run_id: uuid.UUID, n: int = 3, fail_after: int | None = None
) -> Callable[[Session], int]:
    def write(s: Session) -> int:
        sr = _add_source_run(s, run_id, name)
        for i in range(n):
            if fail_after is not None and i == fail_after:
                raise BoomError
            _add_contribution(s, sr, name)
        return n

    return write


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_run_record_is_committed_at_start_and_survives_batch_failure(
    engine: Engine,
) -> None:
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running", pool_size=4)
    assert _count(engine, m.IngestionRun) == 1  # visible from another session now
    with pytest.raises(BoomError):
        await writer.write_batch(_batch("a", run_id, fail_after=2))
    with Session(engine) as s:
        run = s.get(m.IngestionRun, run_id)
        assert run is not None
        assert (run.status, run.pool_size) == ("running", 4)


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_failed_batch_rolls_back_every_row_and_run_continues(
    engine: Engine,
) -> None:
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")
    with pytest.raises(BoomError):
        await writer.write_batch(_batch("bad", run_id, fail_after=2))
    for model in (m.SourceRun, m.RawResponse, m.SourceContribution):
        assert _count(engine, model) == 0
    assert await writer.write_batch(_batch("good", run_id, n=3)) == 3
    assert _count(engine, m.SourceContribution) == 3
    assert _count(engine, m.SourceRun) == 1


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_failure_at_commit_time_rolls_back(engine: Engine) -> None:
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")

    @event.listens_for(engine, "commit")
    def _fail(_conn: object) -> None:
        raise BoomError

    with pytest.raises(BoomError):
        await writer.write_batch(_batch("a", run_id))
    event.remove(engine, "commit", _fail)
    assert _count(engine, m.SourceContribution) == 0


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_session_commit_inside_a_batch_cannot_tear_it(
    engine: Engine,
) -> None:
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")

    def sneaky(s: Session) -> None:
        sr = _add_source_run(s, run_id, "a")
        _add_contribution(s, sr, "a")
        s.commit()  # would split the batch into two transactions
        raise BoomError

    with pytest.raises(BoomError):
        await writer.write_batch(sneaky)
    assert _count(engine, m.SourceContribution) == 0
    assert _count(engine, m.SourceRun) == 0


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_cancel_mid_batch_never_leaves_a_torn_write(engine: Engine) -> None:
    """Cancellation arrives while the batch is half-written: the batch runs to
    completion and commits whole, and the cancellation is still delivered."""
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")
    midway = threading.Event()

    def slow(s: Session) -> int:
        sr = _add_source_run(s, run_id, "a")
        for i in range(4):
            _add_contribution(s, sr, "a")
            if i == 1:
                midway.set()
                time.sleep(0.2)
        return 4

    task = asyncio.ensure_future(writer.write_batch(slow))
    await asyncio.to_thread(midway.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _count(engine, m.SourceContribution) == 4  # whole, not 2


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_cancel_mid_batch_then_failure_is_fully_rolled_back(
    engine: Engine,
) -> None:
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")
    midway = threading.Event()

    def slow_fail(s: Session) -> None:
        sr = _add_source_run(s, run_id, "a")
        _add_contribution(s, sr, "a")
        midway.set()
        time.sleep(0.2)
        _add_contribution(s, sr, "a")
        raise BoomError

    task = asyncio.ensure_future(writer.write_batch(slow_fail))
    await asyncio.to_thread(midway.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _count(engine, m.SourceContribution) == 0
    assert _count(engine, m.SourceRun) == 0


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_cancel_arriving_during_commit_leaves_the_batch_whole(
    engine: Engine,
) -> None:
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")
    loop = asyncio.get_running_loop()
    holder: list[asyncio.Future[int]] = []

    @event.listens_for(engine, "commit")
    def _cancel_during_commit(_conn: object) -> None:
        if holder:
            loop.call_soon_threadsafe(holder[0].cancel)
            time.sleep(0.2)  # the cancel lands before the commit completes

    holder.append(asyncio.ensure_future(writer.write_batch(_batch("a", run_id, n=3))))
    with pytest.raises(asyncio.CancelledError):
        await holder[0]
    event.remove(engine, "commit", _cancel_during_commit)
    assert _count(engine, m.SourceContribution) == 3


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_run_timeout_around_a_batch_is_all_or_nothing(engine: Engine) -> None:
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")

    def slow(s: Session) -> int:
        sr = _add_source_run(s, run_id, "a")
        for _ in range(3):
            _add_contribution(s, sr, "a")
            time.sleep(0.1)
        return 3

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await writer.write_batch(slow)
    assert _count(engine, m.SourceContribution) == 3


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_cancelling_one_source_does_not_disturb_another(engine: Engine) -> None:
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")
    started = threading.Event()

    def slow(s: Session) -> int:
        sr = _add_source_run(s, run_id, "slow")
        started.set()
        time.sleep(0.2)
        _add_contribution(s, sr, "slow")
        return 1

    slow_task = asyncio.ensure_future(writer.write_batch(slow))
    await asyncio.to_thread(started.wait, 5)
    slow_task.cancel()
    other = await writer.write_batch(_batch("fast", run_id, n=2))
    with pytest.raises(asyncio.CancelledError):
        await slow_task
    assert other == 2
    assert _count(engine, m.SourceContribution) == 3


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_concurrent_batches_on_in_memory_sqlite_do_not_interleave() -> None:
    eng = create_store_engine("sqlite://")
    with eng.connect() as conn:
        upgrade_to_head(conn)
        conn.commit()
    writer = StoreWriter(eng)
    run_id = await writer.begin_run(status="running")
    results = await asyncio.gather(
        *(writer.write_batch(_batch(f"s{i}", run_id, n=5)) for i in range(6))
    )
    assert results == [5] * 6
    assert _count(eng, m.SourceContribution) == 30
    eng.dispose()


# Verifies: specs/lead-source-adapters/requirements.md#9.6 (property)
@pytest.mark.parametrize("fail_after", [0, 1, 2, 3, None])
async def test_batch_is_all_or_nothing_for_every_failure_point(
    engine: Engine, fail_after: int | None
) -> None:
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")
    with contextlib.suppress(BoomError):
        await writer.write_batch(_batch("a", run_id, n=4, fail_after=fail_after))
    assert _count(engine, m.SourceContribution) in (0, 4)
    assert _count(engine, m.SourceContribution) == (4 if fail_after is None else 0)


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_session_rollback_inside_a_batch_dooms_it(engine: Engine) -> None:
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")

    def sneaky(s: Session) -> None:
        sr = _add_source_run(s, run_id, "a")
        _add_contribution(s, sr, "a")
        s.rollback()
        _add_source_run(s, run_id, "b")

    with pytest.raises(Exception):  # noqa: B017, PT011 - SQLAlchemy's own error
        await writer.write_batch(sneaky)
    assert _count(engine, m.SourceRun) == 0
    assert _count(engine, m.SourceContribution) == 0


# Verifies: specs/lead-source-adapters/requirements.md#9.6
async def test_repeated_cancellation_still_waits_for_the_batch(engine: Engine) -> None:
    writer = StoreWriter(engine)
    run_id = await writer.begin_run(status="running")
    started = threading.Event()

    def slow(s: Session) -> int:
        sr = _add_source_run(s, run_id, "a")
        started.set()
        time.sleep(0.3)
        _add_contribution(s, sr, "a")
        return 1

    task = asyncio.ensure_future(writer.write_batch(slow))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _count(engine, m.SourceContribution) == 1
