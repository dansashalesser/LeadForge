"""Per-source pacing wiring (task 10.3, Requirement 7.5).

A live source gets a ``SourceThrottle`` (one ``CompositeTokenBucket`` per declared
bucket) and a ``RetryPolicy``. A synthetic source gets neither: ``build_pacing``
constructs nothing and returns ``None``, so the orchestrator holds no bucket to await
and no policy to run, and a synthetic run has zero throttle delay and zero retries
by absence. Callers hold ``SourcePacing | None`` and must not substitute a no-op.

Provisional decisions (see choices.md, task 10.3):

* The wiring is a free function taking the source's name and declared ``rate_limit``,
  not a method on an adapter, so adapters stay free of mode logic.
* The retry policy uses ``RetryPolicy`` defaults: no retry configuration exists yet.
* A mode that is neither live nor synthetic raises ``ValueError``; it is never
  treated as live.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from leadforge.lead_ingestion.base_source import RateBucket
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.throttle import SourceThrottle

__all__ = ["SourcePacing", "build_pacing"]


@dataclass(frozen=True)
class SourcePacing:
    """The throttle and retry policy of one live source for one run."""

    throttle: SourceThrottle
    retry: RetryPolicy


def build_pacing(
    source_name: str,
    rate_limit: Mapping[str, RateBucket],
    mode: DataMode,
    retry: RetryPolicy | None = None,
) -> SourcePacing | None:
    """Pacing for a live source; ``None`` (nothing constructed) for synthetic.

    ``retry`` replaces the default policy when the caller holds a configured one.
    """
    if mode is DataMode.SYNTHETIC:
        return None
    if mode is not DataMode.LIVE:
        raise ValueError(f"unknown data mode {mode!r}")
    return SourcePacing(
        throttle=SourceThrottle(source_name, rate_limit),
        retry=retry if retry is not None else RetryPolicy(),
    )
