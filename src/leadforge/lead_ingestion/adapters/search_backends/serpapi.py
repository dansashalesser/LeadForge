"""SerpApi search backend, the default for the Google Search adapter (14.1, 14.3).

``GET /search?engine=google`` with the key as the ``api_key`` request parameter
(14.3). Google discontinued ``num``, so a page is at most ten results and more are
reached with ``start`` (14.7); ``num`` is never sent. A next page exists when
``serpapi_pagination.next`` is present. Throttling-versus-balance exhaustion on a 429
is read by ``throttle_cause`` (14.3); the adapter turns it into an error.

Provisional decisions (see choices.md, task 14.1):

* The bucket is one request per second, self-imposed and undocumented: SerpApi's
  hourly throughput depends on the plan, and no figure is quoted for it.

Provisional decisions (see choices.md, task 14.3):

* SerpApi answers both an exhausted balance and exceeded hourly throughput with a 429
  and an ``error`` string. Only the balance text is documented ("Your account has run
  out of searches."); the throughput wording is not, so it is matched by the word
  "throughput" (an assumption). Matching is case-insensitive on a top-level string
  ``error``; anything else is ``UNRECOGNIZED``, which the adapter retries with backoff.
"""

from collections.abc import Mapping
from typing import ClassVar

from leadforge.lead_ingestion.adapters.search_backends import (
    SearchBackend,
    SearchCall,
    ThrottleCause,
)
from leadforge.lead_ingestion.base_source import Endpoint, RateBucket, RateWindow

__all__ = ["SerpApiBackend"]

_KEY_ENV = "SERPAPI_API_KEY"
_DOCS = "https://serpapi.com/search-api"
_BALANCE_MARKER = "run out of searches"
_THROUGHPUT_MARKER = "throughput"


class SerpApiBackend(SearchBackend):
    name: ClassVar[str] = "serpapi"
    endpoint: ClassVar[Endpoint] = Endpoint(
        method="GET", path="/search", bucket="default"
    )
    rate_bucket: ClassVar[RateBucket] = RateBucket(
        name="default",
        windows=(RateWindow(requests=1, per_seconds=1.0),),
        documented=False,
        doc_url=_DOCS,
    )
    required_env: ClassVar[tuple[str, ...]] = (_KEY_ENV,)
    base_url: ClassVar[str] = "https://serpapi.com"
    docs_url: ClassVar[str] = _DOCS
    page_size: ClassVar[int] = 10

    def build_call(
        self, query: str, page_index: int, credentials: Mapping[str, str]
    ) -> SearchCall:
        params: dict[str, object] = {
            "engine": "google",
            "q": query,
            "start": page_index * self.page_size,
        }
        if _KEY_ENV in credentials:
            params["api_key"] = credentials[_KEY_ENV]
        return SearchCall(params=params)

    def has_next_page(self, body: object) -> bool:
        paging = body.get("serpapi_pagination") if isinstance(body, Mapping) else None
        next_url = paging.get("next") if isinstance(paging, Mapping) else None
        return isinstance(next_url, str) and bool(next_url.strip())

    def throttle_cause(self, body: object) -> ThrottleCause:
        text = body.get("error") if isinstance(body, Mapping) else None
        if not isinstance(text, str):
            return ThrottleCause.UNRECOGNIZED
        lowered = text.casefold()
        if _BALANCE_MARKER in lowered:
            return ThrottleCause.BALANCE
        if _THROUGHPUT_MARKER in lowered:
            return ThrottleCause.THROUGHPUT
        return ThrottleCause.UNRECOGNIZED
