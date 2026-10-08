"""The usage SERP client: one family query via the ingestion backend (Req 4.1).

Reuses ingestion's backend (request shape), transport and throttle; spends one search
from the run budget per query. Exhausted budget raises ``SearchBudgetExhaustedError`` so the
stage stops and grades the company ``unverified`` with ``BUDGET_EXHAUSTED``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from leadforge.lead_ingestion.adapters.search_backends import SearchBackend
from leadforge.lead_ingestion.throttle import SourceThrottle
from leadforge.lead_ingestion.transport import Transport
from leadforge.outreach.usage.budget import BUDGET_EXHAUSTED, UsageBudget
from leadforge.outreach.usage.queries import QuerySpec

__all__ = [
    "SearchBudgetExhaustedError",
    "SearchFailedError",
    "SearchResult",
    "SerpClient",
]


class SearchBudgetExhaustedError(Exception):
    """The run's search budget is spent (reason ``BUDGET_EXHAUSTED``)."""

    reason = BUDGET_EXHAUSTED

    def __init__(self) -> None:
        super().__init__(BUDGET_EXHAUSTED)


class SearchFailedError(Exception):
    """The search backend answered with an error or an unreadable body."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    date: str | None = None


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


class SerpClient:
    def __init__(
        self,
        backend: SearchBackend,
        transport: Transport,
        budget: UsageBudget,
        throttle: SourceThrottle,
        *,
        credentials: Mapping[str, str] | None = None,
    ) -> None:
        self.budget = budget
        self._backend = backend
        self._transport = transport
        self._throttle = throttle
        self._credentials = dict(credentials or {})

    async def search(self, spec: QuerySpec) -> list[SearchResult]:
        if not self.budget.spend("searches"):
            raise SearchBudgetExhaustedError
        endpoint = self._backend.endpoint
        call = self._backend.build_call(spec.query, 0, self._credentials)
        await self._throttle.bucket(endpoint.bucket).acquire()
        response = await self._transport.send(
            endpoint, params=call.params, json_body=None, headers=call.headers
        )
        body = response.body
        if response.status >= 400 or not isinstance(body, Mapping):
            raise SearchFailedError(f"search answered status {response.status}")
        if self._backend.failed_search(body):
            raise SearchFailedError("search backend reported a failed search")
        rows = body.get("organic_results", [])
        if not isinstance(rows, list):
            raise SearchFailedError("organic results are not a list")
        out: list[SearchResult] = []
        for row in rows:
            if not isinstance(row, Mapping) or not (url := _text(row, "link")):
                continue
            out.append(
                SearchResult(
                    _text(row, "title"),
                    url,
                    _text(row, "snippet"),
                    _text(row, "date") or None,
                )
            )
        return out
