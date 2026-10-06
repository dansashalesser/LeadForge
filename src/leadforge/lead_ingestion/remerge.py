"""Merge a run's contributions with the stored log (follow-up, option A, 2026-10-06).

The canonical Lead is a projection of the whole contribution log (8.12), so a run
merges what it fetched with everything already stored, and ``store.merged_leads.
persist_merge`` saves the result through its one path. This module builds that input:

* ``identify`` is the identity partition: ``clustering.cluster_contributions`` (with
  the Identity Exclusions), then each KEYLESS record joined by its source's own
  record id (see below).
* ``merge_contributions`` is the pure merge: identify, then project each cluster with
  the Source Trust Ranks and the compliance reports of every contribution, reading
  stored primary-domain tie answers.
* ``reproject`` reads the stored log (``load_lead_contributions``), adds each new
  contribution whose identity (``contribution_sha``) is not stored yet (a stored
  observation wins, so a re-fetch adds nothing), identifies the whole log and
  projects ONLY the clusters that need it (incremental re-projection, follow-up
  2026-10-06), asking for the answer to each exact primary-domain tie a projection
  flagged (8.18) through ``tie_resolution.resolve_primary_domain`` (stored answer
  first, never a model in synthetic mode). ``project_with_store`` is its merge.

Incremental re-projection (provisional decisions):

* A cluster is re-projected when its members are not exactly one active stored
  Lead's contributions (a new or not yet merged contribution, a split, a join), or
  when that Lead's canonical row was projected under another version than
  ``projection_version``. Any other cluster's projection cannot have changed: the
  projection is a pure function of the members, the ranks, the compliance reports
  and the stored tie answers.
* So every cluster is re-projected (``full_reason``, recorded on the run) when one of
  the other inputs changed: the caller passes a reason when the projection basis
  (Identity Exclusions, Source Trust Ranks, rules revision) changed; the compliance
  reports of the whole log differing from those of the merged part gives
  ``compliance reports changed``; a tie answer stored during this re-projection gives
  ``tie answer stored`` (another Lead may hold the same tie).
* The partition is still computed over the whole log (union-find is global: a new
  record can bridge two stored Leads); only projection, writes and logs are bounded
  by what changed.

Keyless records (provisional decision): a record with no Match Key (none present and
none barred) used to be a singleton keyed by its whole content, so a re-fetch that
differed became a second Lead. When it carries its provider's own record id
(``person.provider_id``) it joins the one cluster that holds a record of the same
source with that id, or else the other keyless records of that id. Keyed clusters are
never joined through it (Match Keys stay the identity authority, so no rule of
``clustering`` is bypassed), and an id held by two keyed clusters is ambiguous: the
keyless record then stays alone. A keyless record with no provider id stays a
singleton (recorded gap: a source that does not emit its record id).

Other provisional decisions:

* A cluster is in live mode when any of its contributions was fetched live; only then
  may a resolver be built. No resolver is configured by default (``None``: the tie
  stays flagged ``no_resolver_provisional``).
* Compliance reports (``blocked_identities``) are read over the whole log, so an
  opt-out reported by an earlier run still reaches a Lead merged now.
"""

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import (
    IdentityCluster,
    canonical_json,
    cluster_contributions,
)
from leadforge.lead_ingestion.compliance import blocked_identities
from leadforge.lead_ingestion.match_keys import (
    DisqualifiedAddresses,
    IdentityExclusions,
    extract_match_keys,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.projection import ProjectionResult, project_lead
from leadforge.lead_ingestion.store.contributions import (
    contribution_sha,
    load_lead_contributions,
)
from leadforge.lead_ingestion.store.merged_leads import (
    current_leads,
    stale_projections,
)
from leadforge.lead_ingestion.store.models import PrimaryDomainTieResolution
from leadforge.lead_ingestion.store.tie_resolutions import TieResolutionRepository
from leadforge.lead_ingestion.tie_resolution import (
    TieResolutionReader,
    TieResolver,
    resolve_primary_domain,
)

__all__ = [
    "PROVIDER_ID_PATH",
    "Merged",
    "Reprojection",
    "identify",
    "merge_contributions",
    "project_with_store",
    "reproject",
]

PROVIDER_ID_PATH = "person.provider_id"

type Merged = tuple[tuple[IdentityCluster, ProjectionResult], ...]


@dataclass(frozen=True)
class Reprojection:
    """The clusters a merge re-projected, and why all of them when it was full."""

    merged: Merged
    full_reason: str | None  # None: incremental
    clusters: int  # clusters in the whole log

    @property
    def label(self) -> str:
        """``incremental`` or ``full: <reason>`` (the run record's wording)."""
        return (
            "incremental" if self.full_reason is None else f"full: {self.full_reason}"
        )


def identify(
    contributions: Iterable[LeadContribution], exclusions: IdentityExclusions | None
) -> tuple[IdentityCluster, ...]:
    """Cluster by Match Key, then join keyless records by source + provider id."""
    items = tuple(contributions)
    clusters = cluster_contributions(items, exclusions)
    shared = DisqualifiedAddresses.from_contributions(items)

    def keyless(cluster: IdentityCluster) -> bool:
        if len(cluster.contributions) != 1:
            return False
        keys = extract_match_keys(cluster.contributions[0], exclusions, shared)
        return not keys.keys and not keys.barred_kinds

    holders: dict[tuple[str, str], set[int]] = defaultdict(set)
    loose: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, cluster in enumerate(clusters):
        is_loose = keyless(cluster)
        for contribution in cluster.contributions:
            record = _provider_record(contribution)
            if record is None:
                continue
            if is_loose:
                loose[record].append(index)
            else:
                holders[record].add(index)
    anchor: dict[int, int] = {}
    for record, group in loose.items():
        held = holders.get(record, set())
        if len(held) > 1:
            continue  # ambiguous: two keyed clusters claim the record
        target = next(iter(held)) if held else min(group)
        for index in group:
            anchor[index] = target
    if not anchor:
        return clusters
    joined: dict[int, list[LeadContribution]] = defaultdict(list)
    for index, cluster in enumerate(clusters):
        joined[anchor.get(index, index)].extend(cluster.contributions)
    out = [
        replace(
            clusters[index],
            contributions=tuple(sorted(members, key=canonical_json)),
        )
        for index, members in joined.items()
    ]
    return tuple(sorted(out, key=lambda c: c.cluster_id))


def _provider_record(contribution: LeadContribution) -> tuple[str, str] | None:
    value = contribution.values.get(PROVIDER_ID_PATH)
    if isinstance(value, str) and value.strip():
        return contribution.source_name, value.strip()
    return None


def merge_contributions(
    contributions: Iterable[LeadContribution],
    *,
    exclusions: IdentityExclusions | None,
    trust_ranks: Mapping[str, int],
    ties: TieResolutionReader | None = None,
) -> Merged:
    """Identify and project ``contributions``; pure apart from reading ``ties``."""
    items = tuple(contributions)
    project = _projector(items, trust_ranks, ties)
    return tuple((c, project(c)) for c in identify(items, exclusions))


def reproject(
    session: Session,
    contributions: Iterable[LeadContribution],
    *,
    exclusions: IdentityExclusions | None,
    trust_ranks: Mapping[str, int],
    now: datetime,
    tie_resolver: Callable[[], TieResolver] | None = None,
    projection_version: int | None = None,
    full_reason: str | None = None,
) -> Reprojection:
    """The clusters of the stored log plus ``contributions`` that need projecting."""
    stored = load_lead_contributions(session)
    sha_of = {row_id: contribution_sha(c) for row_id, c in stored.items()}
    log: dict[str, LeadContribution] = {}
    for row_id, contribution in stored.items():
        log.setdefault(sha_of[row_id], contribution)
    for contribution in contributions:
        log.setdefault(contribution_sha(contribution), contribution)
    items = tuple(log.values())
    current, stale = _stored_leads(session, sha_of, projection_version)
    merged_part = set().union(*current) if current else set()
    if full_reason is None and blocked_identities(items) != blocked_identities(
        c for sha, c in log.items() if sha in merged_part
    ):
        full_reason = "compliance reports changed"

    clusters = identify(items, exclusions)
    store = TieResolutionRepository(session)
    project = _projector(items, trust_ranks, store)

    def run(selected: Sequence[IdentityCluster]) -> Merged:
        return tuple(
            (c, _with_tie(c, project, store, tie_resolver, now)) for c in selected
        )

    if full_reason is not None:
        return Reprojection(run(clusters), full_reason, len(clusters))
    answers = _tie_answers(session)
    merged = run(
        [
            c
            for c in clusters
            if (members := frozenset(map(contribution_sha, c.contributions)))
            not in current
            or members in stale
        ]
    )
    if _tie_answers(session) != answers:
        return Reprojection(run(clusters), "tie answer stored", len(clusters))
    return Reprojection(merged, None, len(clusters))


def project_with_store(
    session: Session,
    contributions: Iterable[LeadContribution],
    *,
    exclusions: IdentityExclusions | None,
    trust_ranks: Mapping[str, int],
    now: datetime,
    tie_resolver: Callable[[], TieResolver] | None = None,
    projection_version: int | None = None,
    full_reason: str | None = None,
) -> Merged:
    """``reproject``'s clusters and projections, ties asked for once."""
    return reproject(
        session,
        contributions,
        exclusions=exclusions,
        trust_ranks=trust_ranks,
        now=now,
        tie_resolver=tie_resolver,
        projection_version=projection_version,
        full_reason=full_reason,
    ).merged


def _stored_leads(
    session: Session, sha_of: Mapping[uuid.UUID, str], version: int | None
) -> tuple[set[frozenset[str]], set[frozenset[str]]]:
    """Each active Lead's contribution identities; and those projected under
    another version than ``version`` (none when ``version`` is ``None``)."""
    members: dict[uuid.UUID, set[str]] = defaultdict(set)
    for contribution_id, lead in current_leads(session).items():
        members[lead].add(sha_of[contribution_id])
    stale = (
        set()
        if version is None
        else {
            frozenset(members[lead])
            for lead in stale_projections(session, current_version=version)
            if lead in members
        }
    )
    return {frozenset(m) for m in members.values()}, stale


def _tie_answers(session: Session) -> int:
    return session.scalar(sa.select(sa.func.count(PrimaryDomainTieResolution.id))) or 0


def _with_tie(
    cluster: IdentityCluster,
    project: Callable[[IdentityCluster], ProjectionResult],
    store: TieResolutionRepository,
    tie_resolver: Callable[[], TieResolver] | None,
    now: datetime,
) -> ProjectionResult:
    result = project(cluster)
    tie = result.primary_domain_tie
    if tie is None or not result.primary_domain_flagged:
        return result
    outcome = resolve_primary_domain(
        tie.company,
        tie.election,
        mode=_mode(cluster),
        store=store,
        resolver_factory=tie_resolver,
        now=now,
    )
    if outcome.flagged:
        return replace(result, primary_domain_source=outcome.source)
    return project(cluster)  # reads the answer just stored


def _projector(
    items: tuple[LeadContribution, ...],
    trust_ranks: Mapping[str, int],
    ties: TieResolutionReader | None,
) -> Callable[[IdentityCluster], ProjectionResult]:
    blocked = blocked_identities(items)

    def project(cluster: IdentityCluster) -> ProjectionResult:
        return project_lead(cluster, trust_ranks, blocked=blocked, tie_resolutions=ties)

    return project


def _mode(cluster: IdentityCluster) -> DataMode:
    live = any(
        p.data_mode is DataMode.LIVE
        for c in cluster.contributions
        for p in c.provenance
    )
    return DataMode.LIVE if live else DataMode.SYNTHETIC
