"""The base adapter awaits the declared rate bucket before each live dispatch."""

import asyncio
from collections.abc import Mapping
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
    RateWindow,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.errors import SourceError, SourceTransient
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import SourceCallLedger
from leadforge.lead_ingestion.pacing import SourcePacing, build_pacing
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.throttle import SourceThrottle
from leadforge.lead_ingestion.transport import TransportResponse

DOC = "https://example.com/limits"
SLOW = RateBucket("slow", (RateWindow(1, 100.0),), documented=True, doc_url=DOC)
FAST = RateBucket("fast", (RateWindow(1000, 100.0),), documented=True, doc_url=DOC)
FIRST = Endpoint(method="GET", path="/v1/first", bucket="slow")
SECOND = Endpoint(method="GET", path="/v1/second", bucket="fast")


class Clock:
    """Deterministic time; ``sleep`` advances it and records the wait."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds
        await asyncio.sleep(0)


class Provider(BaseLeadSource):
    name: ClassVar[str] = "provider"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {"slow": SLOW, "fast": FAST}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {"first": FIRST, "second": SECOND}
    required_env: ClassVar[tuple[str, ...]] = ()

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        await self._send(FIRST, params=None, json_body=None, headers={})
        return RawBatch(source_name=self.name, payload=None)

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return []


class Recorder:
    """A transport that notes what each bucket had left at the moment of dispatch."""

    def __init__(self, throttle: SourceThrottle | None) -> None:
        self.throttle = throttle
        self.seen: list[tuple[str, tuple[float, ...]]] = []
        self.failures: list[Exception] = []

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        if self.throttle is not None:
            self.seen.append(
                (endpoint.bucket, self.throttle.bucket(endpoint.bucket).available())
            )
        if self.failures:
            raise self.failures.pop(0)
        return TransportResponse(status=200, headers={}, body=None)


def live(
    clock: Clock, *, retry: RetryPolicy | None = None
) -> tuple[Provider, Recorder, SourcePacing]:
    throttle = SourceThrottle(
        "provider", Provider.rate_limit, clock=clock.now, sleep=clock.sleep
    )
    pacing = SourcePacing(throttle=throttle, retry=retry or RetryPolicy())
    transport = Recorder(throttle)
    return (
        Provider(DataMode.LIVE, transport=transport, pacing=pacing),
        transport,
        pacing,
    )


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_a_live_call_takes_from_its_declared_bucket_before_dispatch() -> None:
    source, transport, pacing = live(Clock())
    await source._send(FIRST, params=None, json_body=None, headers={})
    # By the time the transport saw the call, the slow bucket's only token was gone.
    assert transport.seen == [("slow", (0.0,))]
    assert pacing.throttle.snapshot().throttle_waits == 0


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_each_endpoint_draws_on_its_own_bucket_only() -> None:
    clock = Clock()
    source, transport, _ = live(clock)
    await source._send(SECOND, params=None, json_body=None, headers={})
    await source._send(FIRST, params=None, json_body=None, headers={})
    assert [bucket for bucket, _ in transport.seen] == ["fast", "slow"]
    assert clock.sleeps == []  # the slow bucket was untouched by the fast call


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_a_call_over_the_declared_limit_waits_instead_of_dispatching() -> None:
    clock = Clock()
    source, transport, pacing = live(clock)
    await source._send(FIRST, params=None, json_body=None, headers={})
    await source._send(FIRST, params=None, json_body=None, headers={})
    assert clock.sleeps == [pytest.approx(100.0)]
    assert len(transport.seen) == 2
    assert pacing.throttle.snapshot().throttle_waits == 1


# Verifies: specs/lead-source-adapters/requirements.md#7.5
async def test_a_synthetic_source_holds_no_throttle_and_still_dispatches() -> None:
    assert build_pacing("provider", Provider.rate_limit, DataMode.SYNTHETIC) is None
    transport = Recorder(None)
    source = Provider(DataMode.SYNTHETIC, transport=transport, pacing=None)
    for _ in range(3):  # three calls on a one-per-100s bucket: nothing to wait on
        await source._send(FIRST, params=None, json_body=None, headers={})
    assert transport.seen == []
    assert not transport.failures


# Verifies: specs/lead-source-adapters/requirements.md#7.1
async def test_cancellation_while_waiting_for_capacity_dispatches_nothing() -> None:
    gate = asyncio.Event()

    async def never(_: float) -> None:
        await gate.wait()

    throttle = SourceThrottle("provider", Provider.rate_limit, sleep=never)
    transport = Recorder(throttle)
    pacing = SourcePacing(throttle=throttle, retry=RetryPolicy())
    source = Provider(DataMode.LIVE, transport=transport, pacing=pacing)
    await source._send(FIRST, params=None, json_body=None, headers={})
    task = asyncio.ensure_future(
        source._send(FIRST, params=None, json_body=None, headers={})
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(transport.seen) == 1  # only the first call ever reached the transport


# Verifies: specs/lead-source-adapters/requirements.md#7.4
async def test_a_retried_fetch_takes_capacity_again_for_every_attempt() -> None:
    clock = Clock()
    policy = RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.001)
    source, transport, _ = live(clock, retry=policy)
    transport.failures = [SourceTransient("provider")]
    ledger = SourceCallLedger("provider", retry=policy)
    attempt = await ledger.call(lambda: source.fetch_raw(SourceRequest(kind="x")))
    assert attempt.ok
    assert len(transport.seen) == 2  # one failed dispatch, one retry
    assert clock.sleeps == [pytest.approx(100.0)]  # the retry waited for a token


# Verifies: specs/lead-source-adapters/requirements.md#7.4
async def test_a_non_retryable_failure_gets_one_dispatch_and_one_token() -> None:
    clock = Clock()
    policy = RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.001)
    source, transport, _ = live(clock, retry=policy)
    transport.failures = [SourceError("provider", "boom")]
    ledger = SourceCallLedger("provider", retry=policy)
    attempt = await ledger.call(lambda: source.fetch_raw(SourceRequest(kind="x")))
    assert not attempt.ok
    assert len(transport.seen) == 1
    assert clock.sleeps == []
