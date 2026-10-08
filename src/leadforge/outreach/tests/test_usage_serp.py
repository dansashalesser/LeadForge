"""The usage SERP client: budget, throttle, transport, typed results (Req 4.1)."""

import asyncio
from collections.abc import Mapping

import pytest

from leadforge.lead_ingestion.adapters.search_backends.serpapi import SerpApiBackend
from leadforge.lead_ingestion.throttle import SourceThrottle
from leadforge.lead_ingestion.transport import TransportResponse
from leadforge.outreach.usage.budget import BUDGET_EXHAUSTED, UsageBudget
from leadforge.outreach.usage.queries import QuerySpec
from leadforge.outreach.usage.serp import (
    SearchBudgetExhaustedError,
    SearchFailedError,
    SearchResult,
    SerpClient,
)

SPEC = QuerySpec("own_site", 'site:acme.io "Acme"')


class FakeTransport:
    def __init__(self, body: object, status: int = 200) -> None:
        self.body, self.status, self.calls = body, status, []

    async def send(self, endpoint, *, params, json_body, headers):
        self.calls.append(dict(params or {}))
        return TransportResponse(self.status, {}, self.body)


def _client(transport, searches=5):
    backend = SerpApiBackend()
    return SerpClient(
        backend,
        transport,
        UsageBudget(searches=searches, fetches=1, llm_calls=1),
        SourceThrottle("search", {backend.rate_bucket.name: backend.rate_bucket}),
    )


def _run(coro):
    return asyncio.run(coro)


# Verifies: specs/user-recognition/requirements.md#4.1
def test_returns_typed_results_and_spends_one_search() -> None:
    body = {
        "organic_results": [
            {"title": "T", "link": "https://a.io/x", "snippet": "S", "date": "May 1"},
            {"title": "U", "link": "https://b.io"},
            {"title": "no link"},
        ]
    }
    t = FakeTransport(body)
    c = _client(t)
    out = _run(c.search(SPEC))
    assert out == [
        SearchResult("T", "https://a.io/x", "S", "May 1"),
        SearchResult("U", "https://b.io", "", None),
    ]
    assert t.calls[0]["q"] == SPEC.query
    assert c.budget.used("searches") == 1


# Verifies: specs/user-recognition/requirements.md#4.1
def test_budget_exhausted_raises_without_calling() -> None:
    t = FakeTransport({"organic_results": []})
    c = _client(t, searches=0)
    with pytest.raises(SearchBudgetExhaustedError, match=BUDGET_EXHAUSTED):
        _run(c.search(SPEC))
    assert t.calls == []


def test_empty_page_is_empty_list() -> None:
    assert _run(_client(FakeTransport({})).search(SPEC)) == []


@pytest.mark.parametrize(
    ("body", "status"),
    [("junk", 200), ({"error": "boom"}, 200), ({"organic_results": []}, 500)],
)
def test_failures_are_named(body: object, status: int) -> None:
    with pytest.raises(SearchFailedError):
        _run(_client(FakeTransport(body, status)).search(SPEC))


def test_malformed_results_raise() -> None:
    with pytest.raises(SearchFailedError):
        _run(_client(FakeTransport({"organic_results": "x"})).search(SPEC))


def test_params_are_plain_mapping() -> None:
    t = FakeTransport({})
    _run(_client(t).search(SPEC))
    assert isinstance(t.calls[0], Mapping)
