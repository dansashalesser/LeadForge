"""Task 10.2: bounded jittered retry over the error taxonomy (Requirement 7.3, 7.4)."""

from __future__ import annotations

import ast
import asyncio
import math
import random
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import leadforge.lead_ingestion.retry as retry_module
from leadforge.lead_ingestion.errors import (
    NoAccessibleAccountError,
    NormalizationError,
    SourceComplianceRestricted,
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
    SourceTimedOut,
    SourceTransient,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.retry import RetryPolicy, RetryStats


class Sleeper:
    """Records sleeps and never really waits."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        await asyncio.sleep(0)


class Feedback:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def record_retry(self) -> None:
        self.events.append("retry")

    def note_rate_limited(self, retry_after_s: float | None) -> None:
        self.events.append(("rate_limited", retry_after_s))


class Flaky:
    """Raises each queued outcome in turn; an exception instance is raised."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __call__(self) -> object:
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


def transient() -> SourceTransient:
    return SourceTransient("prov")


def limited(after: float | None = None) -> SourceRateLimited:
    return SourceRateLimited("prov", cause="burst", retry_after_s=after)


def upper(_lo: float, hi: float) -> float:
    return hi


def lower(_lo: float, _hi: float) -> float:
    return 0.0


# -- retryable classes ---------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#7.3
@pytest.mark.parametrize("make", [transient, limited])
async def test_transient_and_rate_limited_are_retried_then_succeed(
    make: Callable[[], SourceError],
) -> None:
    sleeper = Sleeper()
    call = Flaky(make(), make(), "ok")
    result = await RetryPolicy().run(call, sleep=sleeper, uniform=upper)
    assert result == "ok"
    assert call.calls == 3
    assert len(sleeper.sleeps) == 2


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_exhaustion_reraises_the_original_error_object_after_max_attempts() -> (
    None
):
    first, second, third = transient(), transient(), transient()
    call = Flaky(first, second, third)
    sleeper = Sleeper()
    with pytest.raises(SourceTransient) as info:
        await RetryPolicy(max_attempts=3).run(call, sleep=sleeper, uniform=upper)
    assert info.value is third
    assert call.calls == 3
    assert len(sleeper.sleeps) == 2  # no sleep after the final failure


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_final_error_keeps_its_type_and_chain() -> None:
    cause = ConnectionResetError("boom")
    err = transient()
    err.__cause__ = cause
    with pytest.raises(SourceTransient) as info:
        await RetryPolicy(max_attempts=1).run(Flaky(err), sleep=Sleeper())
    assert info.value.__cause__ is cause
    assert type(info.value) is SourceTransient


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_max_attempts_of_one_means_no_retries() -> None:
    sleeper = Sleeper()
    feedback = Feedback()
    call = Flaky(transient())
    with pytest.raises(SourceTransient):
        await RetryPolicy(max_attempts=1).run(call, sleep=sleeper, feedback=feedback)
    assert call.calls == 1
    assert sleeper.sleeps == []
    assert "retry" not in feedback.events


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_subclass_of_a_retryable_class_is_retried() -> None:
    class Overloaded(SourceTransient):
        pass

    call = Flaky(Overloaded("prov"), "ok")
    assert await RetryPolicy().run(call, sleep=Sleeper()) == "ok"


# -- one attempt for everything else -------------------------------------------


NON_RETRYABLE: list[Callable[[], BaseException]] = [
    lambda: SourceUnauthorized("prov", endpoint="/e"),
    lambda: SourceQuotaExhausted("prov"),
    lambda: SourceTimedOut("prov"),
    lambda: SourceComplianceRestricted("prov", subject="s"),
    lambda: NormalizationError("prov", raw_field_path="a", canonical_path="b"),
    lambda: NoAccessibleAccountError("prov"),
    lambda: SourceError("prov"),
    lambda: ValueError("not a taxonomy error"),
    lambda: KeyError("k"),
]


# Verifies: specs/lead-source-adapters/requirements.md#7.4
@pytest.mark.parametrize("make", NON_RETRYABLE)
async def test_every_other_failure_class_gets_exactly_one_attempt(
    make: Callable[[], BaseException],
) -> None:
    err = make()
    call = Flaky(err, "never reached")
    sleeper = Sleeper()
    feedback = Feedback()
    stats = RetryStats()
    with pytest.raises(type(err)) as info:
        await RetryPolicy(max_attempts=5).run(
            call, sleep=sleeper, feedback=feedback, stats=stats
        )
    assert info.value is err
    assert call.calls == 1
    assert sleeper.sleeps == []
    assert feedback.events == []
    assert stats.attempts == 1
    assert stats.retries == 0


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_timeouts_follow_the_taxonomy_and_are_not_retried_by_default() -> None:
    assert SourceTimedOut not in RetryPolicy().retryable
    assert RetryPolicy().retryable == frozenset({SourceTransient, SourceRateLimited})


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_retryable_set_can_be_widened_explicitly() -> None:
    policy = RetryPolicy(retryable=frozenset({SourceTransient, SourceTimedOut}))
    call = Flaky(SourceTimedOut("prov"), "ok")
    assert await policy.run(call, sleep=Sleeper()) == "ok"


# Verifies: specs/lead-source-adapters/requirements.md#7.4
async def test_a_failure_after_a_retry_that_is_not_retryable_stops_at_once() -> None:
    fatal = SourceUnauthorized("prov", endpoint="/e")
    call = Flaky(transient(), fatal, "never")
    with pytest.raises(SourceUnauthorized) as info:
        await RetryPolicy(max_attempts=5).run(call, sleep=Sleeper())
    assert info.value is fatal
    assert call.calls == 2


# -- no HTTP awareness ---------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#7.4
async def test_policy_dispatches_on_type_not_on_a_status_attribute() -> None:
    # An inverted-convention provider: its adapter maps what the wire calls a
    # client error to a retryable type, and what the wire calls a server error to a
    # terminal one. The policy must follow the type alone.
    retried = SourceRateLimited("prov", cause="burst")
    retried.status = 403  # type: ignore[attr-defined]
    terminal = SourceQuotaExhausted("prov")
    terminal.status = 503  # type: ignore[attr-defined]

    ok_call = Flaky(retried, "ok")
    assert await RetryPolicy().run(ok_call, sleep=Sleeper()) == "ok"
    assert ok_call.calls == 2

    bad_call = Flaky(terminal, "never")
    with pytest.raises(SourceQuotaExhausted):
        await RetryPolicy().run(bad_call, sleep=Sleeper())
    assert bad_call.calls == 1


# Verifies: specs/lead-source-adapters/requirements.md#7.4
async def test_transient_status_value_is_irrelevant_to_the_decision() -> None:
    for status in (None, 200, 400, 404, 500, 999):
        call = Flaky(SourceTransient("prov", status=status), "ok")
        assert await RetryPolicy().run(call, sleep=Sleeper()) == "ok"
        assert call.calls == 2


# Verifies: specs/lead-source-adapters/requirements.md#7.4
def test_retry_module_has_no_http_status_awareness() -> None:
    source = Path(retry_module.__file__).read_text()
    tree = ast.parse(source)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            names |= {k.arg for k in n.keywords if k.arg is not None}
    assert not {n for n in names if "status" in n.lower()}
    assert not re.search(r"status|\b[1-5]\d\d\b|httpx|http", source, re.IGNORECASE)


# Verifies: specs/lead-source-adapters/requirements.md#7.3
def test_retry_module_is_a_pure_library() -> None:
    tree = ast.parse(Path(retry_module.__file__).read_text())
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module)
        elif isinstance(n, ast.Import):
            imported |= {a.name for a in n.names}
    assert not {m for m in imported if "registry" in m or "adapters" in m}
    assert not {m for m in imported if "mode_resolution" in m}


# -- backoff ------------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_backoff_ceiling_doubles_per_attempt_up_to_the_cap() -> None:
    policy = RetryPolicy(max_attempts=7, base_delay_s=1.0, max_delay_s=10.0)
    sleeper = Sleeper()
    errs = [transient() for _ in range(6)]
    with pytest.raises(SourceTransient):
        await policy.run(Flaky(*errs, transient()), sleep=sleeper, uniform=upper)
    assert sleeper.sleeps == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_full_jitter_draws_from_zero_to_the_ceiling() -> None:
    draws: list[tuple[float, float]] = []

    def spy(lo: float, hi: float) -> float:
        draws.append((lo, hi))
        return hi / 2

    sleeper = Sleeper()
    await RetryPolicy(base_delay_s=2.0, max_delay_s=100.0).run(
        Flaky(transient(), transient(), "ok"), sleep=sleeper, uniform=spy
    )
    assert draws == [(0.0, 2.0), (0.0, 4.0)]
    assert sleeper.sleeps == [1.0, 2.0]


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_jitter_can_draw_zero() -> None:
    sleeper = Sleeper()
    await RetryPolicy().run(Flaky(transient(), "ok"), sleep=sleeper, uniform=lower)
    assert sleeper.sleeps == [0.0]


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_a_seeded_random_instance_is_deterministic() -> None:
    async def run(seed: int) -> list[float]:
        sleeper = Sleeper()
        await RetryPolicy(max_attempts=5).run(
            Flaky(transient(), transient(), transient(), "ok"),
            sleep=sleeper,
            uniform=random.Random(seed).uniform,
        )
        return sleeper.sleeps

    assert await run(7) == await run(7)
    assert await run(7) != await run(8)


# Verifies: specs/lead-source-adapters/requirements.md#7.3
@pytest.mark.parametrize("attempt", [0, 1, 10, 1023, 1024, 1025, 10**6, 10**9])
def test_ceiling_never_overflows_and_is_bounded_by_the_cap(attempt: int) -> None:
    policy = RetryPolicy(base_delay_s=0.5, max_delay_s=30.0)
    ceiling = policy.backoff_ceiling(attempt)
    assert 0.5 <= ceiling <= 30.0
    assert math.isfinite(ceiling)


# Verifies: specs/lead-source-adapters/requirements.md#7.3
def test_ceiling_at_attempt_zero_is_the_base() -> None:
    assert RetryPolicy(base_delay_s=0.25, max_delay_s=9.0).backoff_ceiling(0) == 0.25


# Verifies: specs/lead-source-adapters/requirements.md#7.3 (property)
def test_sweep_jittered_delay_lies_in_zero_to_capped_ceiling() -> None:
    # No property-testing library is a project dependency, so this is a seeded sweep.
    gen = random.Random(20261005)
    for _ in range(2000):
        base = 10 ** gen.uniform(-6, 2)
        cap = base + 10 ** gen.uniform(-6, 3) * gen.random()
        attempt = gen.choice(
            [0, 1, 2, 5, 30, 1023, 1024, 1025, 5000, gen.randrange(10**7)]
        )
        policy = RetryPolicy(base_delay_s=base, max_delay_s=cap)
        ceiling = policy.backoff_ceiling(attempt)
        assert base <= ceiling <= cap
        delay = policy.jittered_delay(attempt, gen.uniform)
        assert 0.0 <= delay <= ceiling


# Verifies: specs/lead-source-adapters/requirements.md#7.3 (property)
def test_sweep_ceiling_is_monotonic_in_attempt() -> None:
    policy = RetryPolicy(base_delay_s=0.1, max_delay_s=60.0)
    for attempt in range(2100):
        assert policy.backoff_ceiling(attempt) <= policy.backoff_ceiling(attempt + 1)


# Verifies: specs/lead-source-adapters/requirements.md#7.3
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 99.0])
def test_a_misbehaving_random_source_cannot_escape_the_bounds(bad: float) -> None:
    policy = RetryPolicy(base_delay_s=1.0, max_delay_s=4.0)
    delay = policy.jittered_delay(1, lambda _lo, _hi: bad)
    assert 0.0 <= delay <= 2.0


# -- provider-supplied interval -----------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#7.2
async def test_retry_after_replaces_the_computed_backoff_even_when_shorter() -> None:
    sleeper = Sleeper()
    policy = RetryPolicy(base_delay_s=10.0, max_delay_s=100.0)
    draws: list[Any] = []

    def spy(lo: float, hi: float) -> float:
        draws.append((lo, hi))
        return hi

    await policy.run(Flaky(limited(0.5), "ok"), sleep=sleeper, uniform=spy)
    assert sleeper.sleeps == [0.5]
    assert draws == []  # the provider interval is used as given, with no jitter


# Verifies: specs/lead-source-adapters/requirements.md#7.2
async def test_retry_after_longer_than_the_backoff_cap_is_honoured() -> None:
    sleeper = Sleeper()
    policy = RetryPolicy(base_delay_s=1.0, max_delay_s=2.0)
    await policy.run(Flaky(limited(45.0), "ok"), sleep=sleeper, uniform=upper)
    assert sleeper.sleeps == [45.0]


# Verifies: specs/lead-source-adapters/requirements.md#7.2
@pytest.mark.parametrize("unusable", [None, 0.0, -3.0, float("nan")])
async def test_unusable_retry_after_falls_back_to_computed_backoff(
    unusable: float | None,
) -> None:
    sleeper = Sleeper()
    stats = RetryStats()
    await RetryPolicy(base_delay_s=2.0, max_delay_s=8.0).run(
        Flaky(limited(unusable), "ok"), sleep=sleeper, uniform=upper, stats=stats
    )
    assert sleeper.sleeps == [2.0]
    assert stats.retry_after_used == 0


# Verifies: specs/lead-source-adapters/requirements.md#7.2
async def test_retry_after_is_capped_and_the_cap_is_counted() -> None:
    sleeper = Sleeper()
    stats = RetryStats()
    policy = RetryPolicy(max_retry_after_s=60.0)
    await policy.run(
        Flaky(limited(10_000.0), limited(float("inf")), limited(5.0), "ok"),
        sleep=sleeper,
        stats=stats,
    )
    assert sleeper.sleeps == [60.0, 60.0, 5.0]
    assert stats.retry_after_used == 3
    assert stats.retry_after_capped == 2


# Verifies: specs/lead-source-adapters/requirements.md#7.2
async def test_retry_after_on_a_transient_error_is_not_looked_for() -> None:
    err = transient()
    err.retry_after_s = 500.0  # type: ignore[attr-defined]
    sleeper = Sleeper()
    await RetryPolicy(base_delay_s=1.0, max_delay_s=1.0).run(
        Flaky(err, "ok"), sleep=sleeper, uniform=upper
    )
    assert sleeper.sleeps == [1.0]


# -- throttle feedback -----------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_each_retry_is_recorded_and_throttling_responses_are_noted() -> None:
    feedback = Feedback()
    await RetryPolicy().run(
        Flaky(transient(), limited(7.0), limited(), "ok"),
        sleep=Sleeper(),
        uniform=upper,
        feedback=feedback,
    )
    assert feedback.events == [
        "retry",
        ("rate_limited", 7.0),
        "retry",
        ("rate_limited", None),
        "retry",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_a_final_throttling_response_is_still_noted_but_not_retried() -> None:
    feedback = Feedback()
    with pytest.raises(SourceRateLimited):
        await RetryPolicy(max_attempts=2).run(
            Flaky(limited(1.0), limited(2.0)), sleep=Sleeper(), feedback=feedback
        )
    assert feedback.events == [("rate_limited", 1.0), "retry", ("rate_limited", 2.0)]


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_a_throttling_response_is_noted_even_when_not_in_the_retryable_set() -> (
    None
):
    feedback = Feedback()
    policy = RetryPolicy(retryable=frozenset({SourceTransient}))
    with pytest.raises(SourceRateLimited):
        await policy.run(Flaky(limited(3.0)), sleep=Sleeper(), feedback=feedback)
    assert feedback.events == [("rate_limited", 3.0)]


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_feedback_is_optional() -> None:
    assert await RetryPolicy().run(Flaky(transient(), "ok"), sleep=Sleeper()) == "ok"


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_real_token_bucket_counters_follow_the_retries() -> None:
    from leadforge.lead_ingestion.base_source import RateBucket, RateWindow
    from leadforge.lead_ingestion.throttle import CompositeTokenBucket

    now = [0.0]
    sleeper = Sleeper()
    bucket = CompositeTokenBucket(
        "prov",
        RateBucket("b", (RateWindow(100, 1.0),), True, "https://docs.example.com"),
        clock=lambda: now[0],
        sleep=sleeper,
    )
    await RetryPolicy().run(
        Flaky(transient(), limited(2.0), "ok"),
        sleep=sleeper,
        uniform=upper,
        feedback=bucket,
    )
    snap = bucket.snapshot()
    assert (snap.retries, snap.throttled_responses) == (2, 1)


# -- observability --------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_stats_report_attempts_retries_and_delays_on_success() -> None:
    stats = RetryStats()
    await RetryPolicy(base_delay_s=1.0, max_delay_s=8.0).run(
        Flaky(transient(), transient(), "ok"),
        sleep=Sleeper(),
        uniform=upper,
        stats=stats,
    )
    assert (stats.attempts, stats.retries) == (3, 2)
    assert stats.delays_s == [1.0, 2.0]


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_stats_report_attempts_on_exhaustion() -> None:
    stats = RetryStats()
    with pytest.raises(SourceTransient):
        await RetryPolicy(max_attempts=3).run(
            Flaky(transient(), transient(), transient()),
            sleep=Sleeper(),
            stats=stats,
        )
    assert (stats.attempts, stats.retries) == (3, 2)


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_first_attempt_success_is_one_attempt_and_no_sleep() -> None:
    stats = RetryStats()
    sleeper = Sleeper()
    assert await RetryPolicy().run(Flaky("ok"), sleep=sleeper, stats=stats) == "ok"
    assert (stats.attempts, stats.retries) == (1, 0)
    assert sleeper.sleeps == []


# Verifies: specs/lead-source-adapters/requirements.md#7.6
async def test_stats_are_not_shared_between_runs_by_default() -> None:
    policy = RetryPolicy()
    a, b = RetryStats(), RetryStats()
    await policy.run(Flaky(transient(), "ok"), sleep=Sleeper(), stats=a)
    await policy.run(Flaky("ok"), sleep=Sleeper(), stats=b)
    assert (a.attempts, b.attempts) == (2, 1)


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_a_reused_stats_object_does_not_shorten_the_attempt_budget() -> None:
    policy = RetryPolicy(max_attempts=3)
    stats = RetryStats()
    await policy.run(Flaky(transient(), "ok"), sleep=Sleeper(), stats=stats)
    call = Flaky(transient(), transient(), transient())
    with pytest.raises(SourceTransient):
        await policy.run(call, sleep=Sleeper(), stats=stats)
    assert call.calls == 3
    assert stats.attempts == 5


# -- cancellation and exceptions in hooks ------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_cancellation_from_the_call_propagates_without_retry() -> None:
    call = Flaky(asyncio.CancelledError(), "never")
    sleeper = Sleeper()
    with pytest.raises(asyncio.CancelledError):
        await RetryPolicy().run(call, sleep=sleeper)
    assert call.calls == 1
    assert sleeper.sleeps == []


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_cancellation_during_backoff_sleep_propagates_immediately() -> None:
    call = Flaky(transient(), "never")
    started = asyncio.Event()

    async def hang(_seconds: float) -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.ensure_future(RetryPolicy().run(call, sleep=hang))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert call.calls == 1


# Verifies: specs/lead-source-adapters/requirements.md#7.3
@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit, GeneratorExit])
async def test_base_exceptions_are_never_swallowed_or_retried(
    exc: type[BaseException],
) -> None:
    call = Flaky(exc(), "never")
    with pytest.raises(exc):
        await RetryPolicy().run(call, sleep=Sleeper())
    assert call.calls == 1


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_an_error_from_the_sleep_hook_propagates_and_stops_retrying() -> None:
    boom = RuntimeError("sleep failed")

    async def bad_sleep(_s: float) -> None:
        raise boom

    call = Flaky(transient(), "never")
    with pytest.raises(RuntimeError) as info:
        await RetryPolicy().run(call, sleep=bad_sleep)
    assert info.value is boom
    assert isinstance(info.value.__context__, SourceTransient)
    assert call.calls == 1


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_an_error_from_the_feedback_hook_propagates_with_the_cause_kept() -> None:
    class Broken(Feedback):
        def record_retry(self) -> None:
            raise RuntimeError("counter failed")

    call = Flaky(transient(), "never")
    with pytest.raises(RuntimeError, match="counter failed") as info:
        await RetryPolicy().run(call, sleep=Sleeper(), feedback=Broken())
    assert isinstance(info.value.__context__, SourceTransient)
    assert call.calls == 1


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_an_error_from_note_rate_limited_propagates() -> None:
    class Broken(Feedback):
        def note_rate_limited(self, retry_after_s: float | None) -> None:
            raise RuntimeError("note failed")

    with pytest.raises(RuntimeError, match="note failed"):
        await RetryPolicy().run(
            Flaky(limited(), "never"), sleep=Sleeper(), feedback=Broken()
        )


# -- construction validation ------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#7.3
@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"max_attempts": -1},
        {"max_attempts": True},
        {"max_attempts": 2.5},
        {"base_delay_s": 0.0},
        {"base_delay_s": -1.0},
        {"base_delay_s": float("nan")},
        {"base_delay_s": float("inf")},
        {"base_delay_s": True},
        {"base_delay_s": 5.0, "max_delay_s": 1.0},
        {"max_delay_s": float("nan")},
        {"max_delay_s": float("inf")},
        {"max_retry_after_s": 0.0},
        {"max_retry_after_s": float("nan")},
        {"max_retry_after_s": -1.0},
        {"jitter": "none"},
        {"retryable": frozenset({ValueError})},
        {"retryable": frozenset({"SourceTransient"})},
    ],
)
def test_invalid_configuration_is_rejected_at_construction(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="must"):
        RetryPolicy(**kwargs)


# Verifies: specs/lead-source-adapters/requirements.md#7.3
def test_boundary_configuration_is_accepted() -> None:
    RetryPolicy(max_attempts=1, base_delay_s=1e-9, max_delay_s=1e-9)
    RetryPolicy(retryable=frozenset())


# Verifies: specs/lead-source-adapters/requirements.md#7.3
def test_policy_is_immutable_and_has_documented_defaults() -> None:
    policy = RetryPolicy()
    assert (policy.max_attempts, policy.base_delay_s, policy.max_delay_s) == (
        4,
        0.5,
        30.0,
    )
    assert policy.jitter == "full"
    with pytest.raises(AttributeError):
        policy.max_attempts = 9  # type: ignore[misc]


# Verifies: specs/lead-source-adapters/requirements.md#7.3
async def test_default_sleep_and_randomness_work_unpatched() -> None:
    policy = RetryPolicy(base_delay_s=1e-6, max_delay_s=1e-6)
    assert await policy.run(Flaky(transient(), "ok")) == "ok"
