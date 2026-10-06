"""SerpApi's real empty-result page versus a failed search (14.4, 14.8).

SerpApi puts a top-level ``error`` on a page with no results as well as on a failed
search. An empty page keeps ``search_metadata.status`` ``Success`` and says
``search_information.organic_results_state`` ``Fully empty``; that is no evidence, not
a source error. Any other page carrying ``error``, or one whose status is ``Error``, is
a failed search and is raised, never read as zero results. The decision reads those
structured fields only, never the wording of the message.
"""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
from leadforge.lead_ingestion.adapters.search_backends.serpapi import SerpApiBackend
from leadforge.lead_ingestion.base_source import Endpoint, SourceRequest
from leadforge.lead_ingestion.errors import (
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.transport import TransportResponse

FIXTURE = (
    Path(__file__).parents[2]
    / "fixtures"
    / "google_search"
    / "no_results"
    / "search.json"
)
REQUEST = SourceRequest(kind="discovery")
EMPTY_MESSAGE = "Google hasn't returned any results for this query."
OUT_OF_SEARCHES = "Your account has run out of searches."


def empty_page() -> dict[str, object]:
    return {
        "search_metadata": {"status": "Success"},
        "search_information": {"organic_results_state": "Fully empty"},
        "error": EMPTY_MESSAGE,
    }


class Fixed:
    def __init__(self, status: int, body: object) -> None:
        self.response = TransportResponse(status=status, headers={}, body=body)
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
        return self.response


def live(transport: Fixed) -> GoogleSearchSource:
    return GoogleSearchSource(
        DataMode.LIVE,
        transport=transport,
        queries=("example query",),
        environ={"SERPAPI_API_KEY": "test-key-not-real"},
    )


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_the_no_results_fixture_has_the_real_empty_page_shape() -> None:
    body = json.loads(FIXTURE.read_text())
    assert body == empty_page()


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_the_no_results_fixture_passes_the_fixture_field_guard() -> None:
    body = json.loads(FIXTURE.read_text())
    GoogleSearchSource.validate_fixture("search", body)
    assert GoogleSearchSource.unmapped_fixture_paths("search", body) == []


# Verifies: specs/lead-source-adapters/requirements.md#14.4
async def test_a_fully_empty_page_is_no_evidence_not_a_source_error() -> None:
    source = live(Fixed(200, empty_page()))
    raw = await source.fetch_raw(REQUEST)
    assert source.normalize(raw) == []


# Verifies: specs/lead-source-adapters/requirements.md#14.4
@pytest.mark.parametrize(
    "body",
    [
        {"search_metadata": {"status": "Error"}, "error": "Something went wrong."},
        {"search_metadata": {"status": "Error"}},
        {"search_metadata": {"status": "Success"}, "error": "Something went wrong."},
        {
            "search_metadata": {"status": "Error"},
            "search_information": {"organic_results_state": "Fully empty"},
            "error": EMPTY_MESSAGE,
        },
        {"error": EMPTY_MESSAGE},
    ],
)
async def test_a_failed_search_on_a_2xx_is_raised_not_read_as_no_results(
    body: dict[str, object],
) -> None:
    transport = Fixed(200, body)
    with pytest.raises(SourceError) as caught:
        await live(transport).fetch_raw(REQUEST)
    assert type(caught.value) is SourceError
    assert "search_failed" in str(caught.value)
    assert "went wrong" not in str(caught.value)
    assert transport.calls == 1  # permanent: one attempt, nothing cached


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_out_of_searches_on_a_2xx_is_still_balance_exhaustion() -> None:
    body = {"search_metadata": {"status": "Error"}, "error": OUT_OF_SEARCHES}
    with pytest.raises(SourceQuotaExhausted):
        await live(Fixed(200, body)).fetch_raw(REQUEST)


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_out_of_searches_inside_the_empty_page_shape_is_balance_exhaustion() -> (
    None
):
    # An exhausted account must never read as "this company has no web presence".
    body = {**empty_page(), "error": OUT_OF_SEARCHES}
    with pytest.raises(SourceQuotaExhausted):
        await live(Fixed(200, body)).fetch_raw(REQUEST)


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_out_of_searches_on_a_429_is_still_balance_exhaustion() -> None:
    with pytest.raises(SourceQuotaExhausted):
        await live(Fixed(429, {"error": OUT_OF_SEARCHES})).fetch_raw(REQUEST)


# Verifies: specs/lead-source-adapters/requirements.md#14.8
async def test_a_429_carrying_the_empty_page_shape_is_still_rate_limited() -> None:
    with pytest.raises(SourceRateLimited):
        await live(Fixed(429, empty_page())).fetch_raw(REQUEST)


# Verifies: specs/lead-source-adapters/requirements.md#14.4
@pytest.mark.parametrize(
    ("body", "failed"),
    [
        (empty_page(), False),
        ({"organic_results": []}, False),
        ({"search_metadata": {"status": "Success"}}, False),
        ({}, False),
        ({"error": EMPTY_MESSAGE}, True),
        ({"search_metadata": {"status": "Error"}}, True),
        ({**empty_page(), "search_information": {"organic_results_state": "x"}}, True),
        ({**empty_page(), "search_metadata": "Success"}, True),
        ({**empty_page(), "search_metadata": {"status": "Processing"}}, True),
        ({**empty_page(), "search_metadata": {}}, True),
        (
            {
                "search_information": {"organic_results_state": "Fully empty"},
                "error": EMPTY_MESSAGE,
            },
            True,
        ),
        ({"error": 5}, True),
    ],
)
def test_the_backend_reads_failure_from_structured_fields(
    body: dict[str, object], failed: bool
) -> None:
    assert SerpApiBackend().failed_search(body) is failed
