"""Ingestion Orchestrator: bounded concurrent run of the enabled sources (task 11.1).

Requirements 6.7 and 6.8. The orchestrator touches adapters only through the
``BaseLeadSource`` contract and never names a concrete adapter (2.4).

Flow of ``run``:

1. Resolve the data mode of every enabled source, and build its pacing, *before* any
   pool slot exists, so a synthetic source can never reserve throttle capacity.
   Pacing is ``None`` for synthetic (``build_pacing`` constructs nothing).
2. Construct the adapters through ``SourceRegistry.active``, the one construction
   point, handing each its resolved mode and its own pacing.
3. Run them concurrently behind an ``asyncio.Semaphore`` of ``max_concurrent_sources``.

The semaphore is a cross-source bound only. It never touches a token bucket, and a
bucket never touches it, so neither mechanism can relax the other (6.8): each adapter
paces its own provider calls through the throttle it was handed.

Provisional decisions (see choices.md, task 11.1):

* The bound is a required constructor argument with no default here; the default of
  four lives in ``source_settings`` next to the key it belongs to.
* Mode resolution and adapter construction are injected (``resolve_mode``,
  ``build_source``); this module does not read the environment or build transports.
  Plan-dependent rate limits are injected too (``live_rate_limits``, follow-up
  2026-10-06): the composition root reads them, for live sources only, before the run
  record exists, so a bad plan value is a configuration error before any write. A live
  source with no entry is paced on its declared ``rate_limit``.
* Phases are a later task (11.5 to 11.7).

Failure isolation (task 11.2, Requirements 6.1 to 6.3). Each source has a
``SourceCallLedger`` for the run. A ``SourceError`` raised by fetch or normalization is
recorded against that source as a ``SourceStatus`` (chosen by exception type, never by
wire status) and the other sources carry on. Provisional decisions (choices.md, 11.2):

* Only the ``SourceError`` taxonomy is isolated. A non-``SourceError`` ``Exception`` (a
  programming error) and every ``BaseException`` (``CancelledError`` included) propagate
  untouched; the former cancels siblings through the ``TaskGroup`` and surfaces as an
  ``ExceptionGroup``, loudly, rather than being recorded as a provider failure.
* ``SourceUnauthorized`` and ``SourceQuotaExhausted`` halt the source: later calls in
  the run are skipped, not attempted. ``SourceRateLimited`` does not halt; each call
  goes through the source's ``RetryPolicy``, which backs off before every retry.
* A live source's call runs through its ``pacing.retry``; a synthetic source has no
  pacing, so it gets one attempt and no backoff.
* The retry, and its backoff sleep, runs while the pool slot is held, so the bound is
  never exceeded by a backing-off source.
* No throttle feedback is passed to the policy: the orchestrator cannot know which
  bucket a response belongs to (the adapter does).

Run timeout (task 11.4, Requirement 6.6). ``run`` is held to ``run_timeout_s`` of wall
clock with ``asyncio.timeout``. Provisional decisions (choices.md, 11.4):

* The timeout is a required constructor argument, like the pool bound; its default and
  config key (``run_timeout_s``) live in ``source_settings``.
* Every source is accounted for. A source that finished keeps its result untouched;
  one in flight is cancelled and recorded ``TIMED_OUT`` (its cancelled call counts as
  one failed call); one still waiting for a pool slot is never called and is recorded
  ``TIMED_OUT`` with zero attempts. No new status: ``TIMED_OUT`` is the one class.
* Only the orchestrator's own deadline becomes a record. The caller's cancellation,
  and any other exception, still propagate (``asyncio.timeout`` turns only its own
  expiry into ``TimeoutError``, which is checked with ``expired()``).
* Results still follow the registry's active order.

Two-phase run (task 11.5, Requirement 6.9; ADR-0002). Provisional decisions
(choices.md, 11.5):

* Discovery runs every ``search``-capable source; once all have finished, Enrichment
  runs every ``enrich``-capable source over ``enrichment_work_list``. A source
  declaring both runs in both phases; one declaring neither never runs.
* The work list is a pure function of the Discovery results alone: every contribution
  a Discovery source normalized, in result order, none filtered, ranked or merged. It
  reads no score and makes no qualification judgment. Enrichment sources receive it
  as ``EnrichmentRequest.work_list``.
* An empty work list makes Enrichment a no-op: no enrichment source is called and no
  Enrichment result is recorded. After a timeout, Enrichment sources never reached are
  still recorded ``TIMED_OUT``.
* One pool, one deadline, one ledger per source span both phases, so the bound, the
  run timeout and a halting failure (unauthorized, quota) hold across them. A halted
  source's Enrichment call is recorded as skipped, not attempted.
* ``SourceResult`` is per source per phase (``phase`` field); Discovery results come
  first, then Enrichment results, each in registry order. ``outcome`` is the source's
  cumulative ledger when that result was built.
* Enrichment sources run concurrently in registry order for now. The seam for task
  11.6 is the list of Enrichment sources in ``run``: it derives the order and the
  suppression-driven pruning of the work list. Task 11.7 (per-company calls) acts on
  the same work list.

Enrichment order (task 11.6, Requirement 6.10; ADR-0002). Provisional decisions
(choices.md, 11.6):

* Order comes only from the declared ``cost_class``, ``charge_unit``,
  ``yields_suppression`` and (ADR-0006) ``evidence_only`` (``enrichment_tiers``);
  nothing here lists a source. Sources
  with equal declarations form a tier. Tiers run one after another, so a free
  Suppression-bearing source has finished before any Credit-bearing source starts;
  inside a tier the sources still run concurrently under the pool bound. This
  supersedes the 11.5 "concurrently in registry order" Enrichment behaviour.
* After each tier, a lead a source reported as ``suppressed`` or ``opt_out`` leaves the
  work list before the next tier is called. A report is a contribution whose values
  carry that flag as ``True``; it names the lead by the ``email`` or ``linkedin_url``
  it shares with a work-list contribution (no merge stage exists to give a lead a
  stronger identity). A contribution already carrying a flag when Discovery produced
  it is removed too, before the first tier. ``enrichment_work_list`` itself stays
  unfiltered.
* When pruning empties the work list, the remaining tiers are not called and record no
  result, exactly like an empty work list from Discovery (11.5).
* Results still follow registry order within each phase.

Tiers feed later tiers (follow-up 2026-10-06, ADR-0006 amending ADR-0002; user
decision: sources enhance each other). Provisional decisions (choices.md, follow-up):

* After a tier finishes, its contributions are appended to the work list, in tier
  (name) order, before the next tier is called. A later tier therefore sees the
  people, addresses, LinkedIn URLs and company domains an earlier tier added (a
  verified address reaches the per-lead match; the match's company domain reaches the
  evidence-only source). Results are untouched: each still holds only its own source's
  contributions.
* One forward pass: every tier is called at most once and nothing found later is fed
  back to an earlier tier, with one exception below (the second, free pass).
* Pruning uses every report so far, not only the last tier's, so a person suppressed
  by an earlier tier and re-added by a later tier's record leaves the list again before
  the next tier. ``enrichment_work_list`` (Discovery only) is unchanged.
* Per-person dedupe is the adapters' (per-run caches keyed by the lookup; a per-lead
  match asks once per person across the records naming them); per-company dedupe
  still runs on the fed list (``per_company_work_list``), a fed record joining the
  person it names.

Opt-outs follow strong identity links (user decision 2026-10-06, ADR-0006 amended):

* ``prune_flagged`` also drops every record strongly linked to a flagged one,
  transitively over the work list and the reports: the strong links are the LinkedIn
  URL and verified-email Match Keys (``extract_match_keys``, the same normalizers),
  never name+domain, and never an address 8.14's structural pass
  (``DisqualifiedAddresses``) finds shared (a role address such as ``info@``).
  Components come from clustering's union-find, so the result is order-free and
  near-linear. The direct match (an identity a flagged record names, any email
  status) is unchanged. A value the run's Identity Exclusions bar (8.13) is no Match
  Key, so it links no one here either (``identity_exclusions``, from the runner).

A second, free pass (user decision 2026-10-06, ADR-0006 amended):

* After the forward pass, each free tier (``cost_class: free``) is called once more,
  with only the pruned work-list records naming an email or LinkedIn identity that
  tier was neither handed nor answered itself. Nothing new: no call, no result. No
  paid tier runs after it, so it moves no spend; the adapters' per-run caches stop a
  repeat lookup.
* Its result is a second ``ENRICHMENT`` result of the same source, after every other
  result, on the same ledger (counts stay per source); its contributions, opt-outs
  included, reach the merge like any other. A ``SourceError`` in it is recorded and
  isolated like any call; a deadline that cuts it short records it ``TIMED_OUT``.

Per-company calls (task 11.7, Requirement 6.11; ADR-0002). Provisional decisions
(choices.md, 11.7):

* A tier whose sources declare ``charge_unit: per_company`` is handed
  ``per_company_work_list(work_list)``: the first Lead of each distinct company, in
  work-list order. Every other tier keeps the whole list, so a per-lead source still
  works every Lead. The orchestrator still invokes a source once per phase; the source
  makes one provider call per work-list item, so the item count is the billable count.
  The ledger therefore counts orchestrator invocations (1), not provider calls.
* Dedupe runs on the already pruned list, so a suppressed Lead is never the one that
  stands for its company. The input tuple is untouched.
* A company is the set of registrable domains at ``company.domain`` (task 16.9,
  Requirement 8.16): a str or a collection of str, reduced under the pinned Public
  Suffix List, webmail excluded. Leads whose sets overlap, transitively, are one
  company (``companies.domain_components``), so ``acme.com`` and ``{acme.com, acme.io}``
  are one.
* A Lead with no usable domain (none, blank, a bare suffix, an IP, ``localhost``,
  webmail only) stands for itself and is never merged; a non-text value is a
  ``TypeError``, not ignored.
* A company-level result is not copied onto the Leads sharing the company here: the
  Company Signal is shared through Employment (ADR-0001), joined by a later stage.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import uuid
from collections.abc import Awaitable, Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

import structlog

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    EnrichmentRequest,
    LeadContribution,
    LiveAccess,
    RateBucket,
    RawBatch,
    SourceRequest,
    enrichment_tiers,
)
from leadforge.lead_ingestion.clustering import _UnionFind
from leadforge.lead_ingestion.companies import company_domains, domain_components
from leadforge.lead_ingestion.compliance import (
    Identity,
    blocked_identities,
    flags_set,
    identities,
)
from leadforge.lead_ingestion.errors import (
    NormalizationError,
    SourceComplianceRestricted,
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
    SourceTimedOut,
    SourceTransient,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.match_keys import (
    DisqualifiedAddresses,
    IdentityExclusions,
    MatchKeyKind,
    extract_match_keys,
)
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.pacing import SourcePacing, build_pacing
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.retry import RetryPolicy, RetryStats
from leadforge.lead_ingestion.throttle import ThrottleSnapshot

__all__ = [
    "AdapterFactory",
    "Attempt",
    "IngestionOrchestrator",
    "ModeResolverWithReason",
    "Phase",
    "ReportsAllowances",
    "RunRecorder",
    "SourceCallLedger",
    "SourceOutcome",
    "SourceResult",
    "SourceStatus",
    "enrichment_work_list",
    "per_company_work_list",
    "prune_flagged",
]

ModeResolverWithReason = Callable[
    [type[BaseLeadSource], SourceSettings], ModeResolution
]
AdapterFactory = Callable[
    [type[BaseLeadSource], DataMode, SourcePacing | None], BaseLeadSource
]


_log = structlog.get_logger(__name__)


@runtime_checkable
class ReportsAllowances(Protocol):
    """An adapter that reads per-window allowances from its provider's responses."""

    @property
    def allowances(self) -> Mapping[str, int]: ...


class RunRecorder(Protocol):
    """Persists the run record (task 18.1); the orchestrator knows no store.

    ``finish`` receives the run's results, or ``None`` when the run was aborted by an
    exception or by cancellation.
    """

    async def start(
        self,
        resolutions: Mapping[str, ModeResolution],
        settings: Mapping[str, SourceSettings],
        *,
        pool_size: int,
        run_timeout_s: float,
        live_access: Mapping[str, LiveAccess],
    ) -> uuid.UUID: ...

    async def finish(
        self, run_id: uuid.UUID, results: tuple[SourceResult, ...] | None
    ) -> None: ...


class SourceStatus(StrEnum):
    """How a source ended the run; the failure class when it did not succeed."""

    OK = "ok"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TRANSIENT = "transient"
    TIMED_OUT = "timed_out"
    COMPLIANCE_RESTRICTED = "compliance_restricted"
    NORMALIZATION_FAILED = "normalization_failed"
    FAILED = "failed"  # any other SourceError


# Most specific first; the first isinstance match wins.
_STATUS_BY_ERROR: tuple[tuple[type[SourceError], SourceStatus], ...] = (
    (SourceUnauthorized, SourceStatus.UNAUTHORIZED),
    (SourceRateLimited, SourceStatus.RATE_LIMITED),
    (SourceQuotaExhausted, SourceStatus.QUOTA_EXHAUSTED),
    (SourceTransient, SourceStatus.TRANSIENT),
    (SourceTimedOut, SourceStatus.TIMED_OUT),
    (SourceComplianceRestricted, SourceStatus.COMPLIANCE_RESTRICTED),
    (NormalizationError, SourceStatus.NORMALIZATION_FAILED),
)
# Failure classes after which the source is not called again in this run.
_HALTING = frozenset({SourceStatus.UNAUTHORIZED, SourceStatus.QUOTA_EXHAUSTED})


def _outcome_message(error: SourceError) -> str:
    """The error text safe to keep in a run outcome.

    A compliance restriction names the person it concerns (``subject``), who must not
    be copied into the run record; every other class names only providers and paths.
    """
    if isinstance(error, SourceComplianceRestricted):
        return f"[{error.source_name}] {type(error).__name__}"
    return str(error)


def _status_of(error: SourceError) -> SourceStatus:
    for error_type, status in _STATUS_BY_ERROR:
        if isinstance(error, error_type):
            return status
    return SourceStatus.FAILED


@dataclass(frozen=True)
class SourceOutcome:
    """One source's record for the run, shaped for the run summary (task 11.3).

    ``attempted`` counts every provider attempt, retries included; ``succeeded`` and
    ``failed`` count calls; ``skipped`` counts calls not made because the source was
    halted. ``status`` is OK, or the class of the most recent failure, and ``error``
    is that failure's message (names and numbers only, never a payload).
    """

    source_name: str
    status: SourceStatus
    attempted: int
    succeeded: int
    failed: int
    skipped: int
    retries: int
    error: str | None


@dataclass(frozen=True)
class Attempt[T]:
    """One ledger call: ``ok`` with its value, or not ``ok`` (failed or skipped; the
    reason is on the ledger's outcome)."""

    ok: bool
    value: T | None = None


class SourceCallLedger:
    """The calls made to one source in one run, and what became of them."""

    def __init__(self, source_name: str, *, retry: RetryPolicy | None) -> None:
        self._source_name = source_name
        self._retry = retry
        self._status = SourceStatus.OK
        self._error: str | None = None
        self._attempted = 0
        self._succeeded = 0
        self._failed = 0
        self._skipped = 0
        self._retries = 0
        self._in_flight: RetryStats | None = None

    async def call[T](self, operation: Callable[[], Awaitable[T]]) -> Attempt[T]:
        """Run ``operation`` through the retry policy unless the source is halted.

        A ``SourceError`` is recorded and returned as a not-ok ``Attempt``. Any other
        exception, and every ``BaseException``, propagates untouched.
        """
        if self._status in _HALTING:
            self._skipped += 1
            return Attempt(ok=False)
        stats = RetryStats()
        self._in_flight = stats
        try:
            if self._retry is None:
                stats.attempts = 1
                value = await operation()
            else:
                value = await self._retry.run(operation, stats=stats)
        except SourceError as error:
            self._in_flight = None
            self._attempted += stats.attempts
            self._retries += stats.retries
            self._failed += 1
            self._status = _status_of(error)
            self._error = _outcome_message(error)
            return Attempt(ok=False)
        self._in_flight = None
        self._attempted += stats.attempts
        self._retries += stats.retries
        self._succeeded += 1
        return Attempt(ok=True, value=value)

    def time_out(self, run_timeout_s: float) -> None:
        """Record that the run's wall clock ran out on this source (6.6).

        A call in flight was cancelled: it counts as one failed call with the attempts
        made so far. Otherwise the source was never reached and nothing is counted.
        A halted source keeps its halting status: it was not going to be called anyway.
        """
        if self._status in _HALTING:
            return
        self._status = SourceStatus.TIMED_OUT
        limit = f"run timeout of {run_timeout_s:g}s exceeded"
        if self._in_flight is None:
            self._error = f"[{self._source_name}] {limit}; not started"
            return
        self._attempted += self._in_flight.attempts
        self._retries += self._in_flight.retries
        self._failed += 1
        self._in_flight = None
        self._error = f"[{self._source_name}] {limit}; cancelled in flight"

    def outcome(self) -> SourceOutcome:
        return SourceOutcome(
            self._source_name,
            self._status,
            self._attempted,
            self._succeeded,
            self._failed,
            self._skipped,
            self._retries,
            self._error,
        )


@dataclass(frozen=True)
class _Fetched:
    """One successful call: the batch, its contributions and what the adapter
    reported of the batch (``None``: not reported)."""

    batch: RawBatch
    contributions: tuple[LeadContribution, ...]
    records_fetched: int | None
    credits_consumed: int | None


class Phase(StrEnum):
    DISCOVERY = "discovery"
    ENRICHMENT = "enrichment"


@dataclass(frozen=True)
class SourceResult:
    """What one source produced in a run, with the mode it ran in and why.

    ``batch`` and ``contributions`` are ``None`` when the source failed or was
    skipped; ``outcome`` says why. ``phase`` is the phase this result belongs to.
    """

    source_name: str
    resolved_mode: DataMode
    mode_reason: str
    batch: RawBatch | None
    contributions: tuple[LeadContribution, ...] | None
    outcome: SourceOutcome
    phase: Phase
    # The source's throttle counters as of this result; ``None`` for a synthetic source,
    # which has no pacing (18.2).
    throttle: ThrottleSnapshot | None = None
    # Requests left per window as the provider itself last stated them (12.5); ``None``
    # when the adapter reports none. Never the local limiter's own tokens.
    allowances: Mapping[str, int] | None = None
    # What the adapter reported of this result's batch (``records_fetched`` and
    # ``credits_spent``); ``None`` when it reports none or there is no batch.
    records_fetched: int | None = None
    credits_consumed: int | None = None


def enrichment_work_list(
    results: tuple[SourceResult, ...],
) -> tuple[LeadContribution, ...]:
    """Every contribution Discovery produced, in result order (Requirement 6.9).

    Mechanical: it reads only ``phase`` and ``contributions``, so no score, field value
    or qualification judgment can enter it. A failed or skipped source has no
    contributions and adds none.
    """
    return tuple(
        contribution
        for result in results
        if result.phase is Phase.DISCOVERY and result.contributions is not None
        for contribution in result.contributions
    )


def prune_flagged(
    work_list: tuple[LeadContribution, ...],
    reports: tuple[LeadContribution, ...] = (),
    *,
    exclusions: IdentityExclusions | None = None,
) -> tuple[LeadContribution, ...]:
    """The work list without any lead marked suppressed or opted out (6.10).

    A contribution is flagged when it carries a flag itself, or shares an ``email`` or
    ``linkedin_url`` with a flagged contribution in ``reports`` or in the list. A
    work-list contribution is dropped when it is flagged or strongly linked to a
    flagged one, transitively (user decision 2026-10-06): the strong links are the
    LinkedIn URL and verified-email Match Keys, never name+domain and never an address
    8.14 finds shared, nor a value ``exclusions`` bars (8.13). Order is kept; a report
    naming no known lead removes nothing.
    """
    everything = (*work_list, *reports)
    blocked = blocked_identities(everything)
    person = _strong_person_labels(everything, exclusions)
    flagged = {
        person[index]
        for index, c in enumerate(everything)
        if flags_set(c) or not blocked.keys().isdisjoint(identities(c))
    }
    return tuple(c for index, c in enumerate(work_list) if person[index] not in flagged)


_STRONG_KINDS = frozenset({MatchKeyKind.LINKEDIN_URL, MatchKeyKind.VERIFIED_EMAIL})


def _strong_person_labels(
    contributions: tuple[LeadContribution, ...],
    exclusions: IdentityExclusions | None = None,
) -> list[int]:
    """Each record's person under the strong Match Keys alone, transitively.

    Keys come from ``extract_match_keys`` with 8.14's shared-address pass over the same
    set, so a role address (or one seen with two LinkedIn URLs) links no one; with it,
    no label holds two LinkedIn identities. A record the key reading refuses (a
    non-text identity value) links no one: pruning never raises (``compliance``).
    """
    readable = [_keys_readable(c) for c in contributions]
    shared = DisqualifiedAddresses.from_contributions(
        c for c, ok in zip(contributions, readable, strict=True) if ok
    )
    return _components(
        [
            [
                key
                for key in extract_match_keys(c, exclusions, shared).keys
                if key.kind in _STRONG_KINDS
            ]
            if ok
            else []
            for c, ok in zip(contributions, readable, strict=True)
        ]
    )


def _components(held: Sequence[Iterable[Hashable]]) -> list[int]:
    """Each item's component label: items holding a common key are one, transitively.

    One union per key held (clustering's union-find), so near-linear and order-free.
    """
    forest = _UnionFind(len(held))
    holder: dict[Hashable, int] = {}
    for index, keys in enumerate(held):
        for key in keys:
            forest.union(holder.setdefault(key, index), index)
    return [forest.find(index) for index in range(len(held))]


def _keys_readable(contribution: LeadContribution) -> bool:
    try:
        DisqualifiedAddresses.from_contributions((contribution,))
        extract_match_keys(contribution)
    except TypeError:
        return False
    return True


_COMPANY_DOMAIN_PATH = "company.domain"


def _tier_work_list(
    tier: list[BaseLeadSource], work_list: tuple[LeadContribution, ...]
) -> tuple[LeadContribution, ...]:
    """What a tier is handed; a tier shares one charge unit (part of its key)."""
    if tier[0].charge_unit is ChargeUnit.PER_COMPANY:
        return per_company_work_list(work_list)
    return work_list


def _identities_of(work_list: tuple[LeadContribution, ...]) -> frozenset[Identity]:
    return frozenset(identity for c in work_list for identity in identities(c))


def per_company_work_list(
    work_list: tuple[LeadContribution, ...],
) -> tuple[LeadContribution, ...]:
    """The work list with one Lead per distinct company (Requirement 6.11).

    Companies are clustered on their registrable-domain sets (``companies``, task 16.9):
    Leads whose sets overlap, transitively, are one company. Keeps the first record of
    each company, in work-list order. A record naming no usable company is dropped when
    another record of its person (a shared address or LinkedIn identity) names one
    (ADR-0006: a fed record is that person, not a new Lead); otherwise the first such
    record of each person is kept. A person's records never fuse two companies: one
    person at a former and a current employer leaves both companies worked.
    Pure: the input is a tuple and the kept items are the same objects.
    """
    person = _person_labels(work_list)
    sets = [company_domains(c.values.get(_COMPANY_DOMAIN_PATH)) for c in work_list]
    labels = domain_components(sets)
    placed = {person[index] for index, domains in enumerate(sets) if domains}
    seen: set[tuple[str, int]] = set()
    kept: list[LeadContribution] = []
    for index, c in enumerate(work_list):
        if sets[index]:
            key = ("company", labels[index])
        elif person[index] in placed:
            continue  # the person is worked through a record naming their company
        else:
            key = ("person", person[index])
        if key not in seen:
            seen.add(key)
            kept.append(c)
    return tuple(kept)


def _person_labels(work_list: tuple[LeadContribution, ...]) -> list[int]:
    """Each record's person: its set of records sharing an address or LinkedIn
    identity, transitively (ADR-0006: a fed record is that person, not a new Lead)."""
    return _components([identities(c) for c in work_list])


class IngestionOrchestrator:
    def __init__(
        self,
        registry: SourceRegistry,
        *,
        resolve_mode: ModeResolverWithReason,
        build_source: AdapterFactory,
        max_concurrent_sources: int,
        run_timeout_s: float,
        retry_policy: RetryPolicy | None = None,
        run_recorder: RunRecorder | None = None,
        live_rate_limits: Mapping[str, Mapping[str, RateBucket]] | None = None,
        identity_exclusions: IdentityExclusions | None = None,
    ) -> None:
        if (
            not isinstance(max_concurrent_sources, int)
            or isinstance(max_concurrent_sources, bool)
            or max_concurrent_sources < 1
        ):
            raise ValueError("max_concurrent_sources must be an integer >= 1")
        try:
            valid = (
                isinstance(run_timeout_s, int | float)
                and not isinstance(run_timeout_s, bool)
                and 0 < float(run_timeout_s) < math.inf  # False for NaN
            )
        except OverflowError:  # an int too large for a float
            valid = False
        if not valid:
            raise ValueError("run_timeout_s must be a positive finite number")
        self._registry = registry
        self._resolve_mode = resolve_mode
        self._build_source = build_source
        self._max_concurrent_sources = max_concurrent_sources
        self._run_timeout_s = run_timeout_s
        self._retry_policy = retry_policy
        self._run_recorder = run_recorder
        self._live_rate_limits = dict(live_rate_limits or {})
        self._identity_exclusions = identity_exclusions

    async def run(self, request: SourceRequest) -> tuple[SourceResult, ...]:
        """Fetch from every enabled source, at most the bound in flight at once.

        Results follow the registry's active order, not completion order. When the
        run timeout expires, unfinished sources are recorded ``TIMED_OUT``.

        With a ``run_recorder`` the run record is persisted once the modes are
        resolved and before any source is built or called (Requirement 21.1), and
        finished with the mapped exit code. Any exception, ``CancelledError`` included,
        marks it aborted and still propagates. A failing record write is never
        swallowed: a failed start stops the run, a failed finish raises, and a failed
        abort marker is attached to the propagating exception as a note.
        """
        resolutions = {
            name: self._resolve_mode(
                self._registry.source_class(name), self._registry.settings(name)
            )
            for name in self._registry.enabled_names()
        }
        recorder = self._run_recorder
        if recorder is None:
            return await self._execute(request, resolutions)
        run_id = await recorder.start(
            resolutions,
            {name: self._registry.settings(name) for name in resolutions},
            pool_size=self._max_concurrent_sources,
            run_timeout_s=self._run_timeout_s,
            live_access={
                name: (
                    self._registry.settings(name).live_access
                    or self._registry.source_class(name).live_access
                )
                for name in resolutions
            },
        )
        try:
            results = await self._execute(request, resolutions)
        except BaseException as exc:
            # Mark the record aborted, then let the exception go on unchanged.
            try:
                await recorder.finish(run_id, None)
            except Exception as write_error:  # noqa: BLE001 - noted and logged, the original wins
                _log.error(
                    "run_record_abort_failed",
                    run_id=str(run_id),
                    error=type(write_error).__name__,
                )
                exc.add_note(
                    f"run record {run_id} could not be marked aborted: "
                    f"{type(write_error).__name__}"
                )
            raise
        await recorder.finish(run_id, results)
        return results

    async def _execute(
        self, request: SourceRequest, resolutions: Mapping[str, ModeResolution]
    ) -> tuple[SourceResult, ...]:
        pacings: dict[str, SourcePacing | None] = {}

        def build(source_class: type[BaseLeadSource]) -> BaseLeadSource:
            resolution = resolutions[source_class.name]
            # Plan-dependent limits were read by the caller, for live sources only
            # (7.5); a live source handed none is paced on its declaration.
            rate_limit = (
                self._live_rate_limits.get(source_class.name, source_class.rate_limit)
                if resolution.mode is DataMode.LIVE
                else source_class.rate_limit
            )
            pacing = build_pacing(
                source_class.name, rate_limit, resolution.mode, self._retry_policy
            )
            pacings[source_class.name] = pacing
            return self._build_source(source_class, resolution.mode, pacing)

        sources = self._registry.active(build)
        slots = asyncio.Semaphore(self._max_concurrent_sources)

        ledgers = {}
        for source in sources:
            pacing = pacings[source.name]
            ledgers[source.name] = SourceCallLedger(
                source.name, retry=None if pacing is None else pacing.retry
            )
        finished: dict[tuple[str, Phase], SourceResult] = {}

        def result_of(
            source: BaseLeadSource,
            phase: Phase,
            batch: RawBatch | None,
            contributions: tuple[LeadContribution, ...] | None,
        ) -> SourceResult:
            resolution = resolutions[source.name]
            pacing = pacings[source.name]
            return SourceResult(
                source.name,
                resolution.mode,
                resolution.reason,
                batch,
                contributions,
                ledgers[source.name].outcome(),
                phase,
                throttle=None if pacing is None else pacing.throttle.snapshot(),
                allowances=(
                    dict(source.allowances)
                    if isinstance(source, ReportsAllowances)
                    else None
                ),
            )

        async def run_one(
            source: BaseLeadSource,
            phase: Phase,
            phase_request: SourceRequest,
            sink: dict[tuple[str, Phase], SourceResult],
        ) -> None:
            ledger = ledgers[source.name]

            async def fetch_and_normalize() -> _Fetched:
                batch = await source.fetch_raw(phase_request)
                contributions = tuple(source.normalize_checked(batch))
                # Inside the call, so a malformed batch the report hooks refuse is
                # this source's recorded failure, like a normalization error.
                return _Fetched(
                    batch,
                    contributions,
                    source.records_fetched(batch),
                    source.credits_spent(batch),
                )

            async with slots:
                attempt = await ledger.call(fetch_and_normalize)
            fetched = attempt.value
            sink[(source.name, phase)] = (
                result_of(source, phase, None, None)
                if fetched is None
                else dataclasses.replace(
                    result_of(source, phase, fetched.batch, fetched.contributions),
                    records_fetched=fetched.records_fetched,
                    credits_consumed=fetched.credits_consumed,
                )
            )

        async def run_phase(
            members: list[BaseLeadSource],
            phase: Phase,
            phase_request: SourceRequest,
            sink: dict[tuple[str, Phase], SourceResult] = finished,
        ) -> None:
            async with asyncio.TaskGroup() as group:
                tasks = [
                    group.create_task(run_one(source, phase, phase_request, sink))
                    for source in members
                ]
            for task in tasks:
                task.result()  # a source's own CancelledError still propagates

        discovery = [s for s in sources if Capability.SEARCH in s.capabilities]
        enrichment = [s for s in sources if Capability.ENRICH in s.capabilities]
        timed_out = False
        # The second, free pass (user decision 2026-10-06): each free tier with the
        # identities its first call was handed, and the results of its second call.
        free_tiers: list[tuple[list[BaseLeadSource], frozenset[Identity]]] = []
        second_pass: list[BaseLeadSource] = []
        second: dict[tuple[str, Phase], SourceResult] = {}
        try:
            async with asyncio.timeout(self._run_timeout_s) as deadline:
                await run_phase(discovery, Phase.DISCOVERY, request)
                work_list = prune_flagged(
                    enrichment_work_list(
                        tuple(
                            finished[(s.name, Phase.DISCOVERY)]
                            for s in discovery
                            if (s.name, Phase.DISCOVERY) in finished
                        )
                    ),
                    exclusions=self._identity_exclusions,
                )
                # Every report so far, so a person pruned by an earlier tier is pruned
                # again when a later tier's record re-adds them (ADR-0006).
                reports: tuple[LeadContribution, ...] = ()
                for tier in enrichment_tiers(enrichment):
                    if not work_list:
                        break
                    tier_list = _tier_work_list(tier, work_list)
                    await run_phase(
                        tier,
                        Phase.ENRICHMENT,
                        EnrichmentRequest(kind="enrich", work_list=tier_list),
                    )
                    # The tier's findings feed every later tier (ADR-0006): one forward
                    # pass, so nothing is fed back and no tier is called again.
                    added = tuple(
                        contribution
                        for s in tier
                        for contribution in (
                            finished[(s.name, Phase.ENRICHMENT)].contributions or ()
                        )
                    )
                    reports = (*reports, *added)
                    work_list = prune_flagged(
                        (*work_list, *added),
                        reports,
                        exclusions=self._identity_exclusions,
                    )
                    if tier[0].cost_class is CostClass.FREE:
                        # What it was handed or answered itself is not new to it.
                        free_tiers.append((tier, _identities_of((*tier_list, *added))))
                # Then each free tier once more, only for what it was never handed: it
                # costs nothing and no paid tier runs after it, so no spend moves.
                for tier, seen in free_tiers:
                    fresh = tuple(c for c in work_list if not identities(c) <= seen)
                    if not fresh:
                        continue
                    second_pass.extend(tier)
                    await run_phase(
                        tier,
                        Phase.ENRICHMENT,
                        EnrichmentRequest(
                            kind="enrich", work_list=_tier_work_list(tier, fresh)
                        ),
                        second,
                    )
        except TimeoutError:
            # Only the deadline's own expiry is a record; any other TimeoutError
            # (there is no such path today) must not be mistaken for it.
            if not deadline.expired():
                raise
            timed_out = True
        results = []
        phases = ((Phase.DISCOVERY, discovery), (Phase.ENRICHMENT, enrichment))
        for phase, members in phases:
            for source in members:
                key = (source.name, phase)
                if key not in finished:
                    if phase is Phase.ENRICHMENT and not timed_out:
                        continue  # empty work list: Enrichment is a no-op
                    ledgers[source.name].time_out(self._run_timeout_s)
                    finished[key] = result_of(source, phase, None, None)
                results.append(finished[key])
        for source in second_pass:
            key = (source.name, Phase.ENRICHMENT)
            if key not in second:  # cut short by the deadline
                ledgers[source.name].time_out(self._run_timeout_s)
                second[key] = result_of(source, Phase.ENRICHMENT, None, None)
            results.append(second[key])
        return tuple(results)
