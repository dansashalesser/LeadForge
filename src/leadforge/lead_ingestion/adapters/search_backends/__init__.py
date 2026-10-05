"""The search backend port the Google Search adapter delegates to (14.1).

A backend only describes its provider: the endpoint, rate bucket and credential names
it needs, how one page of one query is requested, and whether another page follows.
It never sends anything. Dispatch, pacing, error classification and the per-run page
cache stay in the adapter, so every backend is paced and classified the same way and
holds no key of its own.

Selection is configuration, not code: ``DEFAULT_BACKEND_NAME`` names the backend, and
``select_backend`` finds it by scanning this package, as the Source Registry finds
adapters. A new backend is one new module here; the adapter is not edited.

Provisional decisions (see choices.md, task 14.1):

* The adapter's class-level declarations (``endpoints``, ``required_env``,
  ``base_url``, ``rate_limit``) are read from the selected backend when the adapter
  module loads, because ``build_transport`` and the generated ``.env.example`` run
  without an instance. An adapter given a backend whose endpoint or credential names
  differ from those is refused, loudly.
* Only the SerpApi backend exists. The Custom Search JSON API has no backend (14.2).
"""

import importlib
import pkgutil
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from leadforge.lead_ingestion.base_source import Endpoint, RateBucket
from leadforge.lead_ingestion.errors import ConfigurationError, DuplicateSourceNameError

__all__ = ["DEFAULT_BACKEND_NAME", "SearchBackend", "SearchCall", "select_backend"]

DEFAULT_BACKEND_NAME = "serpapi"


@dataclass(frozen=True)
class SearchCall:
    """Query parameters and headers for one page request.

    Both can carry a credential, so neither appears in ``repr``.
    """

    params: Mapping[str, object] = field(repr=False)
    headers: Mapping[str, str] = field(repr=False, default_factory=dict)


class SearchBackend(ABC):
    name: ClassVar[str]
    endpoint: ClassVar[Endpoint]
    rate_bucket: ClassVar[RateBucket]
    # Names of the environment variables holding the backend's credentials.
    required_env: ClassVar[tuple[str, ...]]
    base_url: ClassVar[str]
    docs_url: ClassVar[str]
    # Results one request returns at most; never assumed larger (14.7).
    page_size: ClassVar[int]

    @abstractmethod
    def build_call(
        self, query: str, page_index: int, credentials: Mapping[str, str]
    ) -> SearchCall:
        """The request for page ``page_index`` (0-based) of ``query``.

        ``credentials`` holds the resolved ``required_env`` values, or nothing in
        synthetic mode, when no credential is sent.
        """

    @abstractmethod
    def has_next_page(self, body: object) -> bool:
        """Whether the answer says another page follows; unreadable means no."""


def select_backend(name: str) -> type[SearchBackend]:
    """The backend class called ``name``, found by scanning this package."""
    known: dict[str, type[SearchBackend]] = {}
    for info in pkgutil.iter_modules(__path__, f"{__name__}."):
        module = importlib.import_module(info.name)
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, SearchBackend)
                and obj.__module__ == module.__name__
                and not getattr(obj, "__abstractmethods__", None)
            ):
                if obj.name in known and known[obj.name] is not obj:
                    raise DuplicateSourceNameError(obj.name)
                known[obj.name] = obj
    if name not in known:
        # The value is not echoed: a mistyped setting may hold a secret.
        raise ConfigurationError(
            "google_search",
            key_path="backend",
            detail=f"unknown search backend; known: {sorted(known)}",
        )
    return known[name]
