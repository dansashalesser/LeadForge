"""Bounded jittered retry over the error taxonomy (task 10.2, Requirement 7.3, 7.4).

``RetryPolicy`` wraps an async provider call. It retries only the error types it is
told are retryable (``SourceTransient`` and ``SourceRateLimited`` by default) and gives
every other failure exactly one attempt. The decision is made on the exception *type*
alone: classification happens once, in the adapter, so a provider with inverted wire
conventions needs no special case here. Nothing in this module reads a wire-level
code, and a test scans the source to keep it that way.

Provisional decisions (see choices.md, task 10.2):

* Defaults: 4 attempts, 0.5 s base, 30 s cap. Construction rejects ``max_attempts < 1``,
  a non-positive or non-finite base, and a cap below the base.
* Full jitter: the sleep before retry ``n`` (``n`` from 0) is
  ``uniform(0, min(cap, base * 2**n))``. The exponent cannot overflow: the ceiling
  falls back to the cap instead.
* A provider-supplied interval (``SourceRateLimited.retry_after_s``) *replaces* the
  computed backoff, jitter included, even when it is shorter or longer than the cap
  on the backoff. It is clamped to ``max_retry_after_s`` (same default as the
  token bucket) and each clamp is counted. ``None``, zero, negative and NaN fall back
  to the computed backoff.
* ``SourceTimedOut`` is not retryable by default: the design retries only
  ``SourceTransient`` and ``SourceRateLimited``. Widen ``retryable`` to opt in.
* ``retryable`` matches by ``isinstance``, so a subclass of a retryable class retries.
* Throttle feedback goes through the small ``ThrottleFeedback`` protocol, which
  ``CompositeTokenBucket`` and ``SourceThrottle`` buckets satisfy. Every
  ``SourceRateLimited`` is reported via ``note_rate_limited``, including the final
  one and one not in ``retryable``, because it is still a throttling response.
  ``record_retry`` is called once per retry actually taken, before the sleep.
* Cancellation and every ``BaseException`` propagate untouched, including during the
  backoff sleep. An error raised by a hook propagates, with the provider error as its
  context; nothing is swallowed.
* The last failure is re-raised as the original object. The attempt count is read
  from a caller-supplied ``RetryStats``.

Randomness, sleep and feedback are injected per run so tests never really sleep.
"""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeVar

from leadforge.lead_ingestion.errors import (
    SourceError,
    SourceRateLimited,
    SourceTransient,
)
from leadforge.lead_ingestion.throttle import DEFAULT_MAX_RETRY_AFTER_S

__all__ = [
    "DEFAULT_BASE_DELAY_S",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_DELAY_S",
    "DEFAULT_RETRYABLE",
    "RetryPolicy",
    "RetryStats",
    "ThrottleFeedback",
]

T = TypeVar("T")

Uniform = Callable[[float, float], float]
Sleep = Callable[[float], Awaitable[None]]

DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BASE_DELAY_S = 0.5
DEFAULT_MAX_DELAY_S = 30.0
DEFAULT_RETRYABLE: frozenset[type[SourceError]] = frozenset(
    {SourceTransient, SourceRateLimited}
)


class ThrottleFeedback(Protocol):
    """What the policy tells the throttle; ``CompositeTokenBucket`` satisfies it."""

    def record_retry(self) -> None: ...

    def note_rate_limited(self, retry_after_s: float | None) -> None: ...


@dataclass
class RetryStats:
    """What one ``run`` did, for the run report. Names and numbers only."""

    attempts: int = 0
    retries: int = 0
    delays_s: list[float] = field(default_factory=list)
    retry_after_used: int = 0
    retry_after_capped: int = 0


def _is_real(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay_s: float = DEFAULT_BASE_DELAY_S
    max_delay_s: float = DEFAULT_MAX_DELAY_S
    max_retry_after_s: float = DEFAULT_MAX_RETRY_AFTER_S
    jitter: Literal["full"] = "full"
    retryable: frozenset[type[SourceError]] = DEFAULT_RETRYABLE

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts < 1
        ):
            raise ValueError("max_attempts must be an integer of at least 1")
        if (
            not _is_real(self.base_delay_s)
            or not math.isfinite(self.base_delay_s)
            or self.base_delay_s <= 0
        ):
            raise ValueError("base_delay_s must be positive and finite")
        if not _is_real(self.max_delay_s) or not math.isfinite(self.max_delay_s):
            raise ValueError("max_delay_s must be finite")
        if self.max_delay_s < self.base_delay_s:
            raise ValueError("max_delay_s must be at least base_delay_s")
        if (
            not _is_real(self.max_retry_after_s)
            or math.isnan(self.max_retry_after_s)
            or self.max_retry_after_s <= 0
        ):
            raise ValueError("max_retry_after_s must be positive")
        if self.jitter != "full":
            raise ValueError("jitter must be full")
        for cls in self.retryable:
            if not (isinstance(cls, type) and issubclass(cls, SourceError)):
                raise ValueError("retryable must hold SourceError subclasses only")

    # -- backoff ---------------------------------------------------------------

    def backoff_ceiling(self, attempt: int) -> float:
        """``min(cap, base * 2**attempt)``; ``attempt`` counts retries from zero."""
        try:
            ceiling = self.base_delay_s * 2.0**attempt
        except OverflowError:
            return float(self.max_delay_s)
        return min(float(self.max_delay_s), ceiling)

    def jittered_delay(self, attempt: int, uniform: Uniform) -> float:
        """Full jitter: a draw from ``[0, ceiling]``, clamped into that range."""
        ceiling = self.backoff_ceiling(attempt)
        drawn = uniform(0.0, ceiling)
        if math.isnan(drawn):
            return 0.0
        return min(max(drawn, 0.0), ceiling)

    def _provider_delay(self, err: BaseException, stats: RetryStats) -> float | None:
        """The provider-supplied interval, clamped, or ``None`` when unusable."""
        if not isinstance(err, SourceRateLimited):
            return None
        after = err.retry_after_s
        if after is None or math.isnan(after) or after <= 0:
            return None
        stats.retry_after_used += 1
        if after > self.max_retry_after_s:
            stats.retry_after_capped += 1
            return float(self.max_retry_after_s)
        return float(after)

    # -- running ---------------------------------------------------------------

    async def run(
        self,
        call: Callable[[], Awaitable[T]],
        *,
        feedback: ThrottleFeedback | None = None,
        stats: RetryStats | None = None,
        uniform: Uniform = random.uniform,
        sleep: Sleep = asyncio.sleep,
    ) -> T:
        """Await ``call()``, retrying retryable failures up to ``max_attempts``."""
        stats = stats if stats is not None else RetryStats()
        retry_index = 0
        attempt = 0
        while True:
            attempt += 1
            stats.attempts += 1
            try:
                return await call()
            except Exception as err:
                if feedback is not None and isinstance(err, SourceRateLimited):
                    feedback.note_rate_limited(err.retry_after_s)
                if not isinstance(err, tuple(self.retryable)):
                    raise
                if attempt >= self.max_attempts:
                    raise
                delay = self._provider_delay(err, stats)
                if delay is None:
                    delay = self.jittered_delay(retry_index, uniform)
                retry_index += 1
                stats.retries += 1
                stats.delays_s.append(delay)
                if feedback is not None:
                    feedback.record_retry()
                await sleep(delay)
