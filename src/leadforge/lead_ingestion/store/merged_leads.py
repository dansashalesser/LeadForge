"""Persist one run's merge: identities, contributions, canonical leads (task 20).

``persist_merge`` is synchronous and takes the session of a ``StoreWriter.write_batch``
callable; it does not commit. It writes, in the caller's one transaction:

* a ``lead_identity`` per cluster, with the strongest Match Key kind that linked it;
* a ``raw_response`` per source batch and every contribution of it, each pointing at
  its cluster's identity from the first insert (the log is append-only, so the link
  cannot be added later);
* a ``canonical_lead`` per cluster that names a person, with a
  ``canonical_field_provenance`` row per resolved path: the winning contribution field,
  the number of sources holding its value and the superseded losers' field ids.

Provisional decisions (see choices.md, task 20):

* One transaction for the whole merge, not one per source: an identity is shared by
  contributions of several sources and a canonical lead points at fields of several, so
  a partial write would leave contributions with no projection. The run record is
  already committed on its own and is never touched here (9.6).
* A cluster that names no person (web evidence, a restriction naming only a domain)
  keeps its identity and contributions and gets no canonical lead.
* Identity Keys are not written: matching a new run against stored identities is 16.10
  and is not built, so a second run adds new identities rather than merging into these.
* ``lead_scope`` is a leftover NOT NULL column (ADR-0001 removed the concept): it is
  ``person`` when the contribution carries a person path or a bare CRM ``email``, else
  ``company``.
* A raw batch is stored under its phase as ``endpoint_key`` (a batch spans the
  endpoints of one call) and fingerprinted by its canonical JSON.
* A provenance record is matched to its stored field by (source, path, raw path,
  fetch time); a CRM source's bare ``email`` is the ``person.email`` the projection
  re-keyed it to. Byte-identical duplicates are interchangeable, so the first is used.
"""

import uuid
from collections.abc import Sequence
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
from leadforge.lead_ingestion.store.contributions import write_contribution
from leadforge.lead_ingestion.store.models import (
    CanonicalFieldProvenance,
    CanonicalLeadRow,
    ContributionField,
    LeadIdentity,
    SourceRun,
)
from leadforge.lead_ingestion.store.raw_responses import (
    RawResponseRepository,
    RetentionPolicy,
)

__all__ = ["MergeStored", "SourceBatch", "persist_merge"]

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
    contributions: int
    canonical_leads: int
    raw_responses: int


def persist_merge(
    session: Session,
    *,
    run_id: uuid.UUID,
    batches: Sequence[SourceBatch],
    merged: Sequence[tuple[IdentityCluster, ProjectionResult]],
    computed_at: datetime,
    projection_version: int,
    retention: RetentionPolicy,
) -> MergeStored:
    source_runs = _source_runs(session, run_id)
    for batch in batches:
        if batch.source_name not in source_runs:
            raise LookupError(f"run has no source {batch.source_name!r}")
    cluster_of = {
        id(contribution): index
        for index, (cluster, _) in enumerate(merged)
        for contribution in cluster.contributions
    }
    for batch in batches:
        for contribution in batch.contributions:
            if id(contribution) not in cluster_of:
                raise ValueError(
                    f"a contribution of {batch.source_name!r} is in no cluster"
                )

    identities: list[uuid.UUID] = []
    for cluster, _ in merged:
        identity = LeadIdentity(
            created_at=computed_at,
            primary_key_type=(
                cluster.merged_by[0].name.lower() if cluster.merged_by else None
            ),
        )
        session.add(identity)
        session.flush()
        identities.append(identity.id)

    fields: list[dict[_FieldKey, uuid.UUID]] = [{} for _ in merged]
    contributions = 0
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
            index = cluster_of[id(contribution)]
            contribution_id = write_contribution(
                session,
                contribution,
                source_run_id=source_runs[batch.source_name],
                raw_response_id=raw_id,
                data_mode=batch.data_mode,
                fetched_at=_contribution_fetched_at(contribution, fetched),
                lead_scope=_lead_scope(contribution),
                lead_identity_id=identities[index],
            )
            contributions += 1
            _index_fields(session, contribution, contribution_id, fields[index])

    canonical_leads = 0
    for index, (_, result) in enumerate(merged):
        if result.lead is None:
            continue
        _write_canonical(
            session,
            identities[index],
            result,
            result.lead,
            fields[index],
            computed_at,
            projection_version,
        )
        canonical_leads += 1
    return MergeStored(
        identities=len(identities),
        contributions=contributions,
        canonical_leads=canonical_leads,
        raw_responses=len(batches),
    )


def _source_runs(session: Session, run_id: uuid.UUID) -> dict[str, uuid.UUID]:
    rows = session.execute(
        sa.select(SourceRun.source_name, SourceRun.id).where(SourceRun.run_id == run_id)
    )
    return {name: source_run_id for name, source_run_id in rows}


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
) -> None:
    row = CanonicalLeadRow(
        lead_identity_id=identity_id,
        email=None if lead.email is None else str(lead.email),
        email_status=None if lead.email is None else lead.email_status.value,
        linkedin_url=None if lead.linkedin_url is None else str(lead.linkedin_url),
        full_name=lead.full_name,
        employments=[e.model_dump(mode="json") for e in lead.employments],
        tech_signals=[s.model_dump(mode="json") for s in lead.tech_signals],
        intent_signals=[s.model_dump(mode="json") for s in lead.intent_signals],
        opt_out=lead.opt_out,
        suppressed=lead.suppressed,
        contributing_sources=list(result.contributing_sources),
        computed_at=computed_at,
        projection_version=projection_version,
    )
    session.add(row)
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
