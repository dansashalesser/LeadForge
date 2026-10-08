"""The synthetic flow's SERP client and page fetcher over the demo dataset.

No socket is opened: the SERP answers from the demo transport and pages from the demo
page table.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import httpx

from leadforge.lead_ingestion.adapters.search_backends import select_backend
from leadforge.lead_ingestion.demo.generator import DATA_DIR
from leadforge.lead_ingestion.demo.transport import (
    DemoLog,
    DemoPages,
    DemoTransport,
    _Tables,
)
from leadforge.lead_ingestion.throttle import SourceThrottle
from leadforge.outreach.usage.budget import UsageBudget
from leadforge.outreach.usage.fetch import PageFetcher
from leadforge.outreach.usage.serp import SerpClient

__all__ = ["demo_usage_deps"]


class _VirtualTime:
    """A clock that only moves when the throttle sleeps: the demo never waits."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class _DemoClient:
    """The two calls ``PageFetcher`` makes, answered from the demo page table.

    Not an ``httpx.Client``: a real client, even over a mock transport, is a send
    the synthetic-run socket guard rightly counts as a connection.
    """

    def __init__(self, pages: DemoPages) -> None:
        self._pages = pages

    def get(self, url: str, **_: object) -> httpx.Response:
        status, body = self._pages.fetch(url)
        return httpx.Response(status, text=body)

    @contextmanager
    def stream(self, method: str, url: str, **_: object) -> Iterator[httpx.Response]:
        status, body = self._pages.fetch(url)
        yield httpx.Response(status, content=body.encode())


def demo_usage_deps(
    budget: UsageBudget, *, max_bytes: int, passage_chars: int
) -> tuple[SerpClient, PageFetcher]:
    tables = _Tables(DATA_DIR)
    log = DemoLog()
    backend = select_backend("serpapi")()
    transport = DemoTransport(
        "google_search", {"search": backend.endpoint}, tables=tables, log=log
    )
    time = _VirtualTime()
    throttle = SourceThrottle(
        "search",
        {backend.rate_bucket.name: backend.rate_bucket},
        clock=time,
        sleep=time.sleep,
    )
    serp = SerpClient(backend, transport, budget, throttle)
    pages = DemoPages(tables, log)

    client = _DemoClient(pages)
    fetcher = PageFetcher(
        cast(httpx.Client, client),
        budget,
        max_bytes=max_bytes,
        passage_chars=passage_chars,
    )
    return serp, fetcher
