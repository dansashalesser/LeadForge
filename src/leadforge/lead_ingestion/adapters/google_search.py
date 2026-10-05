"""Google Search Source adapter: public web evidence through a search backend (14.1).

The adapter owns what every provider call needs (the pacing awaited in ``_send``, the
classification of an error status, credential resolution, the per-run page cache) and
delegates what is provider-specific to a ``SearchBackend``: how one page of a query is
requested and whether another follows. Swapping the backend changes none of this.
Reading results as Company Signals with untrusted text is task 14.2, and telling
throughput from balance exhaustion on a 429 is task 14.3 (``classify_error``).

Provisional decisions (see choices.md, task 14.1):

* The queries are a constructor argument, none by default: the spec names query
  templates but no source for them, and with none given a fetch makes no call.
* ``results_per_query`` (default 10, so one request) is how many results to read per
  query; it is met by paging, ``ceil(n / page_size)`` requests at most, and paging stops
  early when the backend sees no next page (14.7).
* Every call spends search balance, so a page already fetched this run is kept by
  ``(query, page)`` and a retried fetch repeats no call, resuming after the last
  page that was paid for.
* ``normalize`` reads results as Company Signal contributions (task 14.2, below).
* Cost is PAID and PER_CALL (a search spends balance); no vocabulary or answerable
  surface is declared until 14.2 defines what the adapter contributes.
* ``fixtures/google_search/search.json`` is a hand-made STAND-IN, not captured.

Provisional decisions (see choices.md, task 14.3):

* A 429 is classified once, here: the backend only reads which limit the body names
  (``throttle_cause``), so a new backend needs no change to this logic. Balance spent is
  ``SourceQuotaExhausted`` (source halted, no retry); hourly throughput and an
  unreadable or unknown answer are ``SourceRateLimited`` (bounded retry), cause
  ``hourly_throughput`` or ``unrecognized_throttle``.

Provisional decisions (see choices.md, task 14.2):

* One ``LeadContribution`` per organic result, answer box and knowledge graph, carrying
  only ``company.web_evidence.*`` paths: ``query``, ``block``, ``url``, ``title``,
  ``snippet`` and ``retrieved_on``. No ``person.*`` path is ever filled, so web evidence
  cannot become a Lead (ADR-0001). Title and snippet are ``UntrustedText`` (14.6); the
  URL, our own query and the date are plain text.
* Raw paths are relative to a wrapper ``{query, block, retrieved_on, result}`` built
  per result, so the matched query and retrieval date get provenance like any field.
* No Signal Strength, ``company.name`` or ``company.domain`` is contributed: strength
  and the tech-versus-intent kind are not in a search result, and a result's host is
  not necessarily the company's. No answerable surface is declared, so no Negative
  Evidence is ever emitted.
* An absent or null block is no evidence; one of the wrong shape raises.
"""

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, StrictStr

from leadforge.lead_ingestion.adapters.search_backends import (
    DEFAULT_BACKEND_NAME,
    SearchBackend,
    ThrottleCause,
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
    retry_after_seconds,
)
from leadforge.lead_ingestion.errors import (
    ConfigurationError,
    NormalizationError,
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.normalizer import (
    FieldRule,
    NormalizationContext,
    Normalizer,
    validate_raw_payload,
)
from leadforge.lead_ingestion.transport import Transport, TransportResponse

if TYPE_CHECKING:
    from leadforge.lead_ingestion.pacing import SourcePacing

__all__ = ["GoogleSearchSource"]

_DEFAULT_BACKEND = select_backend(DEFAULT_BACKEND_NAME)
DEFAULT_RESULTS_PER_QUERY = 10
# Every request spends search balance, so one run's spend is bounded up front.
MAX_RESULTS_PER_QUERY = 100
MAX_QUERIES = 100

_EVIDENCE = "company.web_evidence."
# A record is evidence only if the provider supplied at least one of these.
_EVIDENCE_PATHS = (_EVIDENCE + "url", _EVIDENCE + "title", _EVIDENCE + "snippet")


class _Organic(BaseModel):
    link: StrictStr
    title: StrictStr | None = None
    snippet: StrictStr | None = None


class _AnswerBox(BaseModel):
    link: StrictStr | None = None
    title: StrictStr | None = None
    snippet: StrictStr | None = None


class _KnowledgeGraph(BaseModel):
    website: StrictStr | None = None
    title: StrictStr | None = None
    description: StrictStr | None = None


class _Record(BaseModel):
    query: StrictStr
    block: StrictStr
    retrieved_on: StrictStr


class _OrganicRecord(_Record):
    result: _Organic


class _AnswerBoxRecord(_Record):
    result: _AnswerBox


class _KnowledgeGraphRecord(_Record):
    result: _KnowledgeGraph


def _unmapped(source: str, raw_field_path: str) -> NormalizationError:
    return NormalizationError(
        source, raw_field_path=raw_field_path, canonical_path="<unmapped>"
    )


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

    _CONTEXT_RULES: ClassVar[tuple[FieldRule, ...]] = (
        FieldRule(_EVIDENCE + "query", "query"),
        FieldRule(_EVIDENCE + "block", "block"),
        FieldRule(_EVIDENCE + "retrieved_on", "retrieved_on"),
    )
    ORGANIC_RULES: ClassVar[tuple[FieldRule, ...]] = (
        *_CONTEXT_RULES,
        FieldRule(_EVIDENCE + "url", "result.link"),
        FieldRule(_EVIDENCE + "title", "result.title", untrusted=True),
        FieldRule(_EVIDENCE + "snippet", "result.snippet", untrusted=True),
    )
    ANSWER_BOX_RULES: ClassVar[tuple[FieldRule, ...]] = ORGANIC_RULES
    KNOWLEDGE_GRAPH_RULES: ClassVar[tuple[FieldRule, ...]] = (
        *_CONTEXT_RULES,
        FieldRule(_EVIDENCE + "url", "result.website"),
        FieldRule(_EVIDENCE + "title", "result.title", untrusted=True),
        FieldRule(_EVIDENCE + "snippet", "result.description", untrusted=True),
    )
    # Fields of a result that are neither contributed nor needed (rank and display only;
    # a result's own publication date is not the retrieval date).
    IGNORED: ClassVar[frozenset[str]] = frozenset(
        {"result.position", "result.displayed_link", "result.source", "result.date"}
    )

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

    def classify_error(
        self, response: TransportResponse, *, endpoint: Endpoint
    ) -> SourceError | None:
        """The conventional mapping, telling throughput from balance on a 429 (14.8).

        The backend reads which limit was hit; this is the one place it becomes an
        error type. A spent balance is ``SourceQuotaExhausted``: no retry can fix it.
        Exceeded throughput is ``SourceRateLimited`` with ``Retry-After`` when usable.
        An unrecognized answer is also ``SourceRateLimited``: its retry is bounded,
        whereas halting would drop evidence on a limit that may clear within the hour.
        Error text names the cause, never the body. Every other status is the default.
        """
        if response.status != 429:
            return super().classify_error(response, endpoint=endpoint)
        cause = self._backend.throttle_cause(response.body)
        if cause is ThrottleCause.BALANCE:
            return SourceQuotaExhausted(self.name, "cause=search_balance_exhausted")
        return SourceRateLimited(
            self.name,
            cause=(
                "hourly_throughput"
                if cause is ThrottleCause.THROUGHPUT
                else "unrecognized_throttle"
            ),
            retry_after_s=retry_after_seconds(response.headers),
        )

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
            raise _unmapped(self.name, "searches")
        retrieved = datetime.now(UTC)
        context = NormalizationContext(
            source_name=self.name,
            data_mode=self.data_mode,
            fetched_at=retrieved,
            answerable_surfaces=self.answerable_surfaces,
        )
        contributions: list[LeadContribution] = []
        for search in searches:
            query = search.get("query") if isinstance(search, Mapping) else None
            pages = search.get("pages") if isinstance(search, Mapping) else None
            if not isinstance(query, str) or not isinstance(pages, list):
                raise _unmapped(self.name, "searches")
            for page in pages:
                if not isinstance(page, Mapping):
                    raise _unmapped(self.name, "searches")
                contributions.extend(
                    self._page_evidence(page, query, retrieved.date(), context)
                )
        return contributions

    @classmethod
    def _blocks_of(
        cls, page: Mapping[str, Any]
    ) -> list[tuple[str, Any, type[BaseModel], tuple[FieldRule, ...]]]:
        organic = page.get("organic_results")
        if organic is not None and not isinstance(organic, list):
            raise _unmapped(cls.name, "organic_results")
        blocks: list[tuple[str, Any, type[BaseModel], tuple[FieldRule, ...]]] = [
            ("organic_results", item, _OrganicRecord, cls.ORGANIC_RULES)
            for item in organic or []
        ]
        optional: tuple[tuple[str, type[BaseModel], tuple[FieldRule, ...]], ...] = (
            ("answer_box", _AnswerBoxRecord, cls.ANSWER_BOX_RULES),
            ("knowledge_graph", _KnowledgeGraphRecord, cls.KNOWLEDGE_GRAPH_RULES),
        )
        for block, model, rules in optional:
            if page.get(block) is not None:
                blocks.append((block, page[block], model, rules))
        return blocks

    @classmethod
    def _checked(
        cls,
        block: str,
        result: object,
        model: type[BaseModel],
        rules: tuple[FieldRule, ...],
        query: str,
        retrieved_on: date,
    ) -> Mapping[str, object]:
        if not isinstance(result, Mapping):
            raise _unmapped(cls.name, block)
        wrapped = {
            "query": query,
            "block": block,
            "retrieved_on": retrieved_on.isoformat(),
            "result": result,
        }
        return validate_raw_payload(cls.name, model, wrapped, rules)

    @classmethod
    def validate_fixture(cls, endpoint: str, body: object) -> None:
        if endpoint != "search":
            super().validate_fixture(endpoint, body)
            return
        if not isinstance(body, Mapping):
            raise _unmapped(cls.name, "<response>")
        for block, result, model, rules in cls._blocks_of(body):
            cls._checked(block, result, model, rules, "fixture", date.min)

    def _page_evidence(
        self,
        page: Mapping[str, Any],
        query: str,
        retrieved_on: date,
        context: NormalizationContext,
    ) -> list[LeadContribution]:
        """Evidence records of one result page: organic results, then optional blocks.

        An absent or null block is no evidence; a block of the wrong shape raises.
        """
        blocks = self._blocks_of(page)
        normalizer = Normalizer()
        found: list[LeadContribution] = []
        for block, result, model, rules in blocks:
            checked = self._checked(block, result, model, rules, query, retrieved_on)
            contribution = normalizer.apply(checked, rules, context)
            if any(path in contribution.values for path in _EVIDENCE_PATHS):
                found.append(contribution)
        return found
