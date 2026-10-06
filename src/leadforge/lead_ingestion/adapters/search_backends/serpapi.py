"""SerpApi search backend, the default for the Google Search adapter (14.1, 14.3).

``GET /search?engine=google`` with the key as the ``api_key`` request parameter
(14.3). Google discontinued ``num``, so a page is at most ten results and more are
reached with ``start`` (14.7); ``num`` is never sent. A next page exists when
``serpapi_pagination.next`` is present. Throttling-versus-balance exhaustion on a 429
is read by ``throttle_cause`` (14.3); the adapter turns it into an error.

Provisional decisions (see choices.md, task 14.1; revised 2026-10-06):

* The bucket is SerpApi's hourly throughput, which depends on the plan: Free 50,
  Starter 200, Developer 1,000, Production 3,000, Big Data 6,000 searches per hour
  ("50 throughput per hour" ... "6,000 throughput per hour",
  https://serpapi.com/pricing, read in full 2026-10-06). The former self-imposed one
  request per second (3,600 an hour) was above every plan but Big Data.
* The figure is ``SERPAPI_HOURLY_LIMIT``, a non-secret setting read when a live run
  starts. Unset or blank means ``DEFAULT_HOURLY_LIMIT``, 50: the Free plan, the lowest
  documented figure, so the default is safe on every plan. Anything but a positive
  whole number is a ``ConfigurationError`` naming the variable, never the value.
* Pacing is derived from the figure: one request every ``3600 / limit`` seconds, ANDed
  with the hourly window itself, so a cold bucket never bursts and no hour exceeds it.

Provisional decisions (see choices.md, task 14.3):

* SerpApi answers both an exhausted balance and exceeded hourly throughput with a 429
  and an ``error`` string ("exceeds the hourly throughput limit OR your account has
  run out of searches", https://serpapi.com/api-status-and-error-codes, read
  2026-10-06). Only the balance text is documented ("Your account has run out of
  searches."); the throughput wording is not, so it is matched by the word
  "throughput" (an assumption). The free Account API (``account_rate_limit_per_hour``,
  ``total_searches_left``) could tell them apart; it is not called. Matching is
  case-insensitive on a top-level string ``error``; anything else is
  ``UNRECOGNIZED``, which the adapter retries with backoff.

Empty page versus failed search (follow-up, 2026-10-06):

* SerpApi puts a top-level ``error`` on an empty page too. The documented empty page
  keeps ``search_metadata.status`` ``Success`` and has
  ``search_information.organic_results_state`` ``Fully empty`` (the JSON example;
  the field description says "Fully Empty", so the state is compared casefolded)
  (https://serpapi.com/api-status-and-error-codes, read in full 2026-10-06). That page
  is no results. A 2xx page
  whose status is ``Error``, or which carries ``error`` without being that empty page,
  is a failed search (``failed_search``). Only structured fields decide; the message
  wording is never read for it.
"""

from collections.abc import Mapping
from typing import ClassVar

from leadforge.lead_ingestion.adapters.search_backends import (
    SearchBackend,
    SearchCall,
    ThrottleCause,
)
from leadforge.lead_ingestion.base_source import Endpoint, RateBucket, RateWindow
from leadforge.lead_ingestion.errors import ConfigurationError

__all__ = [
    "DEFAULT_HOURLY_LIMIT",
    "HOURLY_LIMIT_ENV",
    "SerpApiBackend",
    "hourly_bucket",
]

_KEY_ENV = "SERPAPI_API_KEY"
_DOCS = "https://serpapi.com/search-api"
_PRICING = "https://serpapi.com/pricing"
_BALANCE_MARKER = "run out of searches"
_THROUGHPUT_MARKER = "throughput"
_STATUS_ERROR = "Error"
_STATUS_SUCCESS = "Success"
_FULLY_EMPTY = "fully empty"  # compared casefolded: the docs spell it both ways

HOURLY_LIMIT_ENV = "SERPAPI_HOURLY_LIMIT"
DEFAULT_HOURLY_LIMIT = 50  # Free plan: the lowest documented hourly throughput
_MAX_LIMIT_DIGITS = 9
_HOUR_S = 3600.0


def hourly_bucket(searches_per_hour: int) -> RateBucket:
    """The ``default`` bucket for a plan's hourly throughput, evenly spaced."""
    return RateBucket(
        name="default",
        windows=(
            RateWindow(requests=searches_per_hour, per_seconds=_HOUR_S),
            RateWindow(requests=1, per_seconds=_HOUR_S / searches_per_hour),
        ),
        documented=True,
        doc_url=_PRICING,
    )


def _hourly_limit(environ: Mapping[str, str]) -> int:
    text = environ.get(HOURLY_LIMIT_ENV, "").strip()
    if not text:
        return DEFAULT_HOURLY_LIMIT
    if (
        not text.isascii()
        or not text.isdigit()
        or len(text) > _MAX_LIMIT_DIGITS
        or int(text) < 1
    ):
        # The value is not echoed, as for every configuration error.
        raise ConfigurationError(
            "environment",
            key_path=HOURLY_LIMIT_ENV,
            detail="must be a positive whole number of searches per hour",
        )
    return int(text)


class SerpApiBackend(SearchBackend):
    name: ClassVar[str] = "serpapi"
    endpoint: ClassVar[Endpoint] = Endpoint(
        method="GET", path="/search", bucket="default"
    )
    rate_bucket: ClassVar[RateBucket] = hourly_bucket(DEFAULT_HOURLY_LIMIT)
    required_env: ClassVar[tuple[str, ...]] = (_KEY_ENV,)
    optional_env: ClassVar[tuple[str, ...]] = (HOURLY_LIMIT_ENV,)
    env_notes: ClassVar[Mapping[str, str]] = {
        HOURLY_LIMIT_ENV: (
            "optional: searches per hour on your SerpApi plan (Free 50, Starter 200, "
            f"Developer 1000, Production 3000, Big Data 6000; {_PRICING}); "
            f"unset means {DEFAULT_HOURLY_LIMIT}"
        )
    }
    base_url: ClassVar[str] = "https://serpapi.com"
    docs_url: ClassVar[str] = _DOCS
    page_size: ClassVar[int] = 10

    @classmethod
    def rate_bucket_for(cls, environ: Mapping[str, str]) -> RateBucket:
        return hourly_bucket(_hourly_limit(environ))

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

    def failed_search(self, body: object) -> bool:
        if not isinstance(body, Mapping):
            return False
        metadata = body.get("search_metadata")
        if metadata is not None and not isinstance(metadata, Mapping):
            return True
        status = metadata.get("status") if isinstance(metadata, Mapping) else None
        if status == _STATUS_ERROR:
            return True
        if "error" not in body:
            return False
        if self.throttle_cause(body) is ThrottleCause.BALANCE:
            return True  # an exhausted account is never an empty page
        information = body.get("search_information")
        state = (
            information.get("organic_results_state")
            if isinstance(information, Mapping)
            else None
        )
        empty = isinstance(state, str) and state.casefold() == _FULLY_EMPTY
        return not (status == _STATUS_SUCCESS and empty)
