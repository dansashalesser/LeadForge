"""What each real adapter reports of a batch to the run record (follow-up 2026-10-06).

Requirement 21.2 asks for records fetched per source, and the run record has a Credits
column. ``BaseLeadSource.records_fetched`` and ``credits_spent`` are the adapter-neutral
hooks the orchestrator calls once per successful batch; these tests pin each real
adapter's answer on hand-made batches (counted here straight from the payload) and on
the shipped fixtures.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from leadforge.lead_ingestion.adapters.apollo import ApolloSource, credits_in
from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.adapters.hunter import HunterSource
from leadforge.lead_ingestion.adapters.hunter import credits_in as hunter_credits_in
from leadforge.lead_ingestion.base_source import RawBatch
from leadforge.lead_ingestion.models import DataMode

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _fixture(source: str, name: str) -> Any:
    return json.loads((FIXTURES / source / name).read_text(encoding="utf-8"))


def _apollo(mode: DataMode) -> ApolloSource:
    return ApolloSource(
        mode,
        transport=ApolloSource.build_transport(DataMode.SYNTHETIC),
        environ={},
    )


def _match_batch() -> RawBatch:
    return RawBatch(
        source_name="apollo",
        payload={
            "matches": [
                {
                    "lookup": "a",
                    "rung": "id",
                    "response": _fixture("apollo", "match.json"),
                },
                {
                    "lookup": "b",
                    "rung": "id",
                    "response": _fixture("apollo", "no_match/match.json"),
                },
            ]
        },
    )


# Verifies: specs/lead-source-adapters/requirements.md#21.2
@pytest.mark.parametrize("mode", list(DataMode))
def test_apollo_search_fetches_one_record_per_person_and_spends_nothing(
    mode: DataMode,
) -> None:
    people = _fixture("apollo", "search.json")["people"]
    batch = RawBatch(source_name="apollo", payload={"people": people})
    source = _apollo(mode)
    assert source.records_fetched(batch) == len(people)
    assert source.credits_spent(batch) == 0


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_apollo_live_matches_spend_what_credits_in_counts() -> None:
    batch = _match_batch()
    source = _apollo(DataMode.LIVE)
    assert source.records_fetched(batch) == 2
    assert source.credits_spent(batch) == credits_in(batch) == 1


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_apollo_synthetic_matches_spend_no_credit() -> None:
    assert _apollo(DataMode.SYNTHETIC).credits_spent(_match_batch()) == 0


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_hubspot_fetches_one_record_per_contact_found() -> None:
    contacts = _fixture("hubspot", "contact_search.json")["results"]
    batch = RawBatch(
        source_name="hubspot",
        payload={
            "lookups": [
                {"lookup": "a", "contacts": contacts},
                {"lookup": "b", "contacts": []},
                {"lookup": "c", "contacts": contacts + contacts},
            ]
        },
    )
    source = HubSpotSource(
        DataMode.SYNTHETIC, transport=HubSpotSource.build_transport(DataMode.SYNTHETIC)
    )
    assert source.records_fetched(batch) == 3 * len(contacts)
    assert source.credits_spent(batch) is None  # HubSpot bills no Credits


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_google_fetches_one_record_per_organic_result_on_every_page() -> None:
    page = _fixture("google_search", "search.json")
    empty = {**page, "organic_results": []}
    batch = RawBatch(
        source_name="google_search",
        payload={
            "searches": [
                {"query": "q1", "pages": [page, page]},
                {"query": "q2", "pages": [empty]},
            ]
        },
    )
    source = GoogleSearchSource(
        DataMode.SYNTHETIC,
        transport=GoogleSearchSource.build_transport(DataMode.SYNTHETIC),
    )
    assert source.records_fetched(batch) == 2 * len(page["organic_results"])


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_hunter_reports_addresses_returned_and_its_credits() -> None:
    search = _fixture("hunter", "domain_search.json")
    found = _fixture("hunter", "email_finder.json")
    nothing = {"data": {**found["data"], "email": None}}
    batch = RawBatch(
        source_name="hunter",
        payload={
            "searches": [{"domain": "x.test", "response": search}],
            "finds": [
                {
                    "domain": "x.test",
                    "first_name": "A",
                    "last_name": "B",
                    "linkedin_url": None,
                    "response": found,
                },
                {
                    "domain": "x.test",
                    "first_name": "C",
                    "last_name": "D",
                    "linkedin_url": None,
                    "response": nothing,
                },
            ],
            "verifications": [],
            "credits_billable": True,
        },
    )
    source = HunterSource(
        DataMode.SYNTHETIC, transport=HunterSource.build_transport(DataMode.SYNTHETIC)
    )
    assert source.records_fetched(batch) == len(search["data"]["emails"]) + 1
    assert source.credits_spent(batch) == hunter_credits_in(batch)
