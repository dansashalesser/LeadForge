"""Over-merge detector (task 16.8; Requirement 8.15): flags, never blocks or repairs.

A pure function from clusters to ``SuspectCluster`` reports. It reads clusters and
changes nothing: the run produces exactly what it would without it, and no unmerge
exists (8.12; an Identity Exclusion is the only repair, 8.13). Provisional decisions
(choices.md, 16.8):

* The one signal is the requirement's: two or more distinct non-null full names that do
  not match under normalisation. Names compare as the name key and 8.14 do
  (``normalized_person_name``: full_name else first+last, NFKC, casefold, whitespace
  collapse; a missing, blank or masked name is not a name). Rejected: signals the
  requirement does not list (two verified emails, two LinkedIn URLs, a key-less bridge).
  A bridged cluster is caught only when its members carry distinct names.
* A report holds the ``cluster_id`` (a pseudonym of personal data: store it, never log
  it), reason codes and counts, and NO personal value: an operator finds the members
  by id in the database to write an Identity Exclusion. Rejected: carrying names, emails
  or URLs on the report (it would travel to logs and error text).
* The log line is counts-only: clusters examined and suspects found, WARNING when any.
* Odd input is not an error: no clusters, empty clusters and nameless members report
  nothing. A detector must never abort a run (8.15), so a member whose name is not text
  (clustering rejects it first, so only a hand-built cluster reaches here) is skipped
  and counted in the log as ``unreadable_names``: neither silent nor echoed.
* Reports are sorted by ``cluster_id``, so neither cluster order nor member order
  matters. One pass, one normalisation per member.
* Surfacing on the run report (18.x) is not built: the returned reports are the seam.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

import structlog

from leadforge.lead_ingestion.clustering import IdentityCluster
from leadforge.lead_ingestion.match_keys import normalized_person_name

__all__ = ["OverMergeReason", "SuspectCluster", "detect_over_merges"]

_log = structlog.get_logger(__name__)


class OverMergeReason(Enum):
    DISTINCT_FULL_NAMES = "distinct_full_names"


@dataclass(frozen=True)
class SuspectCluster:
    """A cluster suspected of holding more than one person; no personal value."""

    cluster_id: str = field(repr=False)
    reasons: tuple[OverMergeReason, ...]
    distinct_name_count: int
    member_count: int


def detect_over_merges(
    clusters: Iterable[IdentityCluster],
) -> tuple[SuspectCluster, ...]:
    """The suspect clusters among ``clusters``, sorted by ``cluster_id``."""
    examined = 0
    unreadable = 0
    suspects: list[SuspectCluster] = []
    for cluster in clusters:
        examined += 1
        names: set[str] = set()
        for member in cluster.contributions:
            try:
                name = normalized_person_name(member.values)
            except TypeError:  # non-text name: count it, never abort the run
                unreadable += 1
                continue
            if name is not None:
                names.add(name)
        if len(names) >= 2:
            suspects.append(
                SuspectCluster(
                    cluster_id=cluster.cluster_id,
                    reasons=(OverMergeReason.DISTINCT_FULL_NAMES,),
                    distinct_name_count=len(names),
                    member_count=len(cluster.contributions),
                )
            )
    suspects.sort(key=lambda report: report.cluster_id)
    log = _log.warning if suspects else _log.info
    log(
        "over_merge_detection",
        clusters_examined=examined,
        suspect_clusters=len(suspects),
        unreadable_names=unreadable,
    )
    return tuple(suspects)
