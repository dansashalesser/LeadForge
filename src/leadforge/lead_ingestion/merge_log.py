"""Per-merge log events (task 16.12; Requirement 21.4): kinds and rules only.

``merge_log_events`` is a pure function from ``ProjectionResult`` values to
``MergeLogEvent`` records, one per merge (a cluster that combined more than one
contribution); ``log_merges`` is the thin emitter that logs them. The events are derived
from what the result already carries (``match_keys``, ``conflicts``), never assembled by
hand, and nothing here reads or changes the merge result. Provisional decisions
(choices.md, 16.12):

* "The matching key used" is logged as the Match Key KIND (``linkedin_url``,
  ``verified_email``, ``name_domain``), never its value. Requirement 21.4 asks for the
  key used and the fields whose conflicts were resolved; the key value is an email, a
  LinkedIn URL or a name plus domain, all personal data, and nothing in the project
  approves a masking or hashing scheme (``log_redaction`` redacts credentials only; a
  salted HMAC does not exist). Rejected: logging the value, a bare sha256 of it (a
  dictionary attack can test it, as with ``cluster_id``), the ``cluster_id`` itself.
  SPEC GAP: an operator cannot join a line to a person; 21.4 as worded is met at kind
  level only. ``match_key`` is the strongest kind that linked the cluster and
  ``match_keys`` all of them: a cluster can be linked transitively by several.
* A resolved conflict is a canonical path with a losing value: its winning SOURCE NAME,
  the number of superseded candidates, and the rule that decided (``ConflictRule``:
  trust_rank, confidence_origin, confidence, recency, tie_break). Agreeing values are
  not conflicts. Values are never logged.
* Content is order-independent (8.8): conflicts are sorted by path, and the projection
  ignores contribution order. Events follow the order of the results given.
* Bounded volume: one line per merge (by design no per-run cap: a run of 100k merges
  logs 100k lines, each bounded in size), at most ``MAX_LOGGED_CONFLICTS`` conflicts
  on it (first by path), with ``conflict_count`` and ``conflicts_omitted`` stating
  the rest.
* A failing logger must not abort a run: the emitter catches ``Exception`` around each
  call (only the logger call, so a bug in building the line still raises; never
  ``BaseException``, so cancellation and interrupts propagate), drops that
  line, and counts it in the returned ``MergeLogOutcome.failed``. The error is not
  echoed (its text could hold personal data) and no second log attempt is made; the
  count is the signal for the run report (18.x, not built).
* Odd input is not an error: no results, results that merged nothing, a merge with no
  recorded kind (``match_key`` is ``None``).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import structlog

from leadforge.lead_ingestion.projection import ProjectionResult, ResolvedConflict

__all__ = [
    "MAX_LOGGED_CONFLICTS",
    "MergeLogEvent",
    "MergeLogOutcome",
    "log_merges",
    "merge_log_events",
]

MAX_LOGGED_CONFLICTS = 20
_EVENT = "lead_merge"

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MergeLogEvent:
    """One merge: kinds, source names, paths, rules and counts; no personal value."""

    match_keys: tuple[str, ...]
    contributions: int
    sources: int
    conflict_count: int
    conflicts: tuple[ResolvedConflict, ...]  # capped, sorted by path

    @property
    def conflicts_omitted(self) -> int:
        return self.conflict_count - len(self.conflicts)

    def log_fields(self) -> dict[str, Any]:
        """The keyword fields of the log line, in plain log-friendly types."""
        return {
            "match_key": self.match_keys[0] if self.match_keys else None,
            "match_keys": list(self.match_keys),
            "contributions": self.contributions,
            "sources": self.sources,
            "conflict_count": self.conflict_count,
            "conflicts_omitted": self.conflicts_omitted,
            "conflicts": [
                {
                    "path": c.canonical_path,
                    "winner": c.winning_source,
                    "superseded": c.superseded_count,
                    "rule": c.decided_by.value,
                }
                for c in self.conflicts
            ],
        }


@dataclass(frozen=True)
class MergeLogOutcome:
    """How many events were logged and how many the logger failed on."""

    emitted: int
    failed: int


def merge_log_events(
    results: Iterable[ProjectionResult],
) -> tuple[MergeLogEvent, ...]:
    """One event per result that combined more than one contribution."""
    return tuple(_event(r) for r in results if r.contribution_count > 1)


def _event(result: ProjectionResult) -> MergeLogEvent:
    conflicts = sorted(
        result.conflicts,
        key=lambda c: (c.canonical_path, c.winning_source, c.superseded_count),
    )
    return MergeLogEvent(
        match_keys=tuple(kind.name.lower() for kind in sorted(result.match_keys)),
        contributions=result.contribution_count,
        sources=len(result.contributing_sources),
        conflict_count=len(conflicts),
        conflicts=tuple(conflicts[:MAX_LOGGED_CONFLICTS]),
    )


def log_merges(results: Iterable[ProjectionResult]) -> MergeLogOutcome:
    """Log one line per merge; a failing logger is counted, never raised."""
    emitted = failed = 0
    for event in merge_log_events(results):
        fields = event.log_fields()  # builder errors are bugs: outside the catch
        try:
            _log.info(_EVENT, **fields)
        except Exception:  # noqa: BLE001 - a log failure must not abort a run
            failed += 1
        else:
            emitted += 1
    return MergeLogOutcome(emitted, failed)
