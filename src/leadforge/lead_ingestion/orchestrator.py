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
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    LeadContribution,
    RawBatch,
    SourceRequest,
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
from leadforge.lead_ingestion.mode_resolution import ModeResolution
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.pacing import SourcePacing, build_pacing
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.retry import RetryPolicy, RetryStats

__all__ = [
    "AdapterFactory",
    "Attempt",
    "IngestionOrchestrator",
    "ModeResolverWithReason",
    "SourceCallLedger",
    "SourceOutcome",
    "SourceResult",
    "SourceStatus",
]

ModeResolverWithReason = Callable[
    [type[BaseLeadSource], SourceSettings], ModeResolution
]
AdapterFactory = Callable[
    [type[BaseLeadSource], DataMode, SourcePacing | None], BaseLeadSource
]


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
        """
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
class SourceResult:
    """What one source produced in a run, with the mode it ran in and why.

    ``batch`` and ``contributions`` are ``None`` when the source failed or was
    skipped; ``outcome`` says why.
    """

    source_name: str
    resolved_mode: DataMode
    mode_reason: str
    batch: RawBatch | None
    contributions: tuple[LeadContribution, ...] | None
    outcome: SourceOutcome


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

    async def run(self, request: SourceRequest) -> tuple[SourceResult, ...]:
        """Fetch from every enabled source, at most the bound in flight at once.

        Results follow the registry's active order, not completion order. When the
        run timeout expires, unfinished sources are recorded ``TIMED_OUT``.
        """
        resolutions = {
            name: self._resolve_mode(
                self._registry.source_class(name), self._registry.settings(name)
            )
            for name in self._registry.enabled_names()
        }

        pacings: dict[str, SourcePacing | None] = {}

        def build(source_class: type[BaseLeadSource]) -> BaseLeadSource:
            resolution = resolutions[source_class.name]
            pacing = build_pacing(
                source_class.name,
                source_class.rate_limit,
                resolution.mode,
                self._retry_policy,
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
        finished: dict[str, SourceResult] = {}

        def result_of(
            source: BaseLeadSource,
            batch: RawBatch | None,
            contributions: tuple[LeadContribution, ...] | None,
        ) -> SourceResult:
            resolution = resolutions[source.name]
            return SourceResult(
                source.name,
                resolution.mode,
                resolution.reason,
                batch,
                contributions,
                ledgers[source.name].outcome(),
            )

        async def run_one(source: BaseLeadSource) -> None:
            ledger = ledgers[source.name]

            async def fetch_and_normalize() -> tuple[
                RawBatch, tuple[LeadContribution, ...]
            ]:
                batch = await source.fetch_raw(request)
                return batch, tuple(source.normalize_checked(batch))

            async with slots:
                attempt = await ledger.call(fetch_and_normalize)
            fetched = attempt.value
            finished[source.name] = result_of(
                source,
                None if fetched is None else fetched[0],
                None if fetched is None else fetched[1],
            )

        try:
            async with asyncio.timeout(self._run_timeout_s) as deadline:
                async with asyncio.TaskGroup() as group:
                    tasks = [group.create_task(run_one(source)) for source in sources]
            for task in tasks:
                task.result()  # a source's own CancelledError still propagates
        except TimeoutError:
            # Only the deadline's own expiry is a record; any other TimeoutError
            # (there is no such path today) must not be mistaken for it.
            if not deadline.expired():
                raise
        results = []
        for source in sources:
            if source.name not in finished:
                ledgers[source.name].time_out(self._run_timeout_s)
                finished[source.name] = result_of(source, None, None)
            results.append(finished[source.name])
        return tuple(results)
