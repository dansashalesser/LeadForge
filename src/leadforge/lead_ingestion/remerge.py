"""Merge a run's contributions with the stored log (follow-up, option A, 2026-10-06).

The canonical Lead is a projection of the whole contribution log (8.12), so a run
merges what it fetched with everything already stored, and ``store.merged_leads.
persist_merge`` saves the result through its one path. This module builds that input:

* ``merge_contributions`` is the pure merge: cluster (with the Identity Exclusions),
  then project each cluster with the Source Trust Ranks and the compliance reports of
  every contribution, reading stored primary-domain tie answers.
* ``project_with_store`` reads the stored log (``load_lead_contributions``), adds each
  new contribution whose identity (``contribution_sha``) is not stored yet (a stored
  observation wins, so a re-fetch adds nothing), merges, and then asks for the answer
  to each exact primary-domain tie the projection flagged (8.18): through
  ``tie_resolution.resolve_primary_domain``, which reads a stored answer first, never
  calls a model in synthetic mode and persists an accepted answer. A tie it resolves
  is projected again, so the Lead reads the stored answer; one still unresolved keeps
  the fallback, flagged with the reason ``resolve_primary_domain`` gave.

Provisional decisions:

* A cluster is in live mode when any of its contributions was fetched live; only then
  may a resolver be built. No resolver is configured by default (``None``: the tie
  stays flagged ``no_resolver_provisional``).
* Compliance reports (``blocked_identities``) are read over the whole log, so an
  opt-out reported by an earlier run still reaches a Lead merged now.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime

from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import IdentityCluster, cluster_contributions
from leadforge.lead_ingestion.compliance import blocked_identities
from leadforge.lead_ingestion.match_keys import IdentityExclusions
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.projection import ProjectionResult, project_lead
from leadforge.lead_ingestion.store.contributions import (
    contribution_sha,
    load_lead_contributions,
)
from leadforge.lead_ingestion.store.tie_resolutions import TieResolutionRepository
from leadforge.lead_ingestion.tie_resolution import (
    TieResolutionReader,
    TieResolver,
    resolve_primary_domain,
)

__all__ = ["Merged", "merge_contributions", "project_with_store"]

type Merged = tuple[tuple[IdentityCluster, ProjectionResult], ...]


def merge_contributions(
    contributions: Iterable[LeadContribution],
    *,
    exclusions: IdentityExclusions | None,
    trust_ranks: Mapping[str, int],
    ties: TieResolutionReader | None = None,
) -> Merged:
    """Cluster and project ``contributions``; pure apart from reading ``ties``."""
    items = tuple(contributions)
    project = _projector(items, trust_ranks, ties)
    return tuple((c, project(c)) for c in cluster_contributions(items, exclusions))


def project_with_store(
    session: Session,
    contributions: Iterable[LeadContribution],
    *,
    exclusions: IdentityExclusions | None,
    trust_ranks: Mapping[str, int],
    now: datetime,
    tie_resolver: Callable[[], TieResolver] | None = None,
) -> Merged:
    """The merge of the stored log and ``contributions``, ties asked for once."""
    log = {contribution_sha(c): c for c in load_lead_contributions(session).values()}
    for contribution in contributions:
        log.setdefault(contribution_sha(contribution), contribution)
    items = tuple(log.values())
    store = TieResolutionRepository(session)
    project = _projector(items, trust_ranks, store)
    out: list[tuple[IdentityCluster, ProjectionResult]] = []
    for cluster in cluster_contributions(items, exclusions):
        result = project(cluster)
        tie = result.primary_domain_tie
        if tie is not None and result.primary_domain_flagged:
            outcome = resolve_primary_domain(
                tie.company,
                tie.election,
                mode=_mode(cluster),
                store=store,
                resolver_factory=tie_resolver,
                now=now,
            )
            result = (
                replace(result, primary_domain_source=outcome.source)
                if outcome.flagged
                else project(cluster)  # reads the answer just stored
            )
        out.append((cluster, result))
    return tuple(out)


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
