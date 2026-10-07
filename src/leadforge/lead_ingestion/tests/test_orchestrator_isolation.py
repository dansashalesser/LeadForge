"""Orchestrator failure isolation (task 11.2, Requirements 6.1, 6.2, 6.3).

The sources are throwaway subclasses defined here; the orchestrator only sees the
``BaseLeadSource`` contract. ``Scripted`` raises or returns what its per-name script
says, one entry per provider call, and records when each call happened.
"""

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import ClassVar

import pytest

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
from leadforge.lead_ingestion.errors import (
    InvalidAbsenceError,
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
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    SourceCallLedger,
    SourceStatus,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.retry import RetryPolicy

REQUEST = SourceRequest(kind="search")
FAST = RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.002)

Step = BaseException | None  # None means the call succeeds


class Calls:
    def __init__(self, scripts: Mapping[str, list[Step]] | None = None) -> None:
        self.scripts = {k: list(v) for k, v in (scripts or {}).items()}
        self.at: dict[str, list[float]] = {}
        self.normalize_errors: dict[str, Exception] = {}
        self.modes: dict[str, DataMode] = {}

    def count(self, name: str) -> int:
        return len(self.at.get(name, []))


class Scripted(BaseLeadSource):
    name: ClassVar[str] = "scripted"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()

    def __init__(self, mode: DataMode, calls: Calls) -> None:
        super().__init__(mode)
        self.calls = calls

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        self.calls.at.setdefault(self.name, []).append(time.monotonic())
        script = self.calls.scripts.get(self.name, [])
        step = script.pop(0) if script else None
        if step is not None:
            raise step
        return RawBatch(source_name=self.name, payload={})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        error = self.calls.normalize_errors.get(self.name)
        if error is not None:
            raise error
        return []


def classes(*names: str) -> list[type[Scripted]]:
    return [type(f"S_{n}", (Scripted,), {"name": n}) for n in names]


def live(_c: type[BaseLeadSource], _s: SourceSettings) -> ModeResolution:
    return ModeResolution(DataMode.LIVE, "test: live")


def synthetic(_c: type[BaseLeadSource], _s: SourceSettings) -> ModeResolution:
    return ModeResolution(DataMode.SYNTHETIC, "test: synthetic")


def orchestrator(
    names: tuple[str, ...],
    calls: Calls,
    *,
    resolve: Callable[[type[BaseLeadSource], SourceSettings], ModeResolution] = live,
    policy: RetryPolicy | None = FAST,
) -> IngestionOrchestrator:
    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        calls.modes[source_class.name] = mode
        assert issubclass(source_class, Scripted)
        return source_class(mode, calls)

    return IngestionOrchestrator(
        SourceRegistry(classes(*names)),
        resolve_mode=resolve,
        build_source=build,
        max_concurrent_sources=4,
        run_timeout_s=30,
        retry_policy=policy,
    )


def unauthorized(name: str) -> SourceUnauthorized:
    return SourceUnauthorized(name, endpoint="/v1/x")


# Verifies: specs/lead-source-adapters/requirements.md#6.1
@pytest.mark.parametrize(
    ("error", "status", "attempts"),
    [
        (SourceError("bad", "boom"), SourceStatus.FAILED, 1),
        (NoAccessibleAccountError("bad"), SourceStatus.FAILED, 1),
        (
            InvalidAbsenceError(
                "bad", canonical_path="a.b", raw_field_path=None, reason="r"
            ),
            SourceStatus.FAILED,
            1,
        ),
        (SourceTransient("bad", status=503), SourceStatus.TRANSIENT, 3),
        (SourceTimedOut("bad"), SourceStatus.TIMED_OUT, 1),
        (SourceQuotaExhausted("bad"), SourceStatus.QUOTA_EXHAUSTED, 1),
        (
            SourceComplianceRestricted("bad", subject="x"),
            SourceStatus.COMPLIANCE_RESTRICTED,
            1,
        ),
        (unauthorized("bad"), SourceStatus.UNAUTHORIZED, 1),
        (SourceRateLimited("bad", cause="c"), SourceStatus.RATE_LIMITED, 3),
    ],
)
async def test_a_failing_fetch_is_recorded_and_the_other_sources_still_yield(
    error: Exception, status: SourceStatus, attempts: int
) -> None:
    calls = Calls({"bad": [error] * 5})
    results = await orchestrator(("a", "bad", "b"), calls).run(REQUEST)

    by_name = {r.source_name: r for r in results}
    assert [r.source_name for r in results] == ["a", "b", "bad"]
    for good in ("a", "b"):
        assert by_name[good].batch is not None
        assert by_name[good].contributions == ()
        assert by_name[good].outcome.status is SourceStatus.OK
        assert calls.count(good) == 1
    bad = by_name["bad"]
    assert bad.batch is None
    assert bad.contributions is None  # no contact data from a failed source
    assert bad.outcome.status is status
    assert bad.outcome.error is not None
    assert "bad" in bad.outcome.error
    assert calls.count("bad") == attempts  # by error type, never by status
    assert (bad.outcome.attempted, bad.outcome.failed) == (attempts, 1)


# Verifies: specs/lead-source-adapters/requirements.md#6.1
async def test_a_normalization_error_is_recorded_against_its_source() -> None:
    calls = Calls()
    calls.normalize_errors["bad"] = NormalizationError(
        "bad", raw_field_path="$.x", canonical_path="company.name"
    )
    results = await orchestrator(("a", "bad"), calls).run(REQUEST)

    by_name = {r.source_name: r for r in results}
    assert by_name["a"].outcome.status is SourceStatus.OK
    assert by_name["a"].contributions == ()
    assert by_name["bad"].outcome.status is SourceStatus.NORMALIZATION_FAILED
    assert by_name["bad"].contributions is None
    assert calls.count("bad") == 1  # a normalization failure is never retried


# Verifies: specs/lead-source-adapters/requirements.md#6.1
async def test_a_programming_error_is_not_isolated() -> None:
    calls = Calls({"bad": [RuntimeError("bug")]})
    with pytest.raises(ExceptionGroup) as raised:
        await orchestrator(("a", "bad"), calls).run(REQUEST)
    assert raised.group_contains(RuntimeError)


# Verifies: specs/lead-source-adapters/requirements.md#6.1
async def test_cancellation_is_never_recorded_as_a_failure() -> None:
    calls = Calls({"bad": [asyncio.CancelledError()]})
    with pytest.raises(asyncio.CancelledError):
        await orchestrator(("bad",), calls).run(REQUEST)


# Verifies: specs/lead-source-adapters/requirements.md#6.2
async def test_an_unauthorized_source_is_marked_and_never_retried() -> None:
    calls = Calls({"bad": [unauthorized("bad")] * 5})
    results = await orchestrator(("a", "bad"), calls).run(REQUEST)

    bad = {r.source_name: r for r in results}["bad"].outcome
    assert bad.status is SourceStatus.UNAUTHORIZED
    assert calls.count("bad") == 1
    assert (bad.attempted, bad.succeeded, bad.failed, bad.retries) == (1, 0, 1, 0)


# Verifies: specs/lead-source-adapters/requirements.md#6.2
async def test_an_unauthorized_source_skips_its_remaining_calls_in_the_run() -> None:
    ledger = SourceCallLedger("bad", retry=FAST)
    made = 0

    async def operation() -> str:
        nonlocal made
        made += 1
        raise unauthorized("bad")

    first = await ledger.call(operation)
    second = await ledger.call(operation)

    assert not first.ok
    assert not second.ok
    assert made == 1
    outcome = ledger.outcome()
    assert outcome.status is SourceStatus.UNAUTHORIZED
    assert (outcome.attempted, outcome.failed, outcome.skipped) == (1, 1, 1)


# Verifies: specs/lead-source-adapters/requirements.md#6.2
async def test_a_quota_exhausted_source_is_halted_for_the_run_without_retry() -> None:
    ledger = SourceCallLedger("bad", retry=FAST)
    made = 0

    async def operation() -> str:
        nonlocal made
        made += 1
        raise SourceQuotaExhausted("bad")

    await ledger.call(operation)
    await ledger.call(operation)
    assert made == 1
    assert ledger.outcome().skipped == 1


# Verifies: specs/lead-source-adapters/requirements.md#6.3
async def test_a_throttled_call_is_backed_off_then_retried_and_can_succeed() -> None:
    calls = Calls({"slow": [SourceRateLimited("slow", cause="c", retry_after_s=0.05)]})
    results = await orchestrator(("slow",), calls).run(REQUEST)

    outcome = results[0].outcome
    first, second = calls.at["slow"]
    assert second - first >= 0.04  # the provider interval is honoured before the retry
    assert outcome.status is SourceStatus.OK
    assert (outcome.attempted, outcome.succeeded, outcome.failed) == (2, 1, 0)
    assert outcome.retries == 1


# Verifies: specs/lead-source-adapters/requirements.md#6.3
async def test_a_source_still_throttled_after_backoff_is_marked_rate_limited() -> None:
    limited = SourceRateLimited("slow", cause="c")
    calls = Calls({"slow": [limited] * 10})
    results = await orchestrator(("a", "slow"), calls).run(REQUEST)

    by_name = {r.source_name: r.outcome for r in results}
    assert by_name["slow"].status is SourceStatus.RATE_LIMITED
    assert calls.count("slow") == FAST.max_attempts
    assert (by_name["slow"].attempted, by_name["slow"].retries) == (3, 2)
    assert by_name["a"].status is SourceStatus.OK


# Verifies: specs/lead-source-adapters/requirements.md#6.3
async def test_a_rate_limited_source_is_not_halted_for_later_calls() -> None:
    ledger = SourceCallLedger("slow", retry=FAST)
    made = 0

    async def operation() -> str:
        nonlocal made
        made += 1
        raise SourceRateLimited("slow", cause="c")

    await ledger.call(operation)
    await ledger.call(operation)
    assert made == 2 * FAST.max_attempts  # each call gets the backoff policy again


# Verifies: specs/lead-source-adapters/requirements.md#6.3
async def test_a_synthetic_source_gets_one_attempt_and_no_backoff() -> None:
    calls = Calls({"syn": [SourceTransient("syn", status=503)] * 5})
    results = await orchestrator(("syn",), calls, resolve=synthetic).run(REQUEST)

    assert calls.count("syn") == 1
    assert results[0].outcome.status is SourceStatus.TRANSIENT


# Verifies: specs/lead-source-adapters/requirements.md#6.1
async def test_a_successful_source_records_its_counts_for_the_run_summary() -> None:
    results = await orchestrator(("a",), Calls()).run(REQUEST)
    outcome = results[0].outcome
    assert outcome.source_name == "a"
    assert (outcome.attempted, outcome.succeeded, outcome.failed) == (1, 1, 0)
    assert (outcome.skipped, outcome.retries, outcome.error) == (0, 0, None)


# Verifies: specs/lead-source-adapters/requirements.md#6.3
async def test_a_policy_of_one_attempt_never_retries_a_throttled_source() -> None:
    once = RetryPolicy(max_attempts=1, base_delay_s=0.001, max_delay_s=0.002)
    calls = Calls({"slow": [SourceRateLimited("slow", cause="c")] * 3})
    results = await orchestrator(("slow",), calls, policy=once).run(REQUEST)

    outcome = results[0].outcome
    assert calls.count("slow") == 1
    assert (outcome.attempted, outcome.retries) == (1, 0)
    assert outcome.status is SourceStatus.RATE_LIMITED


# Verifies: specs/lead-source-adapters/requirements.md#6.1
async def test_an_invalid_absence_from_normalize_is_recorded_not_raised() -> None:
    calls = Calls()
    calls.normalize_errors["bad"] = InvalidAbsenceError(
        "bad", canonical_path="a.b", raw_field_path=None, reason="r"
    )
    results = await orchestrator(("a", "bad"), calls).run(REQUEST)
    by_name = {r.source_name: r for r in results}
    assert by_name["bad"].outcome.status is SourceStatus.FAILED
    assert by_name["bad"].contributions is None
    assert by_name["a"].outcome.status is SourceStatus.OK


# Verifies: specs/lead-source-adapters/requirements.md#6.3
async def test_the_status_is_the_most_recent_failure_and_a_later_success_keeps_it() -> (
    None
):
    ledger = SourceCallLedger("slow", retry=None)
    steps: list[Exception | None] = [
        SourceTransient("slow", status=503),
        SourceTimedOut("slow"),
        None,
    ]

    async def operation() -> str:
        step = steps.pop(0)
        if step is not None:
            raise step
        return "ok"

    await ledger.call(operation)
    await ledger.call(operation)
    third = await ledger.call(operation)

    outcome = ledger.outcome()
    assert third.ok
    assert outcome.status is SourceStatus.TIMED_OUT
    assert "SourceTimedOut" in (outcome.error or "")
    assert (outcome.attempted, outcome.succeeded, outcome.failed) == (3, 1, 2)


# Verifies: specs/lead-source-adapters/requirements.md#6.1
async def test_a_compliance_restriction_never_puts_its_subject_in_the_outcome() -> None:
    subject = "jane.doe@acme.com"
    calls = Calls({"bad": [SourceComplianceRestricted("bad", subject=subject)]})
    results = await orchestrator(("bad",), calls).run(REQUEST)

    outcome = results[0].outcome
    assert outcome.status is SourceStatus.COMPLIANCE_RESTRICTED
    assert outcome.error is not None
    assert subject not in outcome.error  # a person's address must not be persisted
    assert "bad" in outcome.error
