"""Google Search adapter: the pluggable search backend (14.1, 14.2, 14.3, 14.7).

``fixtures/google_search/search.json`` is a hand-made STAND-IN, not a captured
response. Result normalization is in test_google_search_normalize.py (14.2);
exhaustion handling is task 14.3.
"""

import socket
import sys
import traceback
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from leadforge.lead_ingestion.adapters import google_search as google_search_module
from leadforge.lead_ingestion.adapters import search_backends
from leadforge.lead_ingestion.adapters.google_search import (
    MAX_QUERIES,
    MAX_RESULTS_PER_QUERY,
    GoogleSearchSource,
)
from leadforge.lead_ingestion.adapters.search_backends import (
    DEFAULT_BACKEND_NAME,
    SearchBackend,
    SearchCall,
    select_backend,
)
from leadforge.lead_ingestion.adapters.search_backends.serpapi import SerpApiBackend
from leadforge.lead_ingestion.base_source import (
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    EnrichmentRequest,
    RateBucket,
    RateWindow,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.errors import (
    ConfigurationError,
    DuplicateSourceNameError,
    MissingCredentialError,
    NormalizationError,
    SourceError,
    SourceRateLimited,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.throttle import SourceThrottle
from leadforge.lead_ingestion.transport import RestTransport, TransportResponse

SEARCH_PATH = "/search"
KEY = "test-key-not-real"
ENV = {"SERPAPI_API_KEY": KEY}
REQUEST = SourceRequest(kind="discovery")
QUERY = "example query"
SLICE = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a Google Search test opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket.socket, "sendto", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


Responder = Callable[[Mapping[str, object]], TransportResponse]


def ok(*, more: bool = False, results: int = 10) -> TransportResponse:
    body: dict[str, object] = {
        "organic_results": [
            {"position": i + 1, "title": "t", "link": "https://x.test", "snippet": "s"}
            for i in range(results)
        ]
    }
    if more:
        body["serpapi_pagination"] = {"next": "https://next.test/page"}
    return TransportResponse(status=200, headers={}, body=body)


class Scripted:
    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[tuple[Endpoint, Mapping[str, object], Mapping[str, str]]] = []

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.calls.append((endpoint, dict(params or {}), dict(headers)))
        return self.responder(params or {})


def live(
    transport: Scripted,
    *,
    queries: tuple[str, ...] = (QUERY,),
    results_per_query: int = 10,
    backend: SearchBackend | None = None,
    pacing: SourcePacing | None = None,
) -> GoogleSearchSource:
    return GoogleSearchSource(
        DataMode.LIVE,
        transport=transport,
        queries=queries,
        results_per_query=results_per_query,
        backend=backend,
        environ=ENV,
        pacing=pacing,
    )


# Verifies: specs/lead-source-adapters/requirements.md#14.1
def test_declares_a_read_only_search_adapter_over_serpapi() -> None:
    assert GoogleSearchSource.name == "google_search"
    # ENRICH since the 14.2 completion: anchored queries per discovered company.
    assert GoogleSearchSource.capabilities == frozenset(
        {Capability.SEARCH, Capability.ENRICH}
    )
    assert GoogleSearchSource.required_env == ("SERPAPI_API_KEY",)
    assert GoogleSearchSource.base_url == "https://serpapi.com"
    endpoint = GoogleSearchSource.endpoints["search"]
    assert (endpoint.method, endpoint.path, endpoint.read_only) == (
        "GET",
        SEARCH_PATH,
        True,
    )
    assert endpoint.bucket in GoogleSearchSource.rate_limit


# Verifies: specs/lead-source-adapters/requirements.md#14.1
def test_declares_a_balance_bearing_per_call_cost() -> None:
    assert GoogleSearchSource.cost_class is CostClass.PAID
    assert GoogleSearchSource.charge_unit is ChargeUnit.PER_CALL
    assert GoogleSearchSource.yields_suppression is False


# Verifies: specs/lead-source-adapters/requirements.md#14.1
def test_the_rate_bucket_is_the_documented_free_plan_throughput() -> None:
    bucket = GoogleSearchSource.rate_limit[
        GoogleSearchSource.endpoints["search"].bucket
    ]
    assert isinstance(bucket, RateBucket)
    assert bucket.documented is True
    assert bucket.windows == (
        RateWindow(requests=50, per_seconds=3600.0),
        RateWindow(requests=1, per_seconds=72.0),
    )


# Verifies: specs/lead-source-adapters/requirements.md#14.1
def test_serpapi_is_the_default_backend() -> None:
    assert DEFAULT_BACKEND_NAME == "serpapi"
    assert select_backend(DEFAULT_BACKEND_NAME) is SerpApiBackend
    source = GoogleSearchSource(DataMode.SYNTHETIC, transport=Scripted(lambda _: ok()))
    assert isinstance(source.backend, SerpApiBackend)


# Verifies: specs/lead-source-adapters/requirements.md#14.1
def test_an_unknown_backend_name_is_a_loud_configuration_error() -> None:
    with pytest.raises(ConfigurationError) as caught:
        select_backend("no_such_backend")
    assert "no_such_backend" not in str(caught.value)
    assert "serpapi" in str(caught.value)


class OtherBackend(SearchBackend):
    """A second backend in its own style; the adapter must work with it unchanged."""

    name: ClassVar[str] = "other"
    endpoint: ClassVar[Endpoint] = SerpApiBackend.endpoint
    rate_bucket: ClassVar[RateBucket] = SerpApiBackend.rate_bucket
    required_env: ClassVar[tuple[str, ...]] = SerpApiBackend.required_env
    base_url: ClassVar[str] = SerpApiBackend.base_url
    docs_url: ClassVar[str] = SerpApiBackend.docs_url
    page_size: ClassVar[int] = 5

    def build_call(
        self, query: str, page_index: int, credentials: Mapping[str, str]
    ) -> SearchCall:
        return SearchCall(
            params={"phrase": query, "offset": page_index * 5},
            headers={"x-token": credentials["SERPAPI_API_KEY"]},
        )

    def has_next_page(self, body: object) -> bool:
        return isinstance(body, Mapping) and body.get("more") is True


# Verifies: specs/lead-source-adapters/requirements.md#14.1
async def test_swapping_the_backend_changes_no_adapter_logic() -> None:
    transport = Scripted(
        lambda params: TransportResponse(
            status=200, headers={}, body={"more": params["offset"] == 0, "items": []}
        )
    )
    source = live(transport, results_per_query=10, backend=OtherBackend())
    batch = await source.fetch_raw(REQUEST)
    assert [c[1] for c in transport.calls] == [
        {"phrase": QUERY, "offset": 0},
        {"phrase": QUERY, "offset": 5},
    ]
    assert transport.calls[0][2] == {"x-token": KEY}
    assert len(batch.payload["searches"][0]["pages"]) == 2


# Verifies: specs/lead-source-adapters/requirements.md#14.1
def test_a_backend_whose_declarations_differ_from_the_adapter_is_refused() -> None:
    class Elsewhere(OtherBackend):
        name: ClassVar[str] = "elsewhere"
        required_env: ClassVar[tuple[str, ...]] = ("OTHER_KEY",)

    with pytest.raises(ConfigurationError):
        live(Scripted(lambda _: ok()), backend=Elsewhere())


# Verifies: specs/lead-source-adapters/requirements.md#14.2
def test_no_custom_search_api_path_exists() -> None:
    hosts = {GoogleSearchSource.base_url, SerpApiBackend.base_url}
    assert all("googleapis" not in h and "customsearch" not in h for h in hosts)
    assert all(
        "customsearch" not in e.path for e in GoogleSearchSource.endpoints.values()
    )
    with pytest.raises(ConfigurationError):
        select_backend("cse")
    for module in (google_search_module, search_backends):
        text = Path(str(module.__file__)).read_text(encoding="utf-8").lower()
        assert "customsearch" not in text
    backend_dir = Path(str(search_backends.__file__)).parent
    for path in backend_dir.glob("*.py"):
        assert "customsearch" not in path.read_text(encoding="utf-8").lower()


# Verifies: specs/lead-source-adapters/requirements.md#14.3
async def test_the_key_is_an_api_key_request_parameter_never_a_header() -> None:
    transport = Scripted(lambda _: ok())
    await live(transport).fetch_raw(REQUEST)
    _, params, headers = transport.calls[0]
    assert params["api_key"] == KEY
    assert params["engine"] == "google"
    assert params["q"] == QUERY
    assert KEY not in headers.values()


# Verifies: specs/lead-source-adapters/requirements.md#14.3
def test_the_key_never_reaches_a_repr() -> None:
    call = SerpApiBackend().build_call(QUERY, 0, ENV)
    assert KEY not in repr(call)
    assert KEY not in repr(SerpApiBackend())
    assert KEY not in repr(live(Scripted(lambda _: ok())))


# Verifies: specs/lead-source-adapters/requirements.md#14.3
async def test_a_missing_key_fails_by_name_before_any_call() -> None:
    transport = Scripted(lambda _: ok())
    source = GoogleSearchSource(
        DataMode.LIVE, transport=transport, queries=(QUERY,), environ={}
    )
    with pytest.raises(MissingCredentialError) as caught:
        await source.fetch_raw(REQUEST)
    assert "SERPAPI_API_KEY" in str(caught.value)
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#14.3
async def test_synthetic_mode_sends_no_key_and_opens_no_socket() -> None:
    transport = GoogleSearchSource.build_transport(DataMode.SYNTHETIC)
    source = GoogleSearchSource(
        DataMode.SYNTHETIC, transport=transport, queries=(QUERY,), environ={}
    )
    batch = await source.fetch_raw(REQUEST)
    pages = batch.payload["searches"][0]["pages"]
    assert [r["position"] for r in pages[0]["organic_results"]] == [1, 2]
    fixture = SLICE / "fixtures" / "google_search" / "search.json"
    assert fixture.is_file()


# Verifies: specs/lead-source-adapters/requirements.md#14.3
def test_live_transport_is_https_and_reaches_only_the_declared_endpoint() -> None:
    assert isinstance(GoogleSearchSource.build_transport(DataMode.LIVE), RestTransport)
    assert GoogleSearchSource.base_url.startswith("https://")


# Verifies: specs/lead-source-adapters/requirements.md#14.7
async def test_it_pages_by_start_in_tens_and_never_sends_num() -> None:
    transport = Scripted(lambda _: ok(more=True))
    await live(transport, results_per_query=25).fetch_raw(REQUEST)
    assert [c[1]["start"] for c in transport.calls] == [0, 10, 20]
    assert all("num" not in c[1] for c in transport.calls)


# Verifies: specs/lead-source-adapters/requirements.md#14.7
async def test_one_hundred_results_is_ten_requests_not_one_inline() -> None:
    transport = Scripted(lambda _: ok(more=True))
    await live(transport, results_per_query=100).fetch_raw(REQUEST)
    assert len(transport.calls) == 10
    assert all("num" not in c[1] for c in transport.calls)


# Verifies: specs/lead-source-adapters/requirements.md#14.7
async def test_it_stops_paging_when_there_is_no_next_page() -> None:
    transport = Scripted(lambda _: ok(more=False))
    await live(transport, results_per_query=50).fetch_raw(REQUEST)
    assert len(transport.calls) == 1


# Verifies: specs/lead-source-adapters/requirements.md#14.7
async def test_the_default_is_one_request_per_query() -> None:
    transport = Scripted(lambda _: ok(more=True))
    await live(transport, queries=("a", "b")).fetch_raw(REQUEST)
    assert [(c[1]["q"], c[1]["start"]) for c in transport.calls] == [("a", 0), ("b", 0)]


# Verifies: specs/lead-source-adapters/requirements.md#14.1
async def test_a_retried_fetch_repeats_no_balance_bearing_call() -> None:
    transport = Scripted(lambda _: ok(more=True))
    source = live(transport, results_per_query=20)
    first = await source.fetch_raw(REQUEST)
    second = await source.fetch_raw(REQUEST)
    assert len(transport.calls) == 2
    assert first == second


# Verifies: specs/lead-source-adapters/requirements.md#14.1
async def test_a_retry_after_a_failed_page_resumes_after_the_paid_pages() -> None:
    state = {"fail": True}

    def respond(params: Mapping[str, object]) -> TransportResponse:
        if params["start"] == 10 and state["fail"]:
            state["fail"] = False
            return TransportResponse(status=503, headers={}, body=None)
        return ok(more=True)

    transport = Scripted(respond)
    source = live(transport, results_per_query=20)
    with pytest.raises(SourceError):
        await source.fetch_raw(REQUEST)
    await source.fetch_raw(REQUEST)
    assert [c[1]["start"] for c in transport.calls] == [0, 10, 10]


# Verifies: specs/lead-source-adapters/requirements.md#14.1
async def test_every_call_takes_capacity_from_the_declared_bucket() -> None:
    throttle = SourceThrottle("google_search", GoogleSearchSource.rate_limit)
    pacing = SourcePacing(throttle=throttle, retry=RetryPolicy())
    bucket = throttle.bucket("default")
    seen: list[tuple[float, ...]] = []

    def respond(_: Mapping[str, object]) -> TransportResponse:
        seen.append(bucket.available())
        return ok()

    await live(Scripted(respond), pacing=pacing).fetch_raw(REQUEST)
    assert seen
    hourly, spacing = seen[0]
    assert hourly == pytest.approx(49, abs=0.1)
    assert spacing < 1.0


# Verifies: specs/lead-source-adapters/requirements.md#14.1
async def test_errors_name_the_path_and_never_the_key_query_or_body() -> None:
    body = {"error": f"bad key {KEY} for {QUERY}"}
    transport = Scripted(lambda _: TransportResponse(401, {}, body))
    with pytest.raises(SourceUnauthorized) as caught:
        await live(transport).fetch_raw(REQUEST)
    text = str(caught.value)
    assert SEARCH_PATH in text
    assert KEY not in text
    assert QUERY not in text


# Verifies: specs/lead-source-adapters/requirements.md#14.1
async def test_a_429_is_the_conventional_rate_limited_error() -> None:
    transport = Scripted(lambda _: TransportResponse(429, {"retry-after": "7"}, None))
    with pytest.raises(SourceRateLimited) as caught:
        await live(transport).fetch_raw(REQUEST)
    assert KEY not in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#14.1
async def test_a_non_object_answer_is_a_normalization_error() -> None:
    transport = Scripted(lambda _: TransportResponse(200, {}, ["not", "an", "object"]))
    with pytest.raises(NormalizationError):
        await live(transport).fetch_raw(REQUEST)


# Verifies: specs/lead-source-adapters/requirements.md#14.5
async def test_enrichment_with_no_discovered_company_makes_no_call() -> None:
    """Superseded 'discovery only' (14.2 completion, option C): Enrichment asks
    anchored queries only, never the constructor's unanchored ones."""
    transport = Scripted(lambda _: ok())
    raw = await live(transport).fetch_raw(
        EnrichmentRequest(kind="enrich", work_list=())
    )
    assert transport.calls == []
    assert raw.payload == {"searches": [], "unasked_queries": 0}


# Verifies: specs/lead-source-adapters/requirements.md#14.1
def test_blank_queries_and_a_non_positive_result_count_are_refused() -> None:
    with pytest.raises(ValueError, match="query"):
        live(Scripted(lambda _: ok()), queries=("  ",))
    with pytest.raises(ValueError, match="results_per_query"):
        live(Scripted(lambda _: ok()), results_per_query=0)


# Verifies: specs/lead-source-adapters/requirements.md#14.4
def test_an_empty_batch_normalizes_to_nothing() -> None:
    source = live(Scripted(lambda _: ok()))
    batch = RawBatch(source_name="google_search", payload={"searches": []})
    assert source.normalize(batch) == []


def _backend_module(name: str, backend_name: str) -> str:
    return (
        "from leadforge.lead_ingestion.adapters.search_backends.serpapi import "
        "SerpApiBackend\n"
        f"class {name}(SerpApiBackend):\n    name = {backend_name!r}\n"
    )


@pytest.fixture
def drop_in_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(
        search_backends, "__path__", [*search_backends.__path__, str(tmp_path)]
    )
    before = set(sys.modules)
    yield tmp_path
    for loaded in set(sys.modules) - before:
        del sys.modules[loaded]


# Verifies: specs/lead-source-adapters/requirements.md#14.1
def test_a_new_backend_module_is_selectable_by_name_with_no_other_edit(
    drop_in_dir: Path,
) -> None:
    (drop_in_dir / "drop_in.py").write_text(_backend_module("DropIn", "drop_in"))
    assert select_backend("drop_in").__name__ == "DropIn"
    assert select_backend(DEFAULT_BACKEND_NAME) is SerpApiBackend


# Verifies: specs/lead-source-adapters/requirements.md#14.1
def test_two_backends_with_one_name_are_refused_not_silently_overwritten(
    drop_in_dir: Path,
) -> None:
    (drop_in_dir / "twin_a.py").write_text(_backend_module("TwinA", "twin"))
    (drop_in_dir / "twin_b.py").write_text(_backend_module("TwinB", "twin"))
    with pytest.raises(DuplicateSourceNameError):
        select_backend("twin")


# Verifies: specs/lead-source-adapters/requirements.md#14.1
def test_a_backend_for_another_host_is_refused_so_the_key_cannot_follow_it() -> None:
    class Elsewhere(OtherBackend):
        name: ClassVar[str] = "elsewhere"
        base_url: ClassVar[str] = "https://other.test"

    with pytest.raises(ConfigurationError):
        live(Scripted(lambda _: ok()), backend=Elsewhere())


# Verifies: specs/lead-source-adapters/requirements.md#14.7
def test_a_bare_string_is_not_a_list_of_queries() -> None:
    with pytest.raises(TypeError, match="queries"):
        live(Scripted(lambda _: ok()), queries="abc")  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#14.7
def test_queries_and_results_per_query_are_capped() -> None:
    with pytest.raises(ValueError, match="results_per_query"):
        live(Scripted(lambda _: ok()), results_per_query=MAX_RESULTS_PER_QUERY + 1)
    with pytest.raises(ValueError, match="queries"):
        live(Scripted(lambda _: ok()), queries=("q",) * (MAX_QUERIES + 1))


def _real_transport_failing(exc: httpx.TransportError) -> RestTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc.__class__("boom", request=request)

    transport = GoogleSearchSource.build_transport(DataMode.LIVE)
    assert isinstance(transport, RestTransport)
    transport._client = httpx.AsyncClient(
        base_url=GoogleSearchSource.base_url,
        transport=httpx.MockTransport(handler),
    )
    return transport


# Verifies: specs/lead-source-adapters/requirements.md#14.3
@pytest.mark.parametrize("exc", [httpx.ConnectError("x"), httpx.ReadTimeout("x")])
async def test_a_transport_failure_never_carries_the_key_anywhere(
    exc: httpx.TransportError,
) -> None:
    source = GoogleSearchSource(
        DataMode.LIVE,
        transport=_real_transport_failing(exc),
        queries=(QUERY,),
        environ=ENV,
    )
    with pytest.raises(SourceError) as caught:
        await source.fetch_raw(REQUEST)
    rendered = "".join(traceback.format_exception(caught.value))
    link: BaseException | None = caught.value
    while link is not None:
        assert KEY not in str(link)
        assert KEY not in repr(link)
        link = link.__cause__ or link.__context__
    assert KEY not in rendered
    assert QUERY not in rendered


# Verifies: specs/lead-source-adapters/requirements.md#14.3
async def test_a_non_2xx_over_the_real_transport_never_carries_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": str(request.url)})

    transport = GoogleSearchSource.build_transport(DataMode.LIVE)
    assert isinstance(transport, RestTransport)
    transport._client = httpx.AsyncClient(
        base_url=GoogleSearchSource.base_url, transport=httpx.MockTransport(handler)
    )
    source = GoogleSearchSource(
        DataMode.LIVE, transport=transport, queries=(QUERY,), environ=ENV
    )
    with pytest.raises(SourceError) as caught:
        await source.fetch_raw(REQUEST)
    assert KEY not in "".join(traceback.format_exception(caught.value))


# Verifies: specs/lead-source-adapters/requirements.md#14.3
async def test_a_query_cannot_steer_the_request_off_the_declared_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    transport = GoogleSearchSource.build_transport(DataMode.LIVE)
    assert isinstance(transport, RestTransport)
    transport._client = httpx.AsyncClient(
        base_url=GoogleSearchSource.base_url, transport=httpx.MockTransport(handler)
    )
    hostile = "../../x?api_key=evil#@evil.test/ https://evil.test {q}"
    source = GoogleSearchSource(
        DataMode.LIVE, transport=transport, queries=(hostile,), environ=ENV
    )
    await source.fetch_raw(REQUEST)
    (request,) = seen
    assert request.url.host == "serpapi.com"
    assert request.url.scheme == "https"
    assert request.url.path == SEARCH_PATH
    assert request.url.params["q"] == hostile
    assert request.url.params.get_list("api_key") == [KEY]
