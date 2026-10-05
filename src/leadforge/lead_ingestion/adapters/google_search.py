"""Google Search Source adapter: public web evidence through a search backend (14.1).

The adapter owns what every provider call needs (the pacing awaited in ``_send``, the
classification of an error status, credential resolution, the per-run page cache) and
delegates what is provider-specific to a ``SearchBackend``: how one page of a query is
requested and whether another follows. Swapping the backend changes none of this.
Reading results as Company Signals with untrusted text is task 14.2, and telling
throughput from balance exhaustion on a 429 is task 14.3.

Provisional decisions (see choices.md, task 14.1):

* The queries are a constructor argument, none by default: the spec names query
  templates but no source for them, and with none given a fetch makes no call.
* ``results_per_query`` (default 10, so one request) is how many results to read per
  query; it is met by paging, ``ceil(n / page_size)`` requests at most, and paging stops
  early when the backend sees no next page (14.7).
* Every call spends search balance, so a page already fetched this run is kept by
  ``(query, page)`` and a retried fetch repeats no call, resuming after the last
  page that was paid for.
* ``normalize`` is task 14.2: an empty batch yields nothing and a batch holding results
  raises, so no result is silently dropped.
* Cost is PAID and PER_CALL (a search spends balance); no vocabulary or answerable
  surface is declared until 14.2 defines what the adapter contributes.
* ``fixtures/google_search/search.json`` is a hand-made STAND-IN, not captured.
"""

import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from leadforge.lead_ingestion.adapters.search_backends import (
    DEFAULT_BACKEND_NAME,
    SearchBackend,
    select_backend,
)
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
    resolve_credentials,
)
from leadforge.lead_ingestion.errors import (
    ConfigurationError,
    NormalizationError,
    SourceError,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.transport import Transport

if TYPE_CHECKING:
    from leadforge.lead_ingestion.pacing import SourcePacing

__all__ = ["GoogleSearchSource"]

_DEFAULT_BACKEND = select_backend(DEFAULT_BACKEND_NAME)
DEFAULT_RESULTS_PER_QUERY = 10
# Every request spends search balance, so one run's spend is bounded up front.
MAX_RESULTS_PER_QUERY = 100
MAX_QUERIES = 100


class GoogleSearchSource(BaseLeadSource):
    name: ClassVar[str] = "google_search"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {
        _DEFAULT_BACKEND.rate_bucket.name: _DEFAULT_BACKEND.rate_bucket
    }
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.PAID
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {"search": _DEFAULT_BACKEND.endpoint}
    required_env: ClassVar[tuple[str, ...]] = _DEFAULT_BACKEND.required_env
    docs_url: ClassVar[str] = _DEFAULT_BACKEND.docs_url
    base_url: ClassVar[str] = _DEFAULT_BACKEND.base_url

    def __init__(
        self,
        mode: DataMode,
        *,
        transport: Transport,
        queries: Sequence[str] = (),
        results_per_query: int = DEFAULT_RESULTS_PER_QUERY,
        backend: SearchBackend | None = None,
        environ: Mapping[str, str] | None = None,
        pacing: "SourcePacing | None" = None,
    ) -> None:
        super().__init__(mode, transport=transport, pacing=pacing)
        if isinstance(queries, str):
            raise TypeError("queries must be a sequence of queries, not one string")
        if len(queries) > MAX_QUERIES:
            raise ValueError(f"at most {MAX_QUERIES} queries per run")
        if any(not q.strip() for q in queries):
            raise ValueError("every query must be non-blank")
        if results_per_query < 1:
            raise ValueError(
                f"results_per_query must be at least 1, got {results_per_query}"
            )
        if results_per_query > MAX_RESULTS_PER_QUERY:
            raise ValueError(
                f"results_per_query must be at most {MAX_RESULTS_PER_QUERY}"
            )
        self._backend = _DEFAULT_BACKEND() if backend is None else backend
        if (
            self._backend.endpoint not in self.endpoints.values()
            or self._backend.base_url != self.base_url
            or self._backend.required_env != self.required_env
        ):
            raise ConfigurationError(
                self.name,
                key_path="backend",
                detail="backend endpoint, host or credentials differ from the adapter",
            )
        self._queries = tuple(queries)
        self._results_per_query = results_per_query
        self._environ = environ
        # Pages already paid for this run: a retried fetch must not buy them again.
        self._pages: dict[tuple[str, int], Mapping[str, Any]] = {}

    @property
    def backend(self) -> SearchBackend:
        return self._backend

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        if isinstance(request, EnrichmentRequest):
            raise SourceError(
                self.name, "google_search answers discovery only, not enrichment"
            )
        credentials: Mapping[str, str] = (
            {}
            if self.data_mode is DataMode.SYNTHETIC
            else resolve_credentials(self, self._environ)
        )
        max_pages = math.ceil(self._results_per_query / self._backend.page_size)
        searches: list[Mapping[str, Any]] = []
        for query in self._queries:
            pages: list[Mapping[str, Any]] = []
            for index in range(max_pages):
                page = await self._page(query, index, credentials)
                pages.append(page)
                if not self._backend.has_next_page(page):
                    break
            searches.append({"query": query, "pages": pages})
        return RawBatch(source_name=self.name, payload={"searches": searches})

    async def _page(
        self, query: str, index: int, credentials: Mapping[str, str]
    ) -> Mapping[str, Any]:
        key = (query, index)
        if key not in self._pages:
            call = self._backend.build_call(query, index, credentials)
            response = await self._send(
                self._backend.endpoint,
                params=call.params,
                json_body=None,
                headers=call.headers,
            )
            body = response.body
            if not isinstance(body, Mapping):
                raise NormalizationError(
                    self.name, raw_field_path="<response>", canonical_path="<unmapped>"
                )
            self._pages[key] = body
        return self._pages[key]

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        payload = raw.payload
        searches = payload.get("searches") if isinstance(payload, Mapping) else None
        if not isinstance(searches, list):
            raise NormalizationError(
                self.name, raw_field_path="searches", canonical_path="<unmapped>"
            )
        if searches:
            raise SourceError(
                self.name, "reading search results is not built yet (task 14.2)"
            )
        return []
