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
* An unanchored search contributes no Signal Strength, ``company.name`` or
  ``company.domain`` (an anchored one adds strength and domain, below): strength
  and the tech-versus-intent kind are not in a search result, and a result's host is
  not necessarily the company's. No answerable surface is declared, so no Negative
  Evidence is ever emitted.
* An absent or null block is no evidence; one of the wrong shape raises.

Follow-up decisions (2026-10-06, provider facts checked against live-doc extracts):

* SerpApi's documented empty page (status ``Success``, ``organic_results_state``
  ``Fully empty``, a top-level ``error`` message) is no results: no evidence and no
  error. A 2xx page the backend reports as a failed search (``failed_search``) is
  raised before it is cached: ``SourceQuotaExhausted`` for a spent balance, else a
  permanent ``SourceError``, cause ``search_failed``.
* Pacing for a live run comes from ``run_rate_limit``: the backend's bucket sized from
  its plan setting (``SERPAPI_HOURLY_LIMIT``), listed in ``optional_env``.

Completion of 14.2 (2026-10-06, user decision "option C"; see ``web_evidence``):

* The adapter also runs in Enrichment. Its run-time queries are anchored: one per
  company of the work list (clustered on registrable domains, other sources only) and
  per Target Profile term, ``"<company domain>" <first phrase of the term>``, company
  order then term order, at most ``MAX_QUERIES``; the rest are counted in the payload
  (``unasked_queries``). ``from_run`` no longer turns phrases into unanchored
  Discovery queries, so a run spends nothing on evidence it could not attach. No term
  or no company with a usable domain: no call.
* The anchor (company domains, term, corroborating source count) is kept in the raw
  search, so ``normalize`` reads attachment from the batch alone. Within one anchored
  search a result repeated (``dedupe_key``) is emitted and counted once.
* An attached record adds ``company.domain`` (the anchor's domain, never the result
  host) and ``company.web_evidence.{attachment, agreeing_hosts,
  corroborating_sources, signal_strength, signal_kind, signal_label}``. The kind is
  ``tech`` because every profile term is a technology or competitor product; the
  label is the term. An unattached record adds only ``attachment = unattached``.
  These have provenance through an ``attribution`` block in the per-result wrapper.
* One ``google_search_web_evidence`` log line per normalize with anchored searches:
  attached, unattached, duplicate and unasked counts only.
* Constructor ``queries`` (unanchored) still work for direct use, unchanged.
"""

import math
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Self

import structlog
from pydantic import BaseModel, StrictStr

from leadforge.lead_ingestion.adapters.search_backends import (
    DEFAULT_BACKEND_NAME,
    SearchBackend,
    ThrottleCause,
    select_backend,
)
from leadforge.lead_ingestion.adapters.web_evidence import (
    Attachment,
    CompanyAnchor,
    agreeing_host,
    attachment_of,
    company_anchors,
    dedupe_key,
    signal_strength,
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
    unmapped_raw_paths,
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
# Every profile term is a technology or a competitor product (target_profile).
_SIGNAL_KIND = "tech"
# Per block: where the URL, title and snippet of a result are.
_TEXT_KEYS = {
    "organic_results": ("link", "title", "snippet"),
    "answer_box": ("link", "title", "snippet"),
    "knowledge_graph": ("website", "title", "description"),
}

_log = structlog.get_logger(__name__)


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
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.SEARCH, Capability.ENRICH}
    )
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
    optional_env: ClassVar[tuple[str, ...]] = _DEFAULT_BACKEND.optional_env
    env_notes: ClassVar[Mapping[str, str]] = _DEFAULT_BACKEND.env_notes
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
    # Attachment computed from the anchored search (``web_evidence``), not the text.
    ATTRIBUTION_RULES: ClassVar[tuple[FieldRule, ...]] = (
        FieldRule("company.domain", "attribution.domain"),
        FieldRule(_EVIDENCE + "attachment", "attribution.attachment"),
        FieldRule(_EVIDENCE + "agreeing_hosts", "attribution.agreeing_hosts"),
        FieldRule(
            _EVIDENCE + "corroborating_sources", "attribution.corroborating_sources"
        ),
        FieldRule(_EVIDENCE + "signal_strength", "attribution.signal_strength"),
        FieldRule(_EVIDENCE + "signal_kind", "attribution.signal_kind"),
        FieldRule(_EVIDENCE + "signal_label", "attribution.signal_label"),
    )
    # Fields of a result that are neither contributed nor needed (rank and display only;
    # a result's own publication date is not the retrieval date).
    IGNORED: ClassVar[frozenset[str]] = frozenset(
        {"result.position", "result.displayed_link", "result.source", "result.date"}
    )

    # Page-level fields beside the result blocks: the engine's own status and counts,
    # and the message and state of an empty page (read by ``failed_search`` in the
    # backend, never contributed). Leaves are named, not subtrees, so a field added
    # there still fails the guard.
    ENVELOPE_IGNORED: ClassVar[frozenset[str]] = frozenset(
        {
            "search_metadata.status",
            "search_information.total_results",
            "search_information.organic_results_state",
            "error",
        }
    )

    def __init__(
        self,
        mode: DataMode,
        *,
        transport: Transport,
        queries: Sequence[str] = (),
        terms: Mapping[str, str] | None = None,
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
        term_phrases = dict(terms or {})
        if not all(
            isinstance(t, str) and isinstance(p, str) and t.strip() and p.strip()
            for t, p in term_phrases.items()
        ):
            raise ValueError("every term and its phrase must be non-blank text")
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
        # Profile term -> the phrase an anchored query asks with (completion of 14.2).
        self._terms: Mapping[str, str] = term_phrases
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

    @classmethod
    def run_rate_limit(
        cls, environ: Mapping[str, str] | None = None
    ) -> Mapping[str, RateBucket]:
        """The default backend's bucket, sized from its plan setting (7.1)."""
        bucket = _DEFAULT_BACKEND.rate_bucket_for(
            os.environ if environ is None else environ
        )
        return {bucket.name: bucket}

    def _failed_search_error(self, body: object) -> SourceError:
        """A 2xx page reporting a failed search: never read as zero results.

        A spent balance is still ``SourceQuotaExhausted``; any other failure is a
        permanent ``SourceError`` (one attempt). Text names the cause, never the body.
        """
        if self._backend.throttle_cause(body) is ThrottleCause.BALANCE:
            return SourceQuotaExhausted(self.name, "cause=search_balance_exhausted")
        return SourceError(self.name, "cause=search_failed")

    @classmethod
    def from_run(
        cls,
        mode: DataMode,
        *,
        transport: Transport,
        pacing: "SourcePacing | None",
        vocabulary: Mapping[str, object] | None,
    ) -> Self:
        """Terms are the Target Profile's phrases for this source (task 20, 14.2).

        A term's vocabulary is a phrase or a list of phrases; its first phrase is what
        an anchored query asks with, and a phrase an earlier term already took is not
        asked again. No unanchored query is built: a run's queries are issued per
        discovered company (``fetch_raw`` on an ``EnrichmentRequest``). With no profile
        there are no terms, and a fetch makes no call. ``keyword_templates`` are not
        expanded here.
        """
        terms: dict[str, str] = {}
        for term, value in (vocabulary or {}).items():
            items = [value] if isinstance(value, str) else value
            if not isinstance(items, list | tuple) or not all(
                isinstance(i, str) and i.strip() for i in items
            ):
                raise ValueError(
                    f"google_search vocabulary for term {term!r} must be a phrase "
                    "or a list of phrases"
                )
            if items and items[0] not in terms.values():
                terms[term] = items[0]
        return cls(mode, transport=transport, pacing=pacing, terms=terms)

    @property
    def backend(self) -> SearchBackend:
        return self._backend

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        """Discovery asks the constructor's queries; Enrichment asks anchored ones."""
        credentials: Mapping[str, str] = (
            {}
            if self.data_mode is DataMode.SYNTHETIC
            else resolve_credentials(self, self._environ)
        )
        if isinstance(request, EnrichmentRequest):
            return await self._fetch_anchored(request, credentials)
        searches: list[Mapping[str, Any]] = [
            {"query": query, "pages": await self._pages_of(query, credentials)}
            for query in self._queries
        ]
        return RawBatch(source_name=self.name, payload={"searches": searches})

    async def _fetch_anchored(
        self, request: EnrichmentRequest, credentials: Mapping[str, str]
    ) -> RawBatch:
        """One query per (company, term), company order then term order (14.2)."""
        planned = [
            (anchor, term, phrase)
            for anchor in company_anchors(request.work_list, exclude_source=self.name)
            for term, phrase in self._terms.items()
        ]
        searches: list[Mapping[str, Any]] = []
        for anchor, term, phrase in planned[:MAX_QUERIES]:
            query = f'"{anchor.query_domain}" {phrase}'
            searches.append(
                {
                    "query": query,
                    "anchor": {
                        "domains": list(anchor.domains),
                        "term": term,
                        "corroborating_sources": anchor.corroborating_sources,
                    },
                    "pages": await self._pages_of(query, credentials),
                }
            )
        return RawBatch(
            source_name=self.name,
            payload={
                "searches": searches,
                "unasked_queries": max(0, len(planned) - MAX_QUERIES),
            },
        )

    async def _pages_of(
        self, query: str, credentials: Mapping[str, str]
    ) -> list[Mapping[str, Any]]:
        max_pages = math.ceil(self._results_per_query / self._backend.page_size)
        pages: list[Mapping[str, Any]] = []
        for index in range(max_pages):
            page = await self._page(query, index, credentials)
            pages.append(page)
            if not self._backend.has_next_page(page):
                break
        return pages

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
            if self._backend.failed_search(body):
                raise self._failed_search_error(body)
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
        counts = {"attached": 0, "unattached": 0, "duplicates": 0}
        anchored = False
        for search in searches:
            query = search.get("query") if isinstance(search, Mapping) else None
            pages = search.get("pages") if isinstance(search, Mapping) else None
            if not isinstance(query, str) or not isinstance(pages, list):
                raise _unmapped(self.name, "searches")
            if not all(isinstance(page, Mapping) for page in pages):
                raise _unmapped(self.name, "searches")
            if "anchor" in search:
                anchored = True
                anchor, term = self._anchor_of(search["anchor"])
                contributions.extend(
                    self._anchored_evidence(
                        pages, query, (anchor, term), retrieved.date(), context, counts
                    )
                )
                continue
            for page in pages:
                contributions.extend(
                    self._page_evidence(page, query, retrieved.date(), context)
                )
        if anchored:
            unasked = payload.get("unasked_queries", 0)
            if not isinstance(unasked, int) or isinstance(unasked, bool):
                raise _unmapped(self.name, "unasked_queries")
            _log.info("google_search_web_evidence", **counts, unasked_queries=unasked)
        return contributions

    def _anchor_of(self, raw: object) -> tuple[CompanyAnchor, str]:
        """The anchor kept in an anchored search; a wrong shape raises."""
        if not isinstance(raw, Mapping):
            raise _unmapped(self.name, "searches.anchor")
        domains, term = raw.get("domains"), raw.get("term")
        corroborating = raw.get("corroborating_sources")
        if (
            not isinstance(domains, list)
            or not domains
            or not all(isinstance(d, str) and d for d in domains)
            or not isinstance(term, str)
            or not term
            or not isinstance(corroborating, int)
            or isinstance(corroborating, bool)
            or corroborating < 0
        ):
            raise _unmapped(self.name, "searches.anchor")
        return CompanyAnchor(tuple(domains), corroborating), term

    def _anchored_evidence(
        self,
        pages: list[Mapping[str, Any]],
        query: str,
        anchored_to: tuple[CompanyAnchor, str],
        retrieved_on: date,
        context: NormalizationContext,
        counts: dict[str, int],
    ) -> list[LeadContribution]:
        """Evidence of one anchored search, attached by agreement (``web_evidence``).

        Results are read in page then block order; a repeated URL is dropped and
        counted. Strength is from the agreeing hosts (the company's own domains
        together are one) and corroboration, never text.
        """
        anchor, term = anchored_to
        seen: set[str] = set()
        kept: list[tuple[Mapping[str, object], tuple[FieldRule, ...], Attachment]] = []
        hosts: set[str] = set()
        for page in pages:
            for block, result, model, rules in self._blocks_of(page):
                checked = self._checked(
                    block, result, model, rules, query, retrieved_on
                )
                url_key, title_key, snippet_key = _TEXT_KEYS[block]
                url = result.get(url_key)
                if url is not None:
                    if dedupe_key(url) in seen:
                        counts["duplicates"] += 1
                        continue
                    seen.add(dedupe_key(url))
                texts = [
                    t for t in (result.get(title_key), result.get(snippet_key)) if t
                ]
                how = attachment_of(url, texts, anchor.domains)
                host = agreeing_host(url, how)
                if host is not None:
                    hosts.add(host)
                kept.append((checked, rules, how))
        strength = signal_strength(
            hosts=len(hosts),
            own_domain=any(how is Attachment.OWN_DOMAIN for _, _, how in kept),
            corroborating_sources=anchor.corroborating_sources,
        )
        normalizer = Normalizer()
        found: list[LeadContribution] = []
        for checked, rules, how in kept:
            attribution: dict[str, object] = {"attachment": how.value}
            if how is not Attachment.UNATTACHED:
                attribution |= {
                    "domain": anchor.query_domain,
                    "agreeing_hosts": len(hosts),
                    "corroborating_sources": anchor.corroborating_sources,
                    "signal_strength": strength,
                    "signal_kind": _SIGNAL_KIND,
                    "signal_label": term,
                }
            present = tuple(
                r
                for r in self.ATTRIBUTION_RULES
                if r.raw_field_path.removeprefix("attribution.") in attribution
            )
            contribution = normalizer.apply(
                {**checked, "attribution": attribution}, (*rules, *present), context
            )
            if any(path in contribution.values for path in _EVIDENCE_PATHS):
                found.append(contribution)
                counts[
                    "unattached" if how is Attachment.UNATTACHED else "attached"
                ] += 1
        return found

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

    @classmethod
    def unmapped_fixture_paths(cls, endpoint: str, body: object) -> list[str]:
        if endpoint != "search":
            return super().unmapped_fixture_paths(endpoint, body)
        if not isinstance(body, Mapping):
            raise _unmapped(cls.name, "<response>")
        blocks = cls._blocks_of(body)
        read = {"organic_results", "answer_box", "knowledge_graph"}
        envelope = {k: v for k, v in body.items() if k not in read}
        found = unmapped_raw_paths(envelope, (), cls.ENVELOPE_IGNORED)
        for block, result, _, rules in blocks:
            if not isinstance(result, Mapping):
                raise _unmapped(cls.name, block)
            found += [
                f"{block}.{path.removeprefix('result.')}"
                for path in unmapped_raw_paths({"result": result}, rules, cls.IGNORED)
            ]
        return found

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
