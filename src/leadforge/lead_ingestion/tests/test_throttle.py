"""Task 10.1: composite multi-window token bucket (Requirement 7.1, 7.2, 7.6)."""

from __future__ import annotations

import asyncio
import dataclasses
import math

import pytest

from leadforge.lead_ingestion.base_source import RateBucket, RateWindow
from leadforge.lead_ingestion.errors import SourceRateLimited
from leadforge.lead_ingestion.throttle import (
    CompositeTokenBucket,
    SourceThrottle,
    ThrottleSnapshot,
    ThrottleWait,
)


class FakeClock:
    """Deterministic time: ``sleep`` advances ``now`` and never really waits."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds
        await asyncio.sleep(0)


def bucket_of(
    *windows: tuple[int, float], name: str = "b", documented: bool = True
) -> RateBucket:
    return RateBucket(
        name=name,
        windows=tuple(RateWindow(r, s) for r, s in windows),
        documented=documented,
        doc_url="https://docs.example.com/limits",
    )


def make(
    *windows: tuple[int, float],
    clock: FakeClock | None = None,
    max_retry_after_s: float = 3600.0,
) -> tuple[CompositeTokenBucket, FakeClock]:
    clock = clock or FakeClock()
    b = CompositeTokenBucket(
        "prov",
        bucket_of(*windows),
        clock=clock.now,
        sleep=clock.sleep,
        max_retry_after_s=max_retry_after_s,
    )
    return b, clock


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_burst_up_to_capacity_is_immediate_then_waits() -> None:
    b, clock = make((3, 1.0))
    for _ in range(3):
        w = await b.acquire()
        assert w.waited_s == 0.0
    assert clock.sleeps == []
    w = await b.acquire()
    assert clock.sleeps == [pytest.approx(1 / 3)]
    assert w.waited_s == pytest.approx(1 / 3)


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_every_window_must_permit() -> None:
    # 2 per second AND 3 per 10 seconds: the slow window binds on the 4th call.
    b, clock = make((2, 1.0), (3, 10.0))
    for _ in range(3):
        await b.acquire()
    # per-second window is empty after 3 calls only if time did not pass; the
    # 3rd call already waited for it, so the minute-ish window now binds.
    await b.acquire()
    assert clock.t - 1000.0 >= 10.0 / 3 - 1e-6


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_denial_in_one_window_consumes_nothing_from_the_others() -> None:
    clock = FakeClock()
    b, _ = make((1, 1.0), (100, 1.0), clock=clock)
    await b.acquire()  # drains the 1/s window; the wide window has 99 left
    before = b.available()
    assert before[1] == pytest.approx(99.0)
    # Probe without waiting: a denied attempt must not touch the wide window.
    assert b.try_acquire() is False
    assert b.available()[1] == pytest.approx(99.0)
    assert b.available()[0] == pytest.approx(0.0)


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_windows_of_different_sizes_refill_independently() -> None:
    b, clock = make((1, 1.0), (5, 60.0))
    await b.acquire()
    clock.t += 1.0
    assert b.try_acquire() is True  # fast window refilled, slow still has tokens


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_refill_never_exceeds_capacity() -> None:
    b, clock = make((2, 1.0))
    clock.t += 3600
    assert b.available()[0] == pytest.approx(2.0)


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_float_drift_does_not_cause_off_by_one_waits() -> None:
    # 0.1 s windows and thousands of steps: after exactly one interval a token exists.
    b, clock = make((1, 0.1))
    await b.acquire()
    # The clock is computed per step (not accumulated) so only the bucket can drift.
    for k in range(1, 2001):
        clock.t = 1000.0 + k * 0.1
        assert b.try_acquire() is True


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_clock_going_backwards_neither_mints_nor_freezes() -> None:
    b, clock = make((1, 1.0))
    await b.acquire()
    clock.t -= 500.0
    assert b.try_acquire() is False
    clock.t += 1.0  # one real second after the backwards step
    assert b.try_acquire() is True


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_cost_above_a_window_capacity_is_rejected_not_hung() -> None:
    b, _ = make((2, 1.0), (10, 1.0))
    with pytest.raises(ValueError, match="capacity"):
        await b.acquire(cost=3)
    with pytest.raises(ValueError, match="positive"):
        await b.acquire(cost=0)


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_cost_consumes_that_many_tokens() -> None:
    b, clock = make((4, 1.0))
    await b.acquire(cost=3)
    assert b.available()[0] == pytest.approx(1.0)
    await b.acquire(cost=2)
    assert clock.sleeps == [pytest.approx(0.25)]


@pytest.mark.parametrize(
    "windows",
    [
        (),
        ((0, 1.0),),
        ((-1, 1.0),),
        ((1, 0.0),),
        ((1, -2.0),),
        ((1, math.nan),),
        ((1, math.inf),),
    ],
)
# Verifies: specs/lead-source-adapters/requirements.md#7.1
def test_invalid_limits_are_rejected_at_construction(
    windows: tuple[tuple[int, float], ...],
) -> None:
    with pytest.raises(ValueError, match="window"):
        CompositeTokenBucket("prov", bucket_of(*windows))


# Verifies: specs/lead-source-adapters/requirements.md#7.1
def test_non_integer_request_count_is_rejected() -> None:
    bad = RateBucket("b", (RateWindow(True, 1.0),), True, "u")
    with pytest.raises(ValueError, match="window"):
        CompositeTokenBucket("prov", bad)
    bad = RateBucket("b", (RateWindow(1.5, 1.0),), True, "u")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="window"):
        CompositeTokenBucket("prov", bad)


# Verifies: specs/lead-source-adapters/requirements.md#7.2
async def test_provider_retry_after_blocks_even_with_tokens_available() -> None:
    b, clock = make((100, 1.0))
    b.note_rate_limited(7.5)
    w = await b.acquire()
    assert clock.sleeps == [pytest.approx(7.5)]
    assert w.waited_s == pytest.approx(7.5)


# Verifies: specs/lead-source-adapters/requirements.md#7.2
async def test_retry_after_never_shortens_an_existing_longer_block() -> None:
    b, clock = make((100, 1.0))
    b.note_rate_limited(30.0)
    b.note_rate_limited(5.0)
    await b.acquire()
    assert clock.t - 1000.0 == pytest.approx(30.0)


# Verifies: specs/lead-source-adapters/requirements.md#7.2
async def test_a_longer_retry_after_extends_the_block() -> None:
    b, clock = make((100, 1.0))
    b.note_rate_limited(5.0)
    b.note_rate_limited(20.0)
    await b.acquire()
    assert clock.t - 1000.0 == pytest.approx(20.0)


# Verifies: specs/lead-source-adapters/requirements.md#7.2
async def test_retry_after_arriving_while_a_waiter_sleeps_is_honoured() -> None:
    clock = FakeClock()
    b = CompositeTokenBucket(
        "prov", bucket_of((1, 1.0)), clock=clock.now, sleep=clock.sleep
    )
    await b.acquire()
    task = asyncio.create_task(b.acquire())
    await asyncio.sleep(0)
    b.note_rate_limited(50.0)
    await task
    assert clock.t - 1000.0 >= 50.0 - 1e-6


# Verifies: specs/lead-source-adapters/requirements.md#7.2
@pytest.mark.parametrize("value", [None, 0.0, -5.0, math.nan])
async def test_unusable_retry_after_adds_no_block(value: float | None) -> None:
    b, clock = make((100, 1.0))
    b.note_rate_limited(value)
    await b.acquire()
    assert clock.sleeps == []
    assert b.snapshot().throttled_responses == 1


# Verifies: specs/lead-source-adapters/requirements.md#7.2
async def test_huge_retry_after_is_capped_and_recorded() -> None:
    b, clock = make((100, 1.0), max_retry_after_s=60.0)
    b.note_rate_limited(10**9)
    b.note_rate_limited(math.inf)
    await b.acquire()
    assert clock.t - 1000.0 == pytest.approx(60.0)
    assert b.snapshot().retry_after_capped == 2


# Verifies: specs/lead-source-adapters/requirements.md#7.2
def test_invalid_retry_after_cap_is_rejected() -> None:
    for cap in (0.0, -1.0, math.nan):
        with pytest.raises(ValueError, match="max_retry_after_s"):
            CompositeTokenBucket("prov", bucket_of((1, 1.0)), max_retry_after_s=cap)


# Verifies: specs/lead-source-adapters/requirements.md#7.2
async def test_error_taxonomy_carrier_feeds_the_throttle() -> None:
    b, clock = make((100, 1.0))
    err = SourceRateLimited("prov", cause="throughput", retry_after_s=12.0)
    b.note_rate_limited(err.retry_after_s)
    await b.acquire()
    assert clock.t - 1000.0 == pytest.approx(12.0)


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_counters_for_waits_retries_and_throttled_responses() -> None:
    b, _ = make((1, 1.0))
    assert b.snapshot() == ThrottleSnapshot("prov", 0, 0, 0, 0)
    await b.acquire()
    await b.acquire()  # waits
    await b.acquire()  # waits
    b.note_rate_limited(1.0)
    b.record_retry()
    b.record_retry()
    snap = b.snapshot()
    assert (snap.throttle_waits, snap.retries, snap.throttled_responses) == (2, 2, 1)


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_an_immediate_acquire_is_not_a_throttle_wait() -> None:
    b, _ = make((5, 1.0))
    w = await b.acquire()
    assert w == ThrottleWait(bucket="b", waited_s=0.0)
    assert b.snapshot().throttle_waits == 0


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_snapshot_is_frozen_and_does_not_track_later_changes() -> None:
    b, _ = make((1, 1.0))
    snap = b.snapshot()
    b.record_retry()
    assert snap.retries == 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.retries = 5  # type: ignore[misc]


# Verifies: specs/lead-source-adapters/requirements.md#7.6
def test_snapshot_carries_names_and_counts_only() -> None:
    fields = {f.name for f in dataclasses.fields(ThrottleSnapshot)}
    assert fields == {
        "source_name",
        "throttle_waits",
        "retries",
        "throttled_responses",
        "retry_after_capped",
    }


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_concurrent_acquirers_are_served_first_come_first_served() -> None:
    b, _ = make((1, 1.0))
    order: list[int] = []

    async def worker(i: int) -> None:
        await b.acquire()
        order.append(i)

    tasks = []
    for i in range(20):
        tasks.append(asyncio.create_task(worker(i)))
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    assert order == list(range(20))


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_many_concurrent_waiters_never_exceed_the_rate() -> None:
    b, clock = make((2, 1.0))
    stamps: list[float] = []

    async def worker() -> None:
        await b.acquire()
        stamps.append(clock.t)

    await asyncio.gather(*(worker() for _ in range(50)))
    # 2 burst, then 2 per second: 48 more need 24 seconds.
    assert clock.t - 1000.0 == pytest.approx(24.0, abs=1e-6)
    for i in range(len(stamps)):
        in_window = [s for s in stamps if stamps[i] - 1.0 < s <= stamps[i]]
        assert (
            len(in_window) <= 2 + 1
        )  # a sliding second holds at most capacity+1 boundary


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_cancelled_sleeper_consumes_nothing_and_releases_the_queue() -> None:
    gate = asyncio.Event()

    clock = FakeClock()

    async def blocking_sleep(seconds: float) -> None:
        await gate.wait()
        clock.t += seconds

    b = CompositeTokenBucket(
        "prov", bucket_of((1, 1.0), (10, 1.0)), clock=clock.now, sleep=blocking_sleep
    )
    await b.acquire()
    tokens_before = b.available()
    victim = asyncio.create_task(b.acquire())
    follower = asyncio.create_task(b.acquire())
    await asyncio.sleep(0)
    victim.cancel()
    with pytest.raises(asyncio.CancelledError):
        await victim
    assert b.available() == tokens_before
    gate.set()
    await follower  # not deadlocked behind the cancelled waiter
    assert b.snapshot().throttle_waits >= 1


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_cancelled_while_queued_for_the_lock_leaks_nothing() -> None:
    gate = asyncio.Event()
    clock = FakeClock()

    async def blocking_sleep(seconds: float) -> None:
        await gate.wait()
        clock.t += seconds

    b = CompositeTokenBucket(
        "prov", bucket_of((1, 1.0)), clock=clock.now, sleep=blocking_sleep
    )
    await b.acquire()
    first = asyncio.create_task(b.acquire())
    queued = asyncio.create_task(b.acquire())
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    gate.set()
    await first
    assert b.available()[0] == pytest.approx(0.0)


# Verifies: specs/lead-source-adapters/requirements.md#7.1
def test_source_throttle_builds_one_bucket_per_declared_name() -> None:
    clock = FakeClock()
    t = SourceThrottle(
        "prov",
        {
            "finder": bucket_of((1, 1.0), name="finder"),
            "verify": bucket_of((5, 1.0), name="verify"),
        },
        clock=clock.now,
        sleep=clock.sleep,
    )
    assert t.bucket("finder") is t.bucket("finder")
    assert t.bucket("finder") is not t.bucket("verify")
    assert t.bucket_names() == ("finder", "verify")
    with pytest.raises(KeyError, match="nope"):
        t.bucket("nope")


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_source_throttle_counts_per_source_across_buckets() -> None:
    clock = FakeClock()
    t = SourceThrottle(
        "prov",
        {"a": bucket_of((1, 1.0), name="a"), "b": bucket_of((1, 1.0), name="b")},
        clock=clock.now,
        sleep=clock.sleep,
    )
    await t.bucket("a").acquire()
    await t.bucket("a").acquire()
    await t.bucket("b").acquire()
    t.bucket("b").record_retry()
    t.bucket("b").note_rate_limited(2.0)
    assert t.snapshot() == ThrottleSnapshot("prov", 1, 1, 1, 0)


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_buckets_of_different_classes_do_not_throttle_each_other() -> None:
    clock = FakeClock()
    t = SourceThrottle(
        "prov",
        {"a": bucket_of((1, 1.0), name="a"), "b": bucket_of((1, 1.0), name="b")},
        clock=clock.now,
        sleep=clock.sleep,
    )
    await t.bucket("a").acquire()
    t.bucket("a").note_rate_limited(30.0)
    w = await t.bucket("b").acquire()
    assert w.waited_s == 0.0


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_try_acquire_does_not_jump_ahead_of_a_queued_waiter() -> None:
    gate = asyncio.Event()
    clock = FakeClock()

    async def blocking_sleep(seconds: float) -> None:
        await gate.wait()
        clock.t += seconds

    b = CompositeTokenBucket(
        "prov", bucket_of((1, 1.0)), clock=clock.now, sleep=blocking_sleep
    )
    await b.acquire()
    waiter = asyncio.create_task(b.acquire())
    await asyncio.sleep(0)
    clock.t += 5.0  # a token is available, but the waiter was first
    assert b.try_acquire() is False
    gate.set()
    await waiter


# Verifies: specs/lead-source-adapters/requirements.md#7.2
async def test_backwards_clock_step_cannot_stretch_a_provider_block_past_the_cap() -> (
    None
):
    b, clock = make((100, 1.0), max_retry_after_s=60.0)
    b.note_rate_limited(60.0)
    clock.t -= 10_000.0
    await b.acquire()
    assert clock.sleeps == [pytest.approx(60.0)]
