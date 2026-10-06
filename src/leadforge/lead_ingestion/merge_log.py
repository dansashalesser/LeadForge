"""Per-merge log events (task 16.12; Requirement 21.4): kinds, keyed digests, rules.

``merge_log_events`` is a pure function from ``ProjectionResult`` values to
``MergeLogEvent`` records, one per merge (a cluster that combined more than one
contribution); ``log_merges`` is the thin emitter that logs them. The events are derived
from what the result already carries (``match_keys``, ``conflicts``), never assembled by
hand, and nothing here reads or changes the merge result. Provisional decisions
(choices.md, 16.12):

* "The matching key used" is logged as the Match Key KIND (``linkedin_url``,
  ``verified_email``, ``name_domain``) and, per user decision (2026-10-06), as a keyed
  HMAC-SHA256 digest of each key VALUE whose union joined members
  (``match_key_digests``: ``{"kind", "digest"}``, strongest kind first, then by
  digest). The value is an email, a LinkedIn URL or a name plus domain, all personal
  data, so it is never logged; a plain sha256 of it is rejected (a dictionary attack
  reverses it, as it could ``cluster_id``). The digester (``match_key_digest``) is a
  required argument, so there is no unkeyed path. ``match_key`` is the strongest kind
  that linked the cluster and ``match_keys`` all of them: a cluster can be linked
  transitively by several. A key shared by members an earlier, stronger key already
  joined linked nothing and is not listed.
* A resolved conflict is a canonical path with a losing value: its winning SOURCE NAME,
  the number of superseded candidates, and the rule that decided (``ConflictRule``:
  trust_rank, confidence_origin, confidence, recency, tie_break). Agreeing values are
  not conflicts. Values are never logged.
* Content is order-independent (8.8): conflicts are sorted by path, and the projection
  ignores contribution order. Events follow the order of the results given.
* Bounded volume: one line per merge (by design no per-run cap: a run of 100k merges
  logs 100k lines, each bounded in size), at most ``MAX_LOGGED_CONFLICTS`` conflicts
  on it (first by path), with ``conflict_count`` and ``conflicts_omitted`` stating
  the rest; likewise at most ``MAX_LOGGED_MATCH_KEYS`` digests (strongest kind
  first, then by digest) with ``match_key_digests_omitted``.
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

from leadforge.lead_ingestion.match_key_digest import MatchKeyDigester
from leadforge.lead_ingestion.projection import ProjectionResult, ResolvedConflict

__all__ = [
    "MAX_LOGGED_CONFLICTS",
    "MAX_LOGGED_MATCH_KEYS",
    "MergeLogEvent",
    "MergeLogOutcome",
    "log_merges",
    "merge_log_events",
]

MAX_LOGGED_CONFLICTS = 20
MAX_LOGGED_MATCH_KEYS = 20
_EVENT = "lead_merge"

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MergeLogEvent:
    """One merge: kinds, keyed digests, source names, paths, rules and counts.

    No personal value: a key value appears only as its keyed digest.
    """

    match_keys: tuple[str, ...]
    contributions: int
    sources: int
    conflict_count: int
    conflicts: tuple[ResolvedConflict, ...]  # capped, sorted by path
    # (kind, keyed digest), strongest kind first then by digest; capped.
    match_key_digests: tuple[tuple[str, str], ...] = ()
    match_key_digest_count: int = 0

    @property
    def match_key_digests_omitted(self) -> int:
        return self.match_key_digest_count - len(self.match_key_digests)

    @property
    def conflicts_omitted(self) -> int:
        return self.conflict_count - len(self.conflicts)

    def log_fields(self) -> dict[str, Any]:
        """The keyword fields of the log line, in plain log-friendly types."""
        return {
            "match_key": self.match_keys[0] if self.match_keys else None,
            "match_keys": list(self.match_keys),
            "match_key_digests": [
                {"kind": kind, "digest": digest}
                for kind, digest in self.match_key_digests
            ],
            "match_key_digests_omitted": self.match_key_digests_omitted,
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
    results: Iterable[ProjectionResult], *, digester: MatchKeyDigester
) -> tuple[MergeLogEvent, ...]:
    """One event per result that combined more than one contribution."""
    return tuple(_event(r, digester) for r in results if r.contribution_count > 1)


def _event(result: ProjectionResult, digester: MatchKeyDigester) -> MergeLogEvent:
    conflicts = sorted(
        result.conflicts,
        key=lambda c: (c.canonical_path, c.winning_source, c.superseded_count),
    )
    digests = [
        (key.kind.name.lower(), digest)
        for key, digest in sorted(
            ((key, digester.digest(key)) for key in set(result.linking_keys)),
            key=lambda pair: (pair[0].kind, pair[1]),
        )
    ]
    return MergeLogEvent(
        match_keys=tuple(kind.name.lower() for kind in sorted(result.match_keys)),
        contributions=result.contribution_count,
        sources=len(result.contributing_sources),
        conflict_count=len(conflicts),
        conflicts=tuple(conflicts[:MAX_LOGGED_CONFLICTS]),
        match_key_digests=tuple(digests[:MAX_LOGGED_MATCH_KEYS]),
        match_key_digest_count=len(digests),
    )


def log_merges(
    results: Iterable[ProjectionResult], *, digester: MatchKeyDigester
) -> MergeLogOutcome:
    """Log one line per merge; a failing logger is counted, never raised."""
    emitted = failed = 0
    for event in merge_log_events(results, digester=digester):
        fields = event.log_fields()  # builder errors are bugs: outside the catch
        try:
            _log.info(_EVENT, **fields)
        except Exception:  # noqa: BLE001 - a log failure must not abort a run
            failed += 1
        else:
            emitted += 1
    return MergeLogOutcome(emitted, failed)
