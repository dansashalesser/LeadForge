"""Load stored leads back (follow-up, user request 2026-10-06).

The read path of the Lead Store. ``load_lead`` rebuilds one stored lead as a
``StoredLead``: the ``CanonicalLead`` the projection produced as the store saved it
(the same type; a domainless company carries the persisted id ``merged_leads.
lead_owned_employments`` gave it, the one source of that id) and what the store keeps
about it. ``list_leads`` pages leads in a deterministic order;
``find_lead`` looks leads up by Match Key. Every read is synchronous, writes nothing,
logs nothing and takes a bounded number of queries, independent of how many leads,
fields or sources it returns (no N+1). Nothing here logs, so no PII reaches a log.

Provisional decisions (see the follow-up ledger):

* ``StoredLead.lead`` is the domain model itself, rebuilt (``_rehydrate``) from the
  ``canonical_lead`` row. ``structure_guard`` allows exactly that one function to build
  a ``CanonicalLead`` outside the Merge Engine: it validates stored values and runs no
  merge logic (this module imports no Merge Engine module; a structural test holds
  that), and the round-trip property test proves it equal to the projection.
* Provenance is what the projection produced, as the store keeps it
  (``canonical_field_provenance``): per path, sorted, the winning field, the agreeing
  fields (0011) and the superseded losers, each rebuilt by ``contributions.
  stored_provenance`` (source, Field Confidence, Confidence Origin, raw path). It is
  persisted, not derived at read time: deriving it would re-run the projection's
  agreement logic here, under the trust ranking of the read, not of the projection. A
  path written before 0011 has no agreeing fields recorded (its count still is) until
  the lead is projected again.
* ``stale`` is ``None`` when the current projection version is unknown: neither given
  nor recorded by a completed run (``RunRecordRepository.latest_projection_stamp``).
  A retired lead is never stale (``merged_leads.stale_projections`` never recomputes
  one).
* ``follow_successor`` walks ``lead_succession`` one query per generation, never
  revisiting a lead (cycle-safe). It returns the one active successor, and raises
  ``SuccessionError`` (naming ids, never personal data) for a split (several) or a
  succession that ends in no active lead.
* ``list_leads`` orders by lead id; ``after`` is a keyset cursor (the last id of the
  previous page). ``company_id`` lives inside the ``employments`` JSON: a text
  pre-filter narrows the rows and every hit is confirmed on the rebuilt lead.
* ``find_lead`` matches a lead's own ``email`` or ``linkedin_url``, normalized by the
  ``match_keys`` normalizers clustering uses, on active leads only; several criteria
  must all match, and a given value that normalizes to nothing is a ``ValueError``.
  It reads the match-key index (``store.match_key_index``: keyed digests, one query)
  and confirms every candidate on the loaded lead (a digest is truncated). It returns
  every match, sorted by id: a role address can be several people's ``email``.
* Web evidence is attached to a lead at read time: a stored ``company.web_evidence``
  contribution whose attachment is not ``unattached`` and whose ``company.domain``
  shares a domain (``companies.company_domains``) with one of the lead's companies.
"""

import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.adapters.web_evidence import Attachment
from leadforge.lead_ingestion.companies import company_domains
from leadforge.lead_ingestion.models import CanonicalLead, FieldProvenance
from leadforge.lead_ingestion.store.contributions import (
    aware_utc,
    stored_provenance,
    stored_value,
)
from leadforge.lead_ingestion.store.match_key_index import indexed_leads, lead_keys
from leadforge.lead_ingestion.store.models import (
    CanonicalFieldProvenance,
    CanonicalLeadRow,
    ContributionField,
    LeadIdentity,
    LeadSuccession,
    SourceContribution,
)
from leadforge.lead_ingestion.store.run_records import RunRecordRepository
from leadforge.lead_ingestion.tie_resolution import TieOutcome, TieSource

__all__ = [
    "StoredLead",
    "SuccessionError",
    "WebEvidence",
    "find_lead",
    "list_leads",
    "load_lead",
]

DEFAULT_PAGE = 50
_EVIDENCE = "company.web_evidence."
_ATTACHMENT = _EVIDENCE + "attachment"
_COMPANY_DOMAIN = "company.domain"


class SuccessionError(LookupError):
    """A retired lead has no single active successor; ``lead_ids`` names the active
    ones found (several: a split; none: the succession ends in no active lead)."""

    def __init__(self, lead_id: uuid.UUID, lead_ids: Sequence[uuid.UUID]) -> None:
        self.lead_ids = tuple(lead_ids)
        found = len(self.lead_ids)
        super().__init__(
            f"retired lead {lead_id} has {found} active successors"
            + (f": {', '.join(map(str, self.lead_ids))}" if found else "")
        )


@dataclass(frozen=True)
class WebEvidence:
    """One stored web-evidence record attached to the lead's company.

    ``values`` maps the path under ``company.web_evidence.`` to its stored value;
    provider text (title, snippet) is ``UntrustedText``. Hidden from ``repr``.
    """

    source_name: str
    fetched_at: datetime
    attachment: Attachment
    # The company's registrable domains the record names.
    domains: tuple[str, ...]
    values: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True)
class StoredLead:
    """A stored lead: the projected ``CanonicalLead`` and what the store keeps on it."""

    lead_id: uuid.UUID
    lead: CanonicalLead = field(repr=False)
    # Per canonical path (sorted): the winner, the agreeing, the superseded losers.
    provenance: tuple[FieldProvenance, ...] = field(repr=False)
    # (canonical path, distinct sources holding the winning value), sorted by path.
    agreement: tuple[tuple[str, int], ...]
    contributing_sources: tuple[str, ...]
    primary_domain: str | None = field(repr=False)
    primary_domain_source: TieSource | None
    projection_version: int
    projection_fingerprint: str | None = field(repr=False)
    computed_at: datetime
    # None: the current projection version is not known (see module docstring).
    stale: bool | None
    retired_at: datetime | None
    # The leads that directly replaced this one, sorted; () while it is active.
    successor_ids: tuple[uuid.UUID, ...]
    web_evidence: tuple[WebEvidence, ...] = field(repr=False)

    @property
    def retired(self) -> bool:
        return self.retired_at is not None

    @property
    def primary_domain_flagged(self) -> bool:
        """True when a lowest-sorted fallback stands in for a tie resolution."""
        source = self.primary_domain_source
        return source is not None and TieOutcome(None, source).flagged

    def provenance_of(self, canonical_path: str) -> tuple[FieldProvenance, ...]:
        """The winner, agreeing and superseded records of one canonical path."""
        return tuple(p for p in self.provenance if p.canonical_path == canonical_path)

    def sources_of(self, canonical_path: str) -> tuple[str, ...]:
        """Sorted names of every source that contributed a value for the path."""
        return tuple(
            sorted({p.source_name for p in self.provenance_of(canonical_path)})
        )


def load_lead(
    session: Session,
    lead_id: uuid.UUID,
    *,
    follow_successor: bool = False,
    current_version: int | None = None,
) -> StoredLead | None:
    """The stored lead ``lead_id``, retired or not; ``None`` when there is none.

    With ``follow_successor`` a retired lead resolves to its one active successor
    (``SuccessionError`` when there is no single one); an active lead is itself.
    """
    if follow_successor:
        lead_id = _active_successor(session, lead_id)
    loaded = _load(
        session, [CanonicalLeadRow.lead_identity_id == lead_id], current_version
    )
    return loaded[0] if loaded else None


def list_leads(
    session: Session,
    *,
    include_retired: bool = False,
    company_id: str | None = None,
    limit: int = DEFAULT_PAGE,
    after: uuid.UUID | None = None,
    current_version: int | None = None,
) -> tuple[StoredLead, ...]:
    """Up to ``limit`` leads ordered by id, after the keyset cursor ``after``."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    criteria: list[sa.ColumnElement[bool]] = []
    if not include_retired:
        criteria.append(LeadIdentity.retired_at.is_(None))
    if company_id is not None:
        # A pre-filter only: the id may also appear elsewhere in the JSON text.
        criteria.append(
            sa.cast(CanonicalLeadRow.employments, sa.Text).contains(
                f'"{company_id}"', autoescape=True
            )
        )
    out: list[StoredLead] = []
    while len(out) < limit:
        cursor = [] if after is None else [CanonicalLeadRow.lead_identity_id > after]
        wanted = limit - len(out)
        loaded = _load(session, [*criteria, *cursor], current_version, limit=wanted)
        out.extend(
            x
            for x in loaded
            if company_id is None
            or any(e.company.company_id == company_id for e in x.lead.employments)
        )
        if len(loaded) < wanted:
            break
        after = loaded[-1].lead_id
    return tuple(out)


def find_lead(
    session: Session, *, email: str | None = None, linkedin_url: str | None = None
) -> tuple[StoredLead, ...]:
    """Active leads whose own email / LinkedIn URL is this Match Key, sorted by id."""
    if email is None and linkedin_url is None:
        raise ValueError("find_lead needs an email or linkedin_url")
    wanted = set(lead_keys(email, linkedin_url))
    if len(wanted) != (email is not None) + (linkedin_url is not None):
        raise ValueError("find_lead was given an unusable email or linkedin_url")
    ids = indexed_leads(session, sorted(wanted))
    if not ids:
        return ()
    loaded = _load(
        session,
        [
            CanonicalLeadRow.lead_identity_id.in_(ids),
            LeadIdentity.retired_at.is_(None),
        ],
        None,
    )
    return tuple(
        x for x in loaded if wanted <= set(lead_keys(x.lead.email, x.lead.linkedin_url))
    )


# ------------------------------------------------------------------ batch loading


def _load(
    session: Session,
    criteria: Sequence[sa.ColumnElement[bool]],
    current_version: int | None,
    *,
    limit: int | None = None,
) -> tuple[StoredLead, ...]:
    """Every lead meeting ``criteria``, by id, in a fixed number of queries."""
    pairs = session.execute(
        sa.select(CanonicalLeadRow, LeadIdentity)
        .join(LeadIdentity, LeadIdentity.id == CanonicalLeadRow.lead_identity_id)
        .where(*criteria)
        .order_by(CanonicalLeadRow.lead_identity_id)
        .limit(limit)
    ).all()
    if not pairs:
        return ()
    ids = [identity.id for _, identity in pairs]
    successors = _successors(session, ids)
    provenance = _provenance(session, [row.id for row, _ in pairs])
    if current_version is None:
        stamp = RunRecordRepository(session).latest_projection_stamp()
        current_version = None if stamp is None else stamp[0]
    leads = [(row, identity, _rehydrate(row)) for row, identity in pairs]
    evidence = _web_evidence(
        session,
        {
            d
            for _, _, lead in leads
            for e in lead.employments
            for d in e.company.domains
        },
    )
    return tuple(
        StoredLead(
            lead_id=identity.id,
            lead=lead,
            provenance=provenance.get(row.id, ((), ()))[0],
            agreement=provenance.get(row.id, ((), ()))[1],
            contributing_sources=tuple(row.contributing_sources),
            primary_domain=row.primary_domain,
            primary_domain_source=(
                None
                if row.primary_domain_source is None
                else TieSource(row.primary_domain_source)
            ),
            projection_version=row.projection_version,
            projection_fingerprint=row.projection_fingerprint,
            computed_at=aware_utc(row.computed_at),
            stale=(
                None
                if current_version is None or identity.retired_at is not None
                else row.projection_version != current_version
            ),
            retired_at=(
                None if identity.retired_at is None else aware_utc(identity.retired_at)
            ),
            successor_ids=successors.get(identity.id, ()),
            web_evidence=_attached(evidence, lead),
        )
        for row, identity, lead in leads
    )


def _rehydrate(row: CanonicalLeadRow) -> CanonicalLead:
    """The stored projection as the domain model (the inverse of ``merged_leads.
    _write_canonical``)."""
    return CanonicalLead.model_validate(
        {
            "email": row.email,
            "email_status": row.email_status or "unknown",
            "email_is_role_address": row.email_is_role_address,
            "linkedin_url": row.linkedin_url,
            "full_name": row.full_name,
            "employments": row.employments or [],
            "tech_signals": row.tech_signals or [],
            "intent_signals": row.intent_signals or [],
            "opt_out": row.opt_out,
            "suppressed": row.suppressed,
            "role_contact_emails": row.role_contact_emails or [],
        }
    )


def _successors(
    session: Session, ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, tuple[uuid.UUID, ...]]:
    found: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for before, after in session.execute(
        sa.select(LeadSuccession.predecessor_id, LeadSuccession.successor_id).where(
            LeadSuccession.predecessor_id.in_(ids)
        )
    ):
        found[before].add(after)
    return {k: tuple(sorted(v, key=str)) for k, v in found.items()}


def _provenance(
    session: Session, row_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, tuple[tuple[FieldProvenance, ...], tuple[tuple[str, int], ...]]]:
    """Per canonical lead row: its kept provenance records and its agreement."""
    # Sorted here, not in SQL: a PostgreSQL collation may order paths unlike the
    # projection's Python sort.
    paths = sorted(
        session.scalars(
            sa.select(CanonicalFieldProvenance).where(
                CanonicalFieldProvenance.canonical_lead_id.in_(row_ids)
            )
        ),
        key=lambda p: p.canonical_path,
    )
    field_ids = {
        uuid.UUID(str(i))
        for p in paths
        for i in (
            p.winning_field_id,
            *(p.agreeing_field_ids or ()),
            *p.superseded_field_ids,
        )
    }
    fields = (
        {
            f.id: (f, c)
            for f, c in session.execute(
                sa.select(ContributionField, SourceContribution)
                .join(
                    SourceContribution,
                    SourceContribution.id == ContributionField.contribution_id,
                )
                .where(ContributionField.id.in_(field_ids))
            )
        }
        if field_ids
        else {}
    )

    def record(field_id: Any, path: str, superseded: bool) -> FieldProvenance:
        stored = stored_provenance(*fields[uuid.UUID(str(field_id))])
        # The path the projection kept (a CRM's bare ``email`` is ``person.email``).
        return stored.model_copy(
            update={"canonical_path": path, "superseded": superseded}
        )

    records: dict[uuid.UUID, list[FieldProvenance]] = defaultdict(list)
    agreement: dict[uuid.UUID, list[tuple[str, int]]] = defaultdict(list)
    for p in paths:
        path = p.canonical_path
        records[p.canonical_lead_id].append(record(p.winning_field_id, path, False))
        records[p.canonical_lead_id].extend(
            record(i, path, False) for i in p.agreeing_field_ids or ()
        )
        records[p.canonical_lead_id].extend(
            record(i, path, True) for i in p.superseded_field_ids
        )
        agreement[p.canonical_lead_id].append((path, p.agreeing_source_count))
    return {k: (tuple(records[k]), tuple(agreement[k])) for k in records}


def _web_evidence(session: Session, domains: set[str]) -> tuple[WebEvidence, ...]:
    """Every attached web-evidence record naming one of ``domains``; one query."""
    if not domains:
        return ()
    with_attachment = sa.select(ContributionField.contribution_id).where(
        ContributionField.canonical_path == _ATTACHMENT
    )
    rows = session.execute(
        sa.select(ContributionField, SourceContribution)
        .join(
            SourceContribution,
            SourceContribution.id == ContributionField.contribution_id,
        )
        .where(ContributionField.contribution_id.in_(with_attachment))
        .order_by(SourceContribution.fetched_at, SourceContribution.id)
    )
    grouped: dict[uuid.UUID, tuple[SourceContribution, dict[str, Any]]] = {}
    for f, c in rows:
        grouped.setdefault(c.id, (c, {}))[1][f.canonical_path] = stored_value(f)
    out: list[WebEvidence] = []
    for contribution, values in grouped.values():
        attachment = Attachment(values[_ATTACHMENT])
        named = company_domains(values.get(_COMPANY_DOMAIN))
        if attachment is Attachment.UNATTACHED or not named & domains:
            continue
        out.append(
            WebEvidence(
                source_name=contribution.source_name,
                fetched_at=aware_utc(contribution.fetched_at),
                attachment=attachment,
                domains=tuple(sorted(named)),
                values={
                    path.removeprefix(_EVIDENCE): value
                    for path, value in sorted(values.items())
                    if path.startswith(_EVIDENCE)
                },
            )
        )
    return tuple(out)


def _attached(
    evidence: Iterable[WebEvidence], lead: CanonicalLead
) -> tuple[WebEvidence, ...]:
    own = {d for e in lead.employments for d in e.company.domains}
    return tuple(e for e in evidence if own.intersection(e.domains))


def _active_successor(session: Session, lead_id: uuid.UUID) -> uuid.UUID:
    """``lead_id`` when active, else its one active successor (cycle-safe)."""
    retired_at = session.scalar(
        sa.select(LeadIdentity.retired_at).where(LeadIdentity.id == lead_id)
    )
    if retired_at is None:
        return lead_id
    seen, frontier, active = {lead_id}, {lead_id}, set()
    while frontier:
        step = session.execute(
            sa.select(
                LeadSuccession.successor_id,
                LeadIdentity.retired_at,
                CanonicalLeadRow.id,
            )
            .join(LeadIdentity, LeadIdentity.id == LeadSuccession.successor_id)
            .outerjoin(
                CanonicalLeadRow,
                CanonicalLeadRow.lead_identity_id == LeadSuccession.successor_id,
            )
            .where(LeadSuccession.predecessor_id.in_(frontier))
        ).all()
        frontier = set()
        for successor, gone, row in step:
            if successor in seen:
                continue
            seen.add(successor)
            if gone is not None:
                frontier.add(successor)
            elif row is not None:  # an active identity that names a person
                active.add(successor)
    if len(active) != 1:
        raise SuccessionError(lead_id, sorted(active, key=str))
    return active.pop()
