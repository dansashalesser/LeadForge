"""Composite multi-window token bucket (task 10.1, Requirement 7.1, 7.2, 7.6).

One ``CompositeTokenBucket`` per ``(source, bucket name)``: providers throttle per
endpoint class, so each declared ``RateBucket`` gets its own. Every window of a bucket
must permit before a call is dispatched (AND), and a permit is all-or-nothing: tokens
are taken from every window in one synchronous step, so a window that denies leaves
the others untouched.

Provisional decisions (see choices.md, task 10.1):

* Windows start full, so a cold bucket allows a burst up to each window's capacity.
* FIFO fairness: waiters queue on one ``asyncio.Lock`` (FIFO), and the head waiter
  sleeps while holding it. A cancelled waiter, queued or asleep, takes nothing,
  because tokens are consumed in the same synchronous step that returns.
* A provider-supplied retry interval blocks the whole bucket until it elapses and never
  shortens an existing longer block. It is capped at ``max_retry_after_s`` (default one
  hour) so a hostile or buggy header cannot park a run forever; a capped value is
  counted. Unusable values (``None``, zero, negative, NaN) add no block but a
  throttling response is still counted.
* A clock that steps backwards neither mints nor freezes tokens: elapsed time is
  clamped to zero and the reference point is reset to the new reading.
* Counters hold names and integers only, never a credential value.

Time is injected (``clock`` and an async ``sleep``) so tests never really wait. Retry
decisions live in task 10.2, which calls ``note_rate_limited`` and ``record_retry``.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from leadforge.lead_ingestion.base_source import RateBucket

__all__ = [
    "DEFAULT_MAX_RETRY_AFTER_S",
    "CompositeTokenBucket",
    "SourceThrottle",
    "ThrottleSnapshot",
    "ThrottleWait",
]

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]

DEFAULT_MAX_RETRY_AFTER_S = 3600.0
_EPS = 1e-9  # float-drift tolerance when comparing tokens to a cost


@dataclass(frozen=True)
class ThrottleWait:
    """What one ``acquire`` cost the caller, for the run report (7.6)."""

    bucket: str
    waited_s: float


@dataclass(frozen=True)
class ThrottleSnapshot:
    """Per-source, per-run counters at one instant. Names and counts only."""

    source_name: str
    throttle_waits: int
    retries: int
    throttled_responses: int
    retry_after_capped: int


class _Counters:
    def __init__(self) -> None:
        self.throttle_waits = 0
        self.retries = 0
        self.throttled_responses = 0
        self.retry_after_capped = 0

    def snapshot(self, source_name: str) -> ThrottleSnapshot:
        return ThrottleSnapshot(
            source_name,
            self.throttle_waits,
            self.retries,
            self.throttled_responses,
            self.retry_after_capped,
        )


class _Window:
    """One refilling window: ``capacity`` tokens per ``per_seconds``."""

    def __init__(self, requests: object, per_seconds: object, now: float) -> None:
        if isinstance(requests, bool) or not isinstance(requests, int) or requests <= 0:
            raise ValueError("a rate window needs a positive integer request count")
        if (
            isinstance(per_seconds, bool)
            or not isinstance(per_seconds, int | float)
            or not math.isfinite(per_seconds)
            or per_seconds <= 0
        ):
            raise ValueError("a rate window needs a positive, finite duration")
        self.capacity = float(requests)
        self.rate = requests / float(per_seconds)  # tokens per second
        self.tokens = self.capacity
        self.last = now

    def refill(self, now: float) -> None:
        if now < self.last:  # clock stepped backwards: no minting, no freezing
            self.last = now
            return
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now

    def wait_for(self, cost: int) -> float:
        # Permit within _EPS of the cost, but sleep for the full deficit: aiming at
        # cost - _EPS would leave a residue too small for the clock to register and
        # spin forever.
        deficit = cost - self.tokens
        return 0.0 if deficit <= _EPS else deficit / self.rate


class CompositeTokenBucket:
    def __init__(
        self,
        source_name: str,
        bucket: RateBucket,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        max_retry_after_s: float = DEFAULT_MAX_RETRY_AFTER_S,
        _counters: _Counters | None = None,
    ) -> None:
        if not bucket.windows:
            raise ValueError(f"rate bucket {bucket.name!r} declares no windows")
        if (
            not isinstance(max_retry_after_s, int | float)
            or math.isnan(max_retry_after_s)
            or max_retry_after_s <= 0
        ):
            raise ValueError("max_retry_after_s must be positive")
        self.source_name = source_name
        self.name = bucket.name
        self._clock = clock
        self._sleep = sleep
        self._max_retry_after_s = float(max_retry_after_s)
        now = clock()
        self._windows = tuple(
            _Window(w.requests, w.per_seconds, now) for w in bucket.windows
        )
        self._blocked_until = float("-inf")
        self._lock = asyncio.Lock()
        self._counters = _counters if _counters is not None else _Counters()

    # -- state ----------------------------------------------------------------

    def _refill(self) -> float:
        now = self._clock()
        for w in self._windows:
            w.refill(now)
        if self._blocked_until > now + self._max_retry_after_s:
            # Only after a backwards clock step: never block longer than the cap.
            self._blocked_until = now + self._max_retry_after_s
        return now

    def _required_wait(self, now: float, cost: int) -> float:
        wait = max(0.0, self._blocked_until - now)
        for w in self._windows:
            wait = max(wait, w.wait_for(cost))
        return wait

    def _consume(self, cost: int) -> None:
        for w in self._windows:
            w.tokens -= cost

    def _check_cost(self, cost: int) -> None:
        if isinstance(cost, bool) or not isinstance(cost, int) or cost <= 0:
            raise ValueError("cost must be a positive integer")
        smallest = min(w.capacity for w in self._windows)
        if cost > smallest:
            raise ValueError(
                f"cost {cost} exceeds window capacity {int(smallest)} of bucket "
                f"{self.name!r}; it could never be permitted"
            )

    def available(self) -> tuple[float, ...]:
        """Tokens now in each window, in declaration order (refilled to the clock)."""
        self._refill()
        return tuple(w.tokens for w in self._windows)

    # -- acquiring ------------------------------------------------------------

    def try_acquire(self, cost: int = 1) -> bool:
        """Take ``cost`` tokens from every window now, or take nothing and say so.

        Never queues, and never jumps ahead of waiters already queued in ``acquire``.
        """
        self._check_cost(cost)
        if self._lock.locked():
            return False
        now = self._refill()
        if self._required_wait(now, cost) > 0:
            return False
        self._consume(cost)
        return True

    async def acquire(self, cost: int = 1) -> ThrottleWait:
        """Wait until every window permits, then take ``cost`` from all of them."""
        self._check_cost(cost)
        start = self._clock()
        slept = False
        async with self._lock:
            while True:
                now = self._refill()
                wait = self._required_wait(now, cost)
                if wait <= 0:
                    self._consume(cost)  # no await between the check and this line
                    break
                slept = True
                await self._sleep(wait)
        if slept:
            self._counters.throttle_waits += 1
        waited = max(0.0, self._clock() - start) if slept else 0.0
        return ThrottleWait(self.name, waited)

    # -- provider feedback ----------------------------------------------------

    def note_rate_limited(self, retry_after_s: float | None) -> None:
        """Record a throttling response and block until the provider's interval ends.

        The provider's interval wins over any computed wait: the bucket will not permit
        before it elapses. An existing longer block is kept.
        """
        self._counters.throttled_responses += 1
        if retry_after_s is None or math.isnan(retry_after_s) or retry_after_s <= 0:
            return
        interval = float(retry_after_s)
        if interval > self._max_retry_after_s:
            interval = self._max_retry_after_s
            self._counters.retry_after_capped += 1
        now = self._clock()
        self._blocked_until = max(self._blocked_until, now + interval)

    def record_retry(self) -> None:
        self._counters.retries += 1

    def snapshot(self) -> ThrottleSnapshot:
        return self._counters.snapshot(self.source_name)


class SourceThrottle:
    """All buckets of one source for one run, with one set of counters."""

    def __init__(
        self,
        source_name: str,
        buckets: Mapping[str, RateBucket],
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        max_retry_after_s: float = DEFAULT_MAX_RETRY_AFTER_S,
    ) -> None:
        self.source_name = source_name
        self._counters = _Counters()
        self._buckets = {
            name: CompositeTokenBucket(
                source_name,
                bucket,
                clock=clock,
                sleep=sleep,
                max_retry_after_s=max_retry_after_s,
                _counters=self._counters,
            )
            for name, bucket in buckets.items()
        }

    def bucket(self, name: str) -> CompositeTokenBucket:
        try:
            return self._buckets[name]
        except KeyError:
            raise KeyError(
                f"source {self.source_name!r} declares no rate bucket {name!r}"
            ) from None

    def bucket_names(self) -> tuple[str, ...]:
        return tuple(self._buckets)

    def snapshot(self) -> ThrottleSnapshot:
        return self._counters.snapshot(self.source_name)
