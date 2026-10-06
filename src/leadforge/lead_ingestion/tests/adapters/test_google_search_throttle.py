"""Google Search: SerpApi throughput versus balance exhaustion on a 429 (14.8).

No network: a scripted transport answers. The bodies are minimal hand-made STAND-INS,
not captured responses. SerpApi documents the balance answer as HTTP 429 with
``{"error": "Your account has run out of searches."}``; the hourly-throughput wording
is NOT documented anywhere we could reach, so the throughput marker is an assumption
(the word "throughput", from the status page's "hourly throughput limit").
"""

import socket
from collections.abc import Callable, Mapping
from typing import ClassVar

import pytest

from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
from leadforge.lead_ingestion.adapters.search_backends import (
    SearchBackend,
    SearchCall,
    ThrottleCause,
)
from leadforge.lead_ingestion.adapters.search_backends.serpapi import (
    HOURLY_LIMIT_ENV,
    SerpApiBackend,
)
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
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
    SourceTransient,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import IngestionOrchestrator, SourceStatus
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.transport import TransportResponse

KEY = "test-key-not-real"
REQUEST = SourceRequest(kind="discovery")
BALANCE = {"error": "Your account has run out of searches."}
THROUGHPUT = {"error": "Your account has exceeded the hourly throughput limit."}


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a Google Search test opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture(autouse=True)
def _fast_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    # Orchestrated live runs pace on SERPAPI_HOURLY_LIMIT; the Free-plan default
    # spaces calls 72 s apart. 36,000 an hour spaces them 0.1 s apart.
    monkeypatch.setenv(HOURLY_LIMIT_ENV, "36000")


class Scripted:
    def __init__(self, response: TransportResponse) -> None:
        self.response = response
        self.calls = 0

    async def send(self, endpoint: object, **_: object) -> TransportResponse:
        self.calls += 1
        return self.response


async def error_of(
    status: int,
    body: object,
    headers: Mapping[str, str] | None = None,
    backend: SearchBackend | None = None,
) -> SourceError:
    source = GoogleSearchSource(
        DataMode.LIVE,
        transport=Scripted(TransportResponse(status, headers or {}, body)),
        queries=("q",),
        backend=backend,
        environ={"SERPAPI_API_KEY": KEY},
    )
    with pytest.raises(SourceError) as caught:
        await source.fetch_raw(REQUEST)
    return caught.value


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_a_429_for_hourly_throughput_is_retryable_rate_limiting() -> None:
    err = await error_of(429, THROUGHPUT, {"retry-after": "9"})
    assert type(err) is SourceRateLimited
    assert err.retry_after_s == 9.0
    assert "hourly_throughput" in str(err)


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_a_429_for_an_exhausted_balance_is_quota_exhaustion_not_a_retry() -> None:
    err = await error_of(429, BALANCE, {"retry-after": "9"})
    assert type(err) is SourceQuotaExhausted
    assert "search_balance_exhausted" in str(err)


# Verifies: specs/lead-source-adapters/requirements.md#14.8
@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "run out of searches",
        {},
        {"error": None},
        {"error": 5},
        {"error": ["run out of searches"]},
        {"error": "something new"},
        {"error": ""},
        {"message": "Your account has run out of searches."},
    ],
)
async def test_an_absent_or_unreadable_signal_defaults_to_rate_limited(
    body: object,
) -> None:
    err = await error_of(429, body, {"retry-after": "4"})
    assert type(err) is SourceRateLimited
    assert err.retry_after_s == 4.0
    assert "unrecognized_throttle" in str(err)


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_the_signal_is_matched_ignoring_case_and_padding() -> None:
    err = await error_of(429, {"error": "  YOUR ACCOUNT HAS RUN OUT OF SEARCHES. "})
    assert type(err) is SourceQuotaExhausted


# Verifies: specs/lead-source-adapters/requirements.md#14.8
@pytest.mark.parametrize("body", [BALANCE, THROUGHPUT, {"error": "weird"}])
async def test_error_text_never_carries_the_body_or_the_key(body: object) -> None:
    assert isinstance(body, dict)
    leaky = {"error": f"{body['error']} secret {KEY} q"}
    text = str(await error_of(429, leaky))
    assert "secret" not in text
    assert KEY not in text


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_only_a_429_reads_the_signal() -> None:
    assert type(await error_of(401, BALANCE)) is SourceUnauthorized
    assert type(await error_of(503, BALANCE)) is SourceTransient
    assert type(await error_of(422, BALANCE)) is SourceError


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_the_adapter_maps_a_backends_verdict_without_knowing_serpapi() -> None:
    class Verdict(SerpApiBackend):
        name: ClassVar[str] = "verdict"

        def throttle_cause(self, body: object) -> ThrottleCause:
            return ThrottleCause.BALANCE

        def build_call(
            self, query: str, page_index: int, credentials: Mapping[str, str]
        ) -> SearchCall:
            return SearchCall(params={})

    err = await error_of(429, {"anything": 1}, backend=Verdict())
    assert type(err) is SourceQuotaExhausted


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_a_backend_that_says_nothing_is_the_unrecognized_default() -> None:
    err = await error_of(429, BALANCE, backend=_NoOpinion())
    assert type(err) is SourceRateLimited
    assert "unrecognized_throttle" in str(err)


class _NoOpinion(SerpApiBackend):
    name: ClassVar[str] = "no_opinion"

    def throttle_cause(self, body: object) -> ThrottleCause:
        return SearchBackend.throttle_cause(self, body)


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_the_key_and_body_stay_out_of_the_message_cause_and_repr() -> None:
    err = await error_of(429, {"error": f"run out of searches {KEY} api_key={KEY}"})
    shown = f"{err!s} {err!r} {err.__cause__!r} {err.__context__!r}"
    assert KEY not in shown
    assert "api_key" not in shown


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_throughput_retry_spacing_comes_from_retry_after() -> None:
    source = GoogleSearchSource(
        DataMode.LIVE,
        transport=Scripted(TransportResponse(429, {"retry-after": "3"}, THROUGHPUT)),
        queries=("q",),
        environ={"SERPAPI_API_KEY": KEY},
    )
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    with pytest.raises(SourceRateLimited):
        await RetryPolicy(max_attempts=3).run(
            lambda: source.fetch_raw(REQUEST), sleep=sleep
        )
    assert slept == [3.0, 3.0]


class _Other(BaseLeadSource):
    name: ClassVar[str] = "other"
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
        return []


class _Calls:
    def __init__(self, respond: Callable[[int], TransportResponse]) -> None:
        self.respond = respond
        self.calls = 0

    async def send(self, endpoint: object, **_: object) -> TransportResponse:
        self.calls += 1
        return self.respond(self.calls)


async def _run(transport: _Calls) -> dict[str, tuple[SourceStatus, int, int]]:
    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        if source_class is GoogleSearchSource:
            return GoogleSearchSource(
                mode,
                transport=transport,
                queries=("a", "b"),
                environ={"SERPAPI_API_KEY": KEY},
                pacing=pacing,
            )
        return _Other(mode)

    orchestrator = IngestionOrchestrator(
        SourceRegistry([_Other, GoogleSearchSource]),
        resolve_mode=lambda _c, _s: ModeResolution(DataMode.LIVE, "test"),
        build_source=build,
        max_concurrent_sources=2,
        run_timeout_s=30,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_s=0.001, max_delay_s=0.002),
        # The composition root reads the plan setting and hands the limits over.
        live_rate_limits={"google_search": GoogleSearchSource.run_rate_limit()},
    )
    results = await orchestrator.run(SourceRequest(kind="discovery"))
    return {
        r.source_name: (r.outcome.status, r.outcome.attempted, r.outcome.retries)
        for r in results
    }


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_a_balance_429_halts_google_search_in_one_attempt() -> None:
    transport = _Calls(lambda _n: TransportResponse(429, {}, BALANCE))
    outcomes = await _run(transport)
    assert outcomes["google_search"] == (SourceStatus.QUOTA_EXHAUSTED, 1, 0)
    assert transport.calls == 1
    assert outcomes["other"][0] is SourceStatus.OK


# Verifies: specs/lead-source-adapters/requirements.md#14.8
@pytest.mark.parametrize("body", [THROUGHPUT, {"error": "new"}, None])
async def test_a_persistent_throughput_429_is_bounded_by_the_retry_policy(
    body: object,
) -> None:
    outcomes = await _run(_Calls(lambda _n: TransportResponse(429, {}, body)))
    assert outcomes["google_search"] == (SourceStatus.RATE_LIMITED, 3, 2)
    assert outcomes["other"][0] is SourceStatus.OK


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_a_retried_fetch_does_not_resend_an_answered_paid_query() -> None:
    def respond(n: int) -> TransportResponse:
        if n == 2:
            return TransportResponse(429, {}, THROUGHPUT)
        return TransportResponse(200, {}, {"organic_results": []})

    transport = _Calls(respond)
    outcomes = await _run(transport)
    assert outcomes["google_search"][1:] == (2, 1)
    assert transport.calls == 3  # a, b (throttled), b again; "a" is not re-sent
