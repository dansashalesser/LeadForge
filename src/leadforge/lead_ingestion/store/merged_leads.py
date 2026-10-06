"""Persist a merge: identities, contributions, the lead mapping, canonical leads.

``persist_merge`` is synchronous and takes the session of a ``StoreWriter.write_batch``
callable; it does not commit. It is the ONE path for a first save and for every
re-merge (follow-up, user option A, 2026-10-06): ``merged`` is the clustering of the
whole contribution log plus the run's new contributions (``remerge.project_with_store``)
and ``batches`` are only what the run fetched. In the caller's one transaction it:

* writes a ``raw_response`` per batch and inserts each contribution whose identity
  (``contributions.contribution_sha``) is not stored yet; a stored one is reused, so
  the same observation is never stored twice (8.12, append-only);
* gives every cluster a stable ``lead_identity`` (``assign_leads``): a cluster keeps the
  lead all its stored contributions belonged to; a join keeps the most senior lead and
  retires the absorbed one; a split retires the lead and every part is a new lead. A
  retired lead gets ``retired_at`` and a ``lead_succession`` row per successor; its
  rows are kept, and it is never active beside its successors;
* rebuilds the derived ``contribution_lead`` mapping, one row per contribution, so a
  split or a join relinks nothing in the append-only log;
* upserts one ``canonical_lead`` per cluster that names a person, keyed by its lead
  identity, with its primary-domain decision and projection stamp, and rewrites its
  ``canonical_field_provenance`` rows (the winning field, its agreeing-source count
  and the superseded losers' field ids).

Provisional decisions (see choices.md, task 20 and the follow-up):

* One transaction for the whole merge: an identity is shared by contributions of
  several sources, so a partial write would leave contributions with no projection.
  The run record is completed by the caller in the same transaction.
* A cluster that names no person keeps an identity and its mapping and gets no
  canonical lead. A lead continues only into a cluster that is the same kind (person
  or not); otherwise it is retired into that cluster.
* A merge must cover every contribution of every lead it touches (else
  ``ValueError``): projecting part of a lead would silently drop the rest.
* Seniority for a join is the identity's ``created_at``, then its id.
* ``lead_scope`` is a leftover NOT NULL column (ADR-0001 removed the concept): it is
  ``person`` when the contribution carries a person path or a bare CRM ``email``, else
  ``company``.
* A raw batch is stored under its phase as ``endpoint_key`` and fingerprinted by its
  canonical JSON; it is written even when every contribution in it was already stored
  (the run did fetch it).
* A provenance record is matched to its stored field by (source, path, raw path,
  fetch time); a CRM source's bare ``email`` is the ``person.email`` the projection
  re-keyed it to. Byte-identical duplicates are interchangeable, so the first is used.
"""

import uuid
from collections import defaultdict
from collections.abc import Callable, Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import IdentityCluster, canonical_value_json
from leadforge.lead_ingestion.models import CanonicalLead, DataMode, FieldProvenance
from leadforge.lead_ingestion.projection import ProjectionResult
from leadforge.lead_ingestion.store.contributions import (
    contribution_sha,
    load_lead_contributions,
    write_contribution,
)
from leadforge.lead_ingestion.store.models import (
    CanonicalFieldProvenance,
    CanonicalLeadRow,
    ContributionField,
    ContributionLead,
    LeadIdentity,
    LeadSuccession,
    SourceContribution,
    SourceRun,
)
from leadforge.lead_ingestion.store.raw_responses import (
    RawResponseRepository,
    RetentionPolicy,
)

__all__ = [
    "LeadAssignment",
    "MergeStored",
    "SourceBatch",
    "assign_leads",
    "persist_merge",
    "stale_projections",
]

_EMAIL = "person.email"
_BARE_EMAIL = "email"

_FieldKey = tuple[str, str, str, datetime]


@dataclass(frozen=True)
class SourceBatch:
    """One source's normalized output for one phase, with its raw payload."""

    source_name: str
    data_mode: DataMode
    endpoint_key: str
    payload: Any
    contributions: tuple[LeadContribution, ...]


@dataclass(frozen=True)
class MergeStored:
    identities: int
    # Contribution rows this merge inserted (a stored observation is not counted).
    contributions: int
    # Active canonical leads written (inserted or updated) by this merge.
    canonical_leads: int
    raw_responses: int
    # Canonical leads on a new identity, and leads this merge retired.
    leads_created: int = 0
    leads_retired: int = 0


@dataclass(frozen=True)
class LeadAssignment[L: Hashable]:
    """Per cluster, the prior lead it keeps (``None``: a new lead); and the retired
    prior leads with the indexes of the clusters that replace them."""

    kept: tuple[L | None, ...]
    retired: dict[L, tuple[int, ...]]


def assign_leads[K: Hashable, L: Hashable](
    clusters: Sequence[Collection[K]],
    prior: Mapping[K, L],
    *,
    seniority: Callable[[L], Any],
    can_continue: Callable[[L, int], bool] = lambda lead, index: True,
) -> LeadAssignment[L]:
    """Which prior lead each cluster continues; pure, and independent of order.

    ``prior`` maps a stored contribution to its current lead. A lead continues into a
    cluster only when that cluster is the one cluster holding its contributions (and
    ``can_continue`` allows it); a cluster that several leads would continue into (a
    join) keeps the most senior one. Every other lead touched is retired, its
    successors being the clusters that now hold its contributions.
    """
    holders: dict[L, set[int]] = defaultdict(set)
    for index, cluster in enumerate(clusters):
        for key in cluster:
            if key in prior:
                holders[prior[key]].add(index)
    kept: list[L | None] = []
    for index in range(len(clusters)):
        continuing = [
            lead
            for lead, where in holders.items()
            if where == {index} and can_continue(lead, index)
        ]
        kept.append(min(continuing, key=seniority) if continuing else None)
    survivors = {lead for lead in kept if lead is not None}
    retired = {
        lead: tuple(sorted(where))
        for lead, where in holders.items()
        if lead not in survivors
    }
    return LeadAssignment(tuple(kept), retired)


def persist_merge(
    session: Session,
    *,
    run_id: uuid.UUID,
    batches: Sequence[SourceBatch],
    merged: Sequence[tuple[IdentityCluster, ProjectionResult]],
    computed_at: datetime,
    projection_version: int,
    retention: RetentionPolicy,
    projection_fingerprint: str | None = None,
) -> MergeStored:
    source_runs = _source_runs(session, run_id)
    for batch in batches:
        if batch.source_name not in source_runs:
            raise LookupError(f"run has no source {batch.source_name!r}")
    shas = [
        {contribution_sha(c) for c in cluster.contributions} for cluster, _ in merged
    ]
    cluster_of = {sha: index for index, group in enumerate(shas) for sha in group}
    for batch in batches:
        for contribution in batch.contributions:
            if contribution_sha(contribution) not in cluster_of:
                raise ValueError(
                    f"a contribution of {batch.source_name!r} is in no cluster"
                )

    rows = _stored_rows_by_sha(session)
    fetched = {contribution_sha(c) for b in batches for c in b.contributions}
    if any(sha not in rows and sha not in fetched for sha in cluster_of):
        raise ValueError("a clustered contribution is neither stored nor in a batch")
    in_scope = [{i for sha in group for i in rows.get(sha, ())} for group in shas]
    prior = _current_leads(session)
    _require_whole_leads(prior, set().union(*in_scope) if in_scope else set())
    touched = {prior[i] for group in in_scope for i in group if i in prior}
    identities = {
        identity.id: identity
        for identity in session.scalars(
            sa.select(LeadIdentity).where(LeadIdentity.id.in_(touched))
        )
    }
    with_lead = set(
        session.scalars(
            sa.select(CanonicalLeadRow.lead_identity_id).where(
                CanonicalLeadRow.lead_identity_id.in_(touched)
            )
        )
    )
    plan = assign_leads(
        in_scope,
        prior,
        seniority=lambda lead: (identities[lead].created_at, str(lead)),
        can_continue=lambda lead, index: (
            (lead in with_lead) == (merged[index][1].lead is not None)
        ),
    )

    lead_ids: list[uuid.UUID] = []
    for (cluster, _), kept in zip(merged, plan.kept, strict=True):
        key_type = cluster.merged_by[0].name.lower() if cluster.merged_by else None
        identity = identities.get(kept) if kept is not None else None
        if identity is None:
            identity = LeadIdentity(created_at=computed_at)
            session.add(identity)
        identity.primary_key_type = key_type
        session.flush()
        lead_ids.append(identity.id)
    for lead, successors in sorted(plan.retired.items(), key=lambda kv: str(kv[0])):
        identities[lead].retired_at = computed_at
        for index in successors:
            session.add(
                LeadSuccession(
                    predecessor_id=lead,
                    successor_id=lead_ids[index],
                    recorded_at=computed_at,
                )
            )
    session.flush()

    inserted = _insert_new(
        session,
        batches,
        rows,
        cluster_of,
        lead_ids,
        source_runs,
        computed_at,
        retention,
    )
    _rebuild_mapping(
        session,
        {
            i: lead_ids[index]
            for index, group in enumerate(shas)
            for sha in group
            for i in rows[sha]
        },
    )

    canonical_leads = created = 0
    for index, (cluster, result) in enumerate(merged):
        if result.lead is None:
            continue
        fields: dict[_FieldKey, uuid.UUID] = {}
        for contribution in cluster.contributions:
            _index_fields(
                session, contribution, rows[contribution_sha(contribution)][0], fields
            )
        _write_canonical(
            session,
            lead_ids[index],
            result,
            result.lead,
            fields,
            computed_at,
            projection_version,
            projection_fingerprint,
        )
        canonical_leads += 1
        created += plan.kept[index] is None
    return MergeStored(
        identities=len(lead_ids),
        contributions=inserted,
        canonical_leads=canonical_leads,
        raw_responses=len(batches),
        leads_created=created,
        leads_retired=sum(1 for lead in plan.retired if lead in with_lead),
    )


def stale_projections(
    session: Session, *, current_version: int
) -> tuple[uuid.UUID, ...]:
    """Active identities whose canonical lead was projected under another version.

    ``current_version`` is ``projection.stamp_projection(...).version``; versions only
    increase, so any other stored version is out of date and must be recomputed (8.13).
    A retired lead is never recomputed. Sorted, so the result ignores row order.
    """
    if current_version < 1:
        raise ValueError("a projection version starts at 1")
    rows = session.scalars(
        sa.select(CanonicalLeadRow.lead_identity_id)
        .join(LeadIdentity, LeadIdentity.id == CanonicalLeadRow.lead_identity_id)
        .where(
            CanonicalLeadRow.projection_version != current_version,
            LeadIdentity.retired_at.is_(None),
        )
    )
    return tuple(sorted(rows, key=str))


def _source_runs(session: Session, run_id: uuid.UUID) -> dict[str, uuid.UUID]:
    rows = session.execute(
        sa.select(SourceRun.source_name, SourceRun.id).where(SourceRun.run_id == run_id)
    )
    return {name: source_run_id for name, source_run_id in rows}


def _stored_rows_by_sha(session: Session) -> dict[str, list[uuid.UUID]]:
    """Stored contribution ids by identity; a row from before 0006 is rebuilt to
    compute its identity (it has no ``content_sha``)."""
    out: dict[str, list[uuid.UUID]] = defaultdict(list)
    legacy = False
    for row_id, sha in session.execute(
        sa.select(SourceContribution.id, SourceContribution.content_sha).order_by(
            SourceContribution.id
        )
    ):
        if sha is None:
            legacy = True
        else:
            out[sha].append(row_id)
    if legacy:
        known = {i for ids in out.values() for i in ids}
        for row_id, contribution in sorted(
            load_lead_contributions(session).items(), key=lambda kv: str(kv[0])
        ):
            if row_id not in known:
                out[contribution_sha(contribution)].append(row_id)
    return out


def _current_leads(session: Session) -> dict[uuid.UUID, uuid.UUID]:
    """Contribution id -> its active lead, from the derived mapping."""
    rows = session.execute(
        sa.select(ContributionLead.contribution_id, ContributionLead.lead_identity_id)
        .join(LeadIdentity, LeadIdentity.id == ContributionLead.lead_identity_id)
        .where(LeadIdentity.retired_at.is_(None))
    )
    return {contribution: lead for contribution, lead in rows}


def _require_whole_leads(
    prior: Mapping[uuid.UUID, uuid.UUID], in_scope: set[uuid.UUID]
) -> None:
    touched = {prior[i] for i in in_scope if i in prior}
    if any(lead in touched and i not in in_scope for i, lead in prior.items()):
        raise ValueError("a merge must cover every contribution of a lead it touches")


def _insert_new(
    session: Session,
    batches: Sequence[SourceBatch],
    rows: dict[str, list[uuid.UUID]],
    cluster_of: Mapping[str, int],
    lead_ids: Sequence[uuid.UUID],
    source_runs: Mapping[str, uuid.UUID],
    computed_at: datetime,
    retention: RetentionPolicy,
) -> int:
    """Raw payloads, and each contribution not stored yet; returns how many."""
    inserted = 0
    for batch in batches:
        fetched = _fetched_at(batch, computed_at)
        raw_id = RawResponseRepository.add(
            session,
            source_run_id=source_runs[batch.source_name],
            endpoint_key=batch.endpoint_key,
            request_fingerprint=sha256(
                canonical_value_json(batch.payload).encode("utf-8")
            ).hexdigest(),
            payload=batch.payload,
            fetched_at=fetched,
            mode=batch.data_mode,
            policy=retention,
        )
        for contribution in batch.contributions:
            sha = contribution_sha(contribution)
            if sha in rows:
                continue
            rows[sha] = [
                write_contribution(
                    session,
                    contribution,
                    source_run_id=source_runs[batch.source_name],
                    raw_response_id=raw_id,
                    data_mode=batch.data_mode,
                    fetched_at=_contribution_fetched_at(contribution, fetched),
                    lead_scope=_lead_scope(contribution),
                    lead_identity_id=lead_ids[cluster_of[sha]],
                )
            ]
            inserted += 1
    return inserted


def _rebuild_mapping(session: Session, wanted: Mapping[uuid.UUID, uuid.UUID]) -> None:
    """Make ``contribution_lead`` say ``wanted`` for these contributions."""
    existing = {
        row.contribution_id: row
        for row in session.scalars(
            sa.select(ContributionLead).where(
                ContributionLead.contribution_id.in_(wanted)
            )
        )
    }
    for contribution_id, lead in sorted(wanted.items(), key=lambda kv: str(kv[0])):
        row = existing.get(contribution_id)
        if row is None:
            session.add(
                ContributionLead(contribution_id=contribution_id, lead_identity_id=lead)
            )
        elif row.lead_identity_id != lead:
            row.lead_identity_id = lead
    session.flush()


def _fetched_at(batch: SourceBatch, default: datetime) -> datetime:
    times = [p.fetched_at for c in batch.contributions for p in c.provenance]
    return min(times) if times else default


def _contribution_fetched_at(
    contribution: LeadContribution, default: datetime
) -> datetime:
    return contribution.provenance[0].fetched_at if contribution.provenance else default


def _lead_scope(contribution: LeadContribution) -> str:
    person = any(
        path.startswith("person.") or path == _BARE_EMAIL
        for path in contribution.values
    )
    return "person" if person else "company"


def _key(source_name: str, record: FieldProvenance, path: str) -> _FieldKey:
    return (source_name, path, record.raw_field_path, record.fetched_at)


def _index_fields(
    session: Session,
    contribution: LeadContribution,
    contribution_id: uuid.UUID,
    index: dict[_FieldKey, uuid.UUID],
) -> None:
    stored = {
        path: field_id
        for path, field_id in session.execute(
            sa.select(ContributionField.canonical_path, ContributionField.id).where(
                ContributionField.contribution_id == contribution_id
            )
        )
    }
    for record in contribution.provenance:
        index.setdefault(
            _key(contribution.source_name, record, record.canonical_path),
            stored[record.canonical_path],
        )


def _field_id(index: dict[_FieldKey, uuid.UUID], record: FieldProvenance) -> uuid.UUID:
    paths = [record.canonical_path]
    if record.canonical_path == _EMAIL:
        paths.append(
            _BARE_EMAIL
        )  # a CRM source's bare ``email``, re-keyed by the merge
    for path in paths:
        found = index.get(_key(record.source_name, record, path))
        if found is not None:
            return found
    raise LookupError(
        f"no stored field for {record.source_name!r} {record.canonical_path!r}"
    )


def _write_canonical(
    session: Session,
    identity_id: uuid.UUID,
    result: ProjectionResult,
    lead: CanonicalLead,
    index: dict[_FieldKey, uuid.UUID],
    computed_at: datetime,
    projection_version: int,
    projection_fingerprint: str | None,
) -> None:
    """Insert or update the identity's canonical lead and rewrite its provenance."""
    row = session.scalar(
        sa.select(CanonicalLeadRow).where(
            CanonicalLeadRow.lead_identity_id == identity_id
        )
    )
    if row is None:
        row = CanonicalLeadRow(lead_identity_id=identity_id)
        session.add(row)
    else:
        session.execute(
            sa.delete(CanonicalFieldProvenance).where(
                CanonicalFieldProvenance.canonical_lead_id == row.id
            )
        )
    row.email = None if lead.email is None else str(lead.email)
    row.email_status = None if lead.email is None else lead.email_status.value
    row.linkedin_url = None if lead.linkedin_url is None else str(lead.linkedin_url)
    row.full_name = lead.full_name
    row.employments = [e.model_dump(mode="json") for e in lead.employments]
    row.tech_signals = [s.model_dump(mode="json") for s in lead.tech_signals]
    row.intent_signals = [s.model_dump(mode="json") for s in lead.intent_signals]
    row.opt_out = lead.opt_out
    row.suppressed = lead.suppressed
    row.contributing_sources = list(result.contributing_sources)
    row.computed_at = computed_at
    row.projection_version = projection_version
    row.projection_fingerprint = projection_fingerprint
    row.primary_domain = result.primary_domain
    row.primary_domain_source = (
        None
        if result.primary_domain_source is None
        else result.primary_domain_source.value
    )
    session.flush()
    agreeing = dict(result.agreement)
    winners: dict[str, uuid.UUID] = {}
    losers: dict[str, list[str]] = {}
    for record in result.provenance:  # per path: winner, agreeing, then superseded
        path = record.canonical_path
        if record.superseded:
            losers.setdefault(path, []).append(str(_field_id(index, record)))
        elif path not in winners:
            winners[path] = _field_id(index, record)
    for path, winner in winners.items():
        session.add(
            CanonicalFieldProvenance(
                canonical_lead_id=row.id,
                canonical_path=path,
                winning_field_id=winner,
                agreeing_source_count=agreeing[path],
                superseded_field_ids=losers.get(path, []),
            )
        )
    session.flush()
