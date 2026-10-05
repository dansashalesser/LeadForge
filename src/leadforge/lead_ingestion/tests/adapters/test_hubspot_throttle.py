"""HubSpot search throttling and policy-aware 429 handling (13.4 to 13.6).

No network: a scripted transport and a fake clock stand in for HubSpot and for time.
"""

import socket
from collections.abc import Callable, Mapping
from typing import ClassVar

import pytest

from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    EnrichmentRequest,
    LeadContribution,
    RateBucket,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.errors import (
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
    SourceTransient,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    SourceOutcome,
    SourceStatus,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.throttle import SourceThrottle
from leadforge.lead_ingestion.transport import TransportResponse

from .test_hubspot_source import (
    CONTACT_PATH,
    DEAL_PATH,
    ENV,
    TOKEN,
    Scripted,
    answers,
    contact,
    lead,
    live,
    request,
)


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a HubSpot test opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


class FakeTime:
    """A clock whose sleep advances it, so waits are exact and instant."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def paced(transport: Scripted, fake: FakeTime) -> HubSpotSource:
    throttle = SourceThrottle(
        "hubspot", HubSpotSource.rate_limit, clock=fake.clock, sleep=fake.sleep
    )
    return HubSpotSource(
        DataMode.LIVE,
        transport=transport,
        environ=ENV,
        pacing=SourcePacing(throttle=throttle, retry=RetryPolicy()),
    )


# Verifies: specs/lead-source-adapters/requirements.md#13.4
def test_every_hubspot_search_endpoint_uses_the_documented_search_bucket() -> None:
    for endpoint in HubSpotSource.endpoints.values():
        assert endpoint.method == "POST"
        assert endpoint.bucket == "search"
    assert set(HubSpotSource.rate_limit) == {"search"}
    assert HubSpotSource.rate_limit["search"].documented is True


# Verifies: specs/lead-source-adapters/requirements.md#13.4
# Verifies: specs/lead-source-adapters/requirements.md#13.5
async def test_search_calls_never_exceed_five_in_one_second_with_no_headers() -> None:
    fake = FakeTime()
    stamps: list[float] = []

    def respond(endpoint: Endpoint, body: Mapping[str, object]) -> TransportResponse:
        stamps.append(fake.now)
        return answers([contact()])(endpoint, body)  # headers are always empty

    source = paced(Scripted(respond), fake)
    # Three emails, each a contact search and a deal search: six search calls.
    await source.fetch_raw(request("a@x.test", "b@x.test", "c@x.test"))
    assert len(stamps) == 6
    assert {CONTACT_PATH, DEAL_PATH} >= {
        c[0].path
        for c in source.transport.calls  # type: ignore[attr-defined]
    }
    for first in stamps:
        assert sum(1 for s in stamps if first <= s < first + 1.0) <= 5
    assert fake.sleeps  # the sixth call waited: the bucket, not the server, paced it


def failing(
    status: int, body: object, headers: Mapping[str, str] | None = None
) -> TransportResponse:
    return TransportResponse(status=status, headers=headers or {}, body=body)


async def error_of(response: TransportResponse) -> SourceError:
    source = live(Scripted(lambda _e, _b: response))
    with pytest.raises(SourceError) as caught:
        await source.fetch_raw(request("ada@example.com"))
    return caught.value


# Verifies: specs/lead-source-adapters/requirements.md#13.6
@pytest.mark.parametrize("policy", ["SECONDLY", "TEN_SECONDLY_ROLLING"])
async def test_a_short_window_policy_429_is_retryable_rate_limiting(
    policy: str,
) -> None:
    err = await error_of(
        failing(429, {"policyName": policy, "message": "words"}, {"retry-after": "2"})
    )
    assert type(err) is SourceRateLimited
    assert err.retry_after_s == 2.0
    assert policy.lower() in str(err)


# Verifies: specs/lead-source-adapters/requirements.md#13.6
async def test_a_daily_policy_429_is_quota_exhaustion_not_a_retry() -> None:
    err = await error_of(failing(429, {"policyName": "DAILY"}, {"retry-after": "30"}))
    assert type(err) is SourceQuotaExhausted
    assert "daily" in str(err)


# Verifies: specs/lead-source-adapters/requirements.md#13.6
@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "policyName",
        {},
        {"policyName": None},
        {"policyName": 5},
        {"policyName": ["DAILY"]},
        {"policyName": "SOMETHING_NEW"},
        {"policyName": ""},
        {"error": {"policyName": "DAILY"}},  # only the top-level field is read
    ],
)
async def test_an_absent_or_unreadable_policy_defaults_to_rate_limited(
    body: object,
) -> None:
    err = await error_of(failing(429, body, {"retry-after": "4"}))
    assert type(err) is SourceRateLimited
    assert err.retry_after_s == 4.0
    assert "unrecognized_policy" in str(err)


# Verifies: specs/lead-source-adapters/requirements.md#13.6
async def test_policy_name_is_matched_ignoring_case_and_padding() -> None:
    err = await error_of(failing(429, {"policyName": " daily "}))
    assert type(err) is SourceQuotaExhausted


# Verifies: specs/lead-source-adapters/requirements.md#13.6
@pytest.mark.parametrize("policy", ["DAILY", "SECONDLY", "weird"])
async def test_error_text_never_carries_the_body_or_the_token(policy: str) -> None:
    body = {"policyName": policy, "message": "secret ada@example.com " + TOKEN}
    err = await error_of(failing(429, body))
    text = str(err)
    assert "secret" not in text
    assert "ada@example.com" not in text
    assert TOKEN not in text


# Verifies: specs/lead-source-adapters/requirements.md#13.6
async def test_only_a_429_reads_the_policy() -> None:
    body = {"policyName": "DAILY"}
    assert type(await error_of(failing(401, body))) is SourceUnauthorized
    assert type(await error_of(failing(503, body))) is SourceTransient
    plain = await error_of(failing(422, body))
    assert type(plain) is SourceError


# Verifies: specs/lead-source-adapters/requirements.md#13.6
@pytest.mark.parametrize(("name", "expected_calls"), [("SECONDLY", 2), ("DAILY", 1)])
async def test_a_secondly_429_is_retried_with_backoff_and_a_daily_one_is_not(
    name: str, expected_calls: int
) -> None:
    calls = 0

    def respond(endpoint: Endpoint, body: Mapping[str, object]) -> TransportResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return failing(429, {"policyName": name}, {"retry-after": "3"})
        return answers([])(endpoint, body)

    source = live(Scripted(respond))
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    req: EnrichmentRequest = request("ada@example.com")
    try:
        await RetryPolicy(max_attempts=2).run(
            lambda: source.fetch_raw(req), sleep=sleep
        )
    except SourceQuotaExhausted:
        assert name == "DAILY"
    assert calls == expected_calls
    assert slept == ([3.0] if name == "SECONDLY" else [])


class _Discovery(BaseLeadSource):
    """A Discovery source that finds two leads, so Enrichment has a work list."""

    name: ClassVar[str] = "finder"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        return RawBatch(source_name=self.name, payload={})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return [lead("a@x.test", source=self.name), lead("b@x.test", source=self.name)]


async def _run_with_hubspot(
    respond: Callable[[Endpoint, Mapping[str, object]], TransportResponse],
) -> tuple[dict[str, SourceOutcome], Scripted]:
    transport = Scripted(respond)

    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        if source_class is HubSpotSource:
            return HubSpotSource(mode, transport=transport, environ=ENV, pacing=pacing)
        return _Discovery(mode)

    orchestrator = IngestionOrchestrator(
        SourceRegistry([_Discovery, HubSpotSource]),
        resolve_mode=lambda _c, _s: ModeResolution(DataMode.LIVE, "test"),
        build_source=build,
        max_concurrent_sources=2,
        run_timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.002),
    )
    results = await orchestrator.run(SourceRequest(kind="search"))
    return {r.source_name: r.outcome for r in results}, transport


# Verifies: specs/lead-source-adapters/requirements.md#13.6
async def test_a_daily_429_halts_hubspot_in_one_attempt_and_leaves_other_sources() -> (
    None
):
    outcomes, transport = await _run_with_hubspot(
        lambda _e, _b: failing(429, {"policyName": "DAILY"}, {"retry-after": "5"})
    )
    assert outcomes["hubspot"].status is SourceStatus.QUOTA_EXHAUSTED
    assert (outcomes["hubspot"].attempted, outcomes["hubspot"].retries) == (1, 0)
    assert len(transport.calls) == 1
    assert outcomes["finder"].status is SourceStatus.OK


# Verifies: specs/lead-source-adapters/requirements.md#13.6
@pytest.mark.parametrize(
    "body", [{"policyName": "SECONDLY"}, {"policyName": "NEW"}, None]
)
async def test_a_persistent_non_daily_429_is_bounded_by_the_retry_policy(
    body: object,
) -> None:
    outcomes, _ = await _run_with_hubspot(lambda _e, _b: failing(429, body))
    assert outcomes["hubspot"].status is SourceStatus.RATE_LIMITED
    assert (outcomes["hubspot"].attempted, outcomes["hubspot"].retries) == (3, 2)
    assert outcomes["finder"].status is SourceStatus.OK
