"""The two adapters that read the Target Profile take it through ``from_run`` (task 20).

Google Search takes its terms from it: since the 14.2 completion (option C) a run's
queries are anchored, ``"<company domain>" <first phrase of a term>`` per discovered
company, and no unanchored query is built from the profile.
"""

from collections.abc import Mapping
from typing import Any

import pytest

from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.adapters.google_search import (
    MAX_QUERIES,
    GoogleSearchSource,
)
from leadforge.lead_ingestion.base_source import EnrichmentRequest, SourceRequest
from leadforge.lead_ingestion.models import DataMode

from .test_synthetic_zero_sockets import make_lead

SYNTHETIC = DataMode.SYNTHETIC
COMPANY = "acme-data.com"


async def google_queries(vocabulary: Mapping[str, object] | None) -> list[str]:
    source = GoogleSearchSource.from_run(
        SYNTHETIC,
        transport=GoogleSearchSource.build_transport(SYNTHETIC),
        pacing=None,
        vocabulary=vocabulary,
    )
    discovery: Any = (await source.fetch_raw(SourceRequest(kind="discovery"))).payload
    assert discovery["searches"] == []  # nothing unanchored is ever asked
    work = EnrichmentRequest(
        kind="enrich", work_list=(make_lead("apollo", company__domain=COMPANY),)
    )
    payload: Any = (await source.fetch_raw(work)).payload
    return [search["query"] for search in payload["searches"]]


# Verifies: specs/lead-source-adapters/requirements.md#4.5
async def test_google_search_asks_each_terms_first_phrase_per_company_in_order() -> (
    None
):
    queries = await google_queries(
        {"first_term": ["alpha phrase", "beta phrase"], "second_term": "gamma phrase"}
    )

    assert queries == [f'"{COMPANY}" alpha phrase', f'"{COMPANY}" gamma phrase']


# Verifies: specs/lead-source-adapters/requirements.md#4.5
async def test_google_search_repeated_phrases_are_asked_once() -> None:
    queries = await google_queries({"a": ["same phrase"], "b": ["same phrase"]})

    assert queries == [f'"{COMPANY}" same phrase']


# Verifies: specs/lead-source-adapters/requirements.md#4.5
async def test_google_search_without_a_profile_makes_no_call() -> None:
    assert await google_queries(None) == []
    assert await google_queries({}) == []


# Verifies: specs/lead-source-adapters/requirements.md#4.5
async def test_google_search_queries_are_capped_at_the_adapters_maximum() -> None:
    many = {f"term_{i}": [f"phrase {i}"] for i in range(MAX_QUERIES + 5)}
    # One company, more terms than the cap: the rest are counted, not asked.

    assert len(await google_queries(many)) == MAX_QUERIES


# Verifies: specs/lead-source-adapters/requirements.md#4.5
def test_apollo_takes_its_technologies_from_the_profile_vocabulary() -> None:
    source = ApolloSource.from_run(
        SYNTHETIC,
        transport=ApolloSource.build_transport(SYNTHETIC),
        pacing=None,
        vocabulary={"some_term": ["some_uid"]},
    )

    assert source._uids == ("some_uid",)


# Verifies: specs/lead-source-adapters/requirements.md#4.5
def test_apollo_without_a_profile_keeps_its_own_default_vocabulary() -> None:
    source = ApolloSource.from_run(
        SYNTHETIC,
        transport=ApolloSource.build_transport(SYNTHETIC),
        pacing=None,
        vocabulary=None,
    )

    assert source._uids == ("datastax", "apache_cassandra")


# Verifies: specs/lead-source-adapters/requirements.md#4.5
def test_google_search_refuses_a_vocabulary_that_is_not_phrases() -> None:
    with pytest.raises(ValueError, match="some_term"):
        GoogleSearchSource.from_run(
            SYNTHETIC,
            transport=GoogleSearchSource.build_transport(SYNTHETIC),
            pacing=None,
            vocabulary={"some_term": [1, 2]},
        )
