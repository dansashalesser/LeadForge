"""The match-key index: look a lead up by key without a scan (migration 0011).

Follow-up (user request 2026-10-06). ``lead_match_key`` maps (kind, keyed digest) to
an active lead: ``find_lead`` reads one indexed row set instead of every lead.

Provisional decisions (see the follow-up ledger):

* A row is a keyed HMAC digest (``match_key_digest.MatchKeyDigester``) of the
  normalised value, never the value: a plain hash of an email or LinkedIn URL is
  reversible by a dictionary attack. The key is the store's own (``store_key``,
  ``INDEX_KEY_NAME``, written by 0011), not ``LEADFORGE_MATCH_KEY_SECRET``: that one
  is random per run when unset, which would make every stored digest unfindable.
* Values are normalised by the ``match_keys`` normalizers clustering uses
  (``normalize_email``: case; ``normalize_linkedin_url``: www/country/mobile hosts,
  path case, query and fragment), so a lookup agrees with how leads were joined.
* The keys are the lead's own ``email`` (any status, role addresses included: a lead
  is looked up by what it holds, not by what clustered it; the digest uses the email
  kind ``VERIFIED_EMAIL`` for domain separation) and ``linkedin_url``.
* One code path writes it: ``reindex``, called by ``merged_leads.persist_merge`` for
  every lead the merge wrote or retired, so it is rebuilt with the derived
  ``contribution_lead`` mapping and never names a retired lead. Migration 0011
  backfills through ``index_rows``, the same digests.
* A digest is truncated (``DIGEST_HEX_CHARS``), so a hit is a candidate: the reader
  confirms it on the loaded lead.
"""

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.match_key_digest import MatchKeyDigester
from leadforge.lead_ingestion.match_keys import (
    MatchKey,
    MatchKeyKind,
    normalize_email,
    normalize_linkedin_url,
)
from leadforge.lead_ingestion.models import CanonicalLead
from leadforge.lead_ingestion.store.models import LeadMatchKey
from leadforge.lead_ingestion.store.store_key import INDEX_KEY_NAME, store_key

__all__ = ["index_digester", "index_rows", "indexed_leads", "lead_keys", "reindex"]


def lead_keys(email: object, linkedin_url: object) -> tuple[MatchKey, ...]:
    """The normalised keys of a lead's own email and LinkedIn URL; none for a
    missing or unusable value."""
    keys = []
    url = normalize_linkedin_url(None if linkedin_url is None else str(linkedin_url))
    if url is not None:
        keys.append(MatchKey(MatchKeyKind.LINKEDIN_URL, url))
    address = normalize_email(None if email is None else str(email))
    if address is not None:
        keys.append(MatchKey(MatchKeyKind.VERIFIED_EMAIL, address))
    return tuple(keys)


def _kind(key: MatchKey) -> str:
    return key.kind.name.lower()


def index_rows(
    digester: MatchKeyDigester,
    lead_id: uuid.UUID,
    email: object,
    linkedin_url: object,
) -> list[dict[str, object]]:
    """The index rows of one lead, as column values (the migration inserts them)."""
    return [
        {
            "id": uuid.uuid4(),
            "lead_identity_id": lead_id,
            "kind": _kind(key),
            "digest": digester.digest(key),
        }
        for key in lead_keys(email, linkedin_url)
    ]


def index_digester(session: Session) -> MatchKeyDigester:
    return MatchKeyDigester(
        store_key(session, INDEX_KEY_NAME), comparable_across_runs=True
    )


def reindex(session: Session, leads: Mapping[uuid.UUID, CanonicalLead | None]) -> None:
    """Make the index name exactly ``leads``' keys (``None``: a lead with no
    person, or retired, gets none); one delete and one insert."""
    if not leads:
        return
    session.execute(
        sa.delete(LeadMatchKey).where(LeadMatchKey.lead_identity_id.in_(leads))
    )
    digester = index_digester(session)
    session.add_all(
        LeadMatchKey(**row)
        for lead_id, lead in sorted(leads.items(), key=lambda kv: str(kv[0]))
        if lead is not None
        for row in index_rows(digester, lead_id, lead.email, lead.linkedin_url)
    )
    session.flush()


def indexed_leads(session: Session, keys: Sequence[MatchKey]) -> set[uuid.UUID]:
    """The leads indexed under every one of ``keys``; one query."""
    digester = index_digester(session)
    wanted = {(_kind(k), digester.digest(k)) for k in keys}
    rows = session.execute(
        sa.select(
            LeadMatchKey.lead_identity_id, LeadMatchKey.kind, LeadMatchKey.digest
        ).where(
            sa.or_(
                *(
                    sa.and_(LeadMatchKey.kind == kind, LeadMatchKey.digest == digest)
                    for kind, digest in sorted(wanted)
                )
            )
        )
    )
    found: dict[uuid.UUID, set[tuple[str, str]]] = defaultdict(set)
    for lead_id, kind, digest in rows:
        found[lead_id].add((kind, digest))
    return {lead_id for lead_id, got in found.items() if got >= wanted}
