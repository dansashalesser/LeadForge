"""Constrained, persisted resolution of an exact primary-domain tie (task 16.11; 8.18).

``elect_primary_domain`` (16.10) reports an exact tie as data. This module is the one
place that may ask a language model to break it, and only through a PORT: the merge code
names no vendor, imports no model client and no store (``TieResolver`` and
``TieResolutionStore`` are Protocols). The projection stays a pure function of stored
data (8.8): it calls ``read_stored_primary_domain``, which takes no resolver at all, and
the answer, once accepted, is persisted so a later run reads it back instead of asking
again. Provisional decisions (choices.md, 16.11):

* The port is ``choose(candidates, *, timeout)`` plus ``model`` and ``prompt_version``
  (the record carries both). The payload is the tied candidate domains and nothing else:
  not the company's other domains, names, ids or signals. One call per distinct tie;
  the timeout is the module constant, handed through the port, which must honour it.
  No retry is added here (the retry policy of ``retry`` is for provider calls).
* Validation is EXACT: the answer must be a ``str`` equal to one candidate. Case,
  whitespace, ``www.`` and every other variant are rejected, never normalised into a
  candidate (the guarantee is "never a domain outside the set"). A rejected answer
  raises ``TieAnswerRejectedError`` inside, is never persisted, and the result falls
  back to the lowest-sorted candidate with a counts-only WARNING. A port failure
  (``TieResolverError`` or ``TimeoutError``) falls back the same way; any other
  exception, and cancellation, propagate.
* The tie's identity is ``tie_key``: sha256 of the company's registrable-domain set and
  the candidate set, both sorted. Not the pseudonymous cluster id, and a company that
  gains a domain is a different tie.
* First write wins: ``put`` returns the record already stored for a key and never
  overwrites it, so an orchestrator retry or a lost race adopts the stored answer.
  There is no supersede path (a changed answer would break byte-identical recompute).
* A stored record is read in any mode, so a recompute stays byte-identical; synthetic
  mode never builds or calls the resolver and never writes, and its tie resolves to the
  lowest-sorted candidate, flagged. Surfacing the flag on the run report (18.x) is not
  built: ``TieOutcome.flagged`` is the seam, as for the over-merge detector.
* Domains may be personal data: they are hidden from ``repr`` and never logged or put in
  an error text; the log line carries the outcome and a candidate count only.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

import structlog

from leadforge.lead_ingestion.companies import CompanyCluster
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.primary_domain import PrimaryDomain

__all__ = [
    "TIE_RESOLVER_TIMEOUT_SECONDS",
    "TieAnswerRejectedError",
    "TieOutcome",
    "TieResolutionRecord",
    "TieResolutionStore",
    "TieResolver",
    "TieResolverError",
    "TieSource",
    "read_stored_primary_domain",
    "resolve_primary_domain",
    "tie_key",
    "validate_tie_answer",
]

_log = structlog.get_logger(__name__)

TIE_RESOLVER_TIMEOUT_SECONDS = 10.0
MAX_MODEL_LENGTH = 128  # the store's column bounds, so a record never fails to insert
MAX_PROMPT_VERSION_LENGTH = 64


class TieResolverError(Exception):
    """The resolver could not answer (transport failure, refusal); raised by a port."""


class TieAnswerRejectedError(ValueError):
    """The resolver answered with something that is not one of the candidates."""


class TieResolver(Protocol):
    """Chooses one of ``candidates``; the only data it is given."""

    @property
    def model(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    def choose(self, candidates: tuple[str, ...], *, timeout: float) -> object: ...


@dataclass(frozen=True)
class TieResolutionRecord:
    """The persisted resolution of one tie (8.18)."""

    chosen_domain: str = field(repr=False)
    candidates: tuple[str, ...] = field(repr=False)
    model: str
    prompt_version: str
    resolved_at: datetime

    def __post_init__(self) -> None:
        if self.chosen_domain not in self.candidates:
            raise ValueError("a resolution must choose one of its candidates")
        for name, value, limit in (
            ("model", self.model, MAX_MODEL_LENGTH),
            ("prompt_version", self.prompt_version, MAX_PROMPT_VERSION_LENGTH),
        ):
            if not value.strip() or len(value) > limit:
                raise ValueError(f"{name} must be non-blank and at most {limit} long")
        if self.resolved_at.tzinfo is None or self.resolved_at.utcoffset() is None:
            raise ValueError("resolved_at must carry a timezone")


class TieResolutionStore(Protocol):
    def get(self, key: str) -> TieResolutionRecord | None: ...

    def put(self, key: str, record: TieResolutionRecord) -> TieResolutionRecord:
        """Store ``record`` unless ``key`` has one; return the record now stored."""
        ...


class TieSource(StrEnum):
    NOT_TIED = "not_tied"
    STORED = "stored"
    RESOLVED = "resolved"
    SYNTHETIC_PROVISIONAL = "synthetic_provisional"
    REJECTED_PROVISIONAL = "rejected_provisional"
    UNAVAILABLE_PROVISIONAL = "unavailable_provisional"
    NO_RESOLVER_PROVISIONAL = "no_resolver_provisional"


_PROVISIONAL = frozenset(
    {
        TieSource.SYNTHETIC_PROVISIONAL,
        TieSource.REJECTED_PROVISIONAL,
        TieSource.UNAVAILABLE_PROVISIONAL,
        TieSource.NO_RESOLVER_PROVISIONAL,
    }
)


@dataclass(frozen=True)
class TieOutcome:
    """The primary domain after a tie, and how it was decided."""

    domain: str | None = field(repr=False)
    source: TieSource

    @property
    def flagged(self) -> bool:
        """True when the lowest-sorted fallback stands in for a resolution."""
        return self.source in _PROVISIONAL


def tie_key(company_domains: tuple[str, ...], candidates: tuple[str, ...]) -> str:
    """Stable identity of a tie: the company's domain set and the candidate set."""
    basis = "\x1e".join(
        "\x1f".join(sorted(set(part))) for part in (company_domains, candidates)
    )
    return sha256(basis.encode("utf-8")).hexdigest()


def validate_tie_answer(answer: object, candidates: tuple[str, ...]) -> str:
    """``answer`` if it is exactly one candidate; else ``TieAnswerRejectedError``."""
    if isinstance(answer, str) and answer in candidates:
        return answer
    raise TieAnswerRejectedError("the answer is not one of the candidate domains")


def read_stored_primary_domain(
    cluster: CompanyCluster, primary: PrimaryDomain, store: TieResolutionStore
) -> str | None:
    """The projection's read: the stored resolution, else ``primary.domain``."""
    if not primary.tied:
        return primary.domain
    stored = store.get(tie_key(cluster.domains, primary.tied_domains))
    return stored.chosen_domain if stored is not None else primary.domain


def _report(outcome: TieOutcome, candidates: int) -> TieOutcome:
    log = _log.warning if outcome.flagged else _log.info
    log(
        "primary_domain_tie_resolution",
        outcome=outcome.source.value,
        candidates=candidates,
    )
    return outcome


def resolve_primary_domain(
    cluster: CompanyCluster,
    primary: PrimaryDomain,
    *,
    mode: DataMode,
    store: TieResolutionStore,
    resolver_factory: Callable[[], TieResolver] | None,
    now: datetime,
) -> TieOutcome:
    """Resolve an exact tie once, persist an accepted answer, never fail the run."""
    if not primary.tied:
        return TieOutcome(primary.domain, TieSource.NOT_TIED)
    candidates = primary.tied_domains
    key = tie_key(cluster.domains, candidates)
    count = len(candidates)

    stored = store.get(key)
    if stored is not None:
        return _report(TieOutcome(stored.chosen_domain, TieSource.STORED), count)

    def fallback(source: TieSource) -> TieOutcome:
        return _report(TieOutcome(primary.domain, source), count)

    if mode is DataMode.SYNTHETIC:
        return fallback(TieSource.SYNTHETIC_PROVISIONAL)  # no resolver, no socket
    if resolver_factory is None:
        return fallback(TieSource.NO_RESOLVER_PROVISIONAL)

    resolver = resolver_factory()
    try:
        answer = resolver.choose(candidates, timeout=TIE_RESOLVER_TIMEOUT_SECONDS)
    except (TieResolverError, TimeoutError):
        return fallback(TieSource.UNAVAILABLE_PROVISIONAL)
    try:
        chosen = validate_tie_answer(answer, candidates)
    except TieAnswerRejectedError:
        return fallback(TieSource.REJECTED_PROVISIONAL)

    record = TieResolutionRecord(
        chosen, candidates, resolver.model, resolver.prompt_version, now
    )
    kept = store.put(key, record)
    source = TieSource.RESOLVED if kept == record else TieSource.STORED
    return _report(TieOutcome(kept.chosen_domain, source), count)
