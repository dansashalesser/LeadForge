"""The conventional ``classify_error`` default on ``BaseLeadSource`` (design: Error
Categories). Every provider inherits it; a provider with an inverted convention (16.7)
overrides it alone, and ``_send`` stays the one call path."""

from collections.abc import Mapping
from functools import partial
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
from leadforge.lead_ingestion.errors import (
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
    SourceTimedOut,
    SourceTransient,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import SourceCallLedger, SourceStatus
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.transport import TransportResponse

DOC = "https://example.com/limits"
BUCKET = RateBucket("b", (RateWindow(1000, 1.0),), documented=True, doc_url=DOC)
EP = Endpoint(method="GET", path="/v1/things", bucket="b")
SECRET_BODY = {"message": "Jane Doe jane@x.test", "key": "sk-live-123"}


class Provider(BaseLeadSource):
    name: ClassVar[str] = "provider"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {"b": BUCKET}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {"things": EP}
    required_env: ClassVar[tuple[str, ...]] = ()

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        await self._send(EP, params=None, json_body=None, headers={})
        return RawBatch(source_name=self.name, payload=None)

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return []


class Inverted(Provider):
    """Inverted convention: 403 is a throttle and 429 is a spent quota (16.7)."""

    def classify_error(
        self, response: TransportResponse, *, endpoint: Endpoint
    ) -> SourceError | None:
        if response.status == 403:
            return SourceRateLimited(self.name, cause="http_403")
        if response.status == 429:
            return SourceQuotaExhausted(self.name, "quota")
        return super().classify_error(response, endpoint=endpoint)


class Answers:
    def __init__(self, *responses: TransportResponse) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.calls += 1
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def reply(status: int, **headers: str) -> TransportResponse:
    return TransportResponse(status=status, headers=headers, body=SECRET_BODY)


def source(
    cls: type[Provider], *responses: TransportResponse
) -> tuple[Provider, Answers]:
    transport = Answers(*responses)
    return cls(DataMode.LIVE, transport=transport), transport


async def failure(
    status: int,
    cls: type[Provider] = Provider,
    headers: Mapping[str, str] | None = None,
) -> SourceError:
    src, _ = source(cls, reply(status, **(headers or {})))
    with pytest.raises(SourceError) as caught:
        await src.fetch_raw(SourceRequest(kind="x"))
    return caught.value


@pytest.mark.parametrize("status", [200, 201, 204, 299])
async def test_a_2xx_is_returned_not_raised(status: int) -> None:
    src, transport = source(Provider, reply(status))
    assert (
        await src._send(EP, params=None, json_body=None, headers={})
    ).status == status
    assert transport.calls == 1


# Verifies: specs/lead-source-adapters/requirements.md#6.2
@pytest.mark.parametrize("status", [401, 403])
async def test_401_and_403_are_unauthorized_naming_the_endpoint(status: int) -> None:
    err = await failure(status)
    assert isinstance(err, SourceUnauthorized)
    assert err.endpoint == EP.path
    assert EP.path in str(err)


# Verifies: specs/lead-source-adapters/requirements.md#6.3
async def test_429_is_rate_limited_with_the_retry_after_seconds() -> None:
    err = await failure(429, headers={"retry-after": "30"})
    assert isinstance(err, SourceRateLimited)
    assert err.retry_after_s == 30.0


@pytest.mark.parametrize(
    "value",
    [
        "",
        "soon",
        "-3",
        "0",
        "nan",
        "inf",
        "1e999",
        "1_0",
        "Wed, 21 Oct 2026 07:28:00 GMT",
        "9" * 5000,
        "٣",
    ],
)
async def test_an_unusable_retry_after_is_none_never_invented(value: str) -> None:
    err = await failure(429, headers={"retry-after": value})
    assert isinstance(err, SourceRateLimited)
    assert err.retry_after_s is None


async def test_429_without_retry_after_has_no_interval() -> None:
    err = await failure(429)
    assert isinstance(err, SourceRateLimited)
    assert err.retry_after_s is None


@pytest.mark.parametrize("status", [408, 500, 502, 503, 599])
async def test_5xx_and_408_are_transient_with_the_status(status: int) -> None:
    err = await failure(status)
    assert isinstance(err, SourceTransient)
    assert err.status == status


@pytest.mark.parametrize("status", [400, 402, 404, 409, 410, 422, 451, 301, 302, 100])
async def test_any_other_non_success_is_a_permanent_plain_source_error(
    status: int,
) -> None:
    err = await failure(status)
    assert type(err) is SourceError
    assert str(status) in str(err)
    assert EP.path in str(err)


# Verifies: specs/lead-source-adapters/requirements.md#7.4
@pytest.mark.parametrize("status", [400, 404, 422])
async def test_a_permanent_4xx_is_exactly_one_attempt_through_the_retry_policy(
    status: int,
) -> None:
    src, transport = source(Provider, reply(status))
    ledger = SourceCallLedger("provider", retry=RetryPolicy(max_attempts=4))
    attempt = await ledger.call(lambda: src.fetch_raw(SourceRequest(kind="x")))
    assert not attempt.ok
    assert transport.calls == 1
    outcome = ledger.outcome()
    assert outcome.status is SourceStatus.FAILED
    assert outcome.attempted == 1


async def test_a_401_is_never_retried_and_halts_the_source() -> None:
    src, transport = source(Provider, reply(401))
    ledger = SourceCallLedger("provider", retry=RetryPolicy(max_attempts=4))
    await ledger.call(lambda: src.fetch_raw(SourceRequest(kind="x")))
    await ledger.call(lambda: src.fetch_raw(SourceRequest(kind="x")))
    assert transport.calls == 1
    outcome = ledger.outcome()
    assert outcome.status is SourceStatus.UNAUTHORIZED
    assert (outcome.attempted, outcome.skipped) == (1, 1)


async def test_a_429_is_retried_by_the_provider_interval_and_a_5xx_is_retried() -> None:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    ok = reply(200)
    for first, expected in (
        (reply(429, **{"retry-after": "3"}), [3.0]),
        (reply(503), None),
    ):
        src, transport = source(Provider, first, ok)
        slept.clear()
        await RetryPolicy(max_attempts=2).run(
            partial(src.fetch_raw, SourceRequest(kind="x")), sleep=sleep
        )
        assert transport.calls == 2
        if expected is not None:
            assert slept == expected


async def test_a_5xx_that_never_recovers_is_transient_at_the_attempt_ceiling() -> None:
    src, transport = source(Provider, reply(503))
    ledger = SourceCallLedger(
        "provider",
        retry=RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.001),
    )
    await ledger.call(lambda: src.fetch_raw(SourceRequest(kind="x")))
    assert transport.calls == 3
    assert ledger.outcome().status is SourceStatus.TRANSIENT


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 422])
async def test_error_text_never_carries_a_body_or_a_credential(status: int) -> None:
    err = await failure(status, headers={"x-api-key": "sk-live-123"})
    text = str(err) + repr(err) + repr(err.args)
    for leaked in ("Jane", "jane@x.test", "sk-live-123"):
        assert leaked not in text


# Verifies: specs/lead-source-adapters/requirements.md#16.7
async def test_an_override_inverts_the_convention_without_touching_the_call_path() -> (
    None
):
    assert isinstance(await failure(403, Inverted), SourceRateLimited)
    assert isinstance(await failure(429, Inverted), SourceQuotaExhausted)
    assert isinstance(await failure(401, Inverted), SourceUnauthorized)
    assert isinstance(await failure(500, Inverted), SourceTransient)


def test_the_hook_returns_none_for_a_2xx_and_never_a_timeout_type() -> None:
    src, _ = source(Provider, reply(200))
    assert src.classify_error(reply(200), endpoint=EP) is None
    # timeouts and connection errors are the transport's, raised before any response
    assert not issubclass(SourceTimedOut, SourceTransient)
