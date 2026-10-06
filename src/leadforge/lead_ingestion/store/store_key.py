"""Keyed digests of stored content (follow-up 2026-10-06; Requirements 8.12, 10.5).

A digest of a contribution or a raw payload is a digest of personal data: a plain
sha256 of it is reversible by a dictionary attack wherever it travels on its own.
A persisted digest is therefore HMAC-SHA256 under the store's own random key
(``store_secret``, written once by migration 0007). Only ``content_sha`` is one: it
must be deterministic, since it is how a re-fetched observation is recognised. A raw
payload's ``request_fingerprint`` is looked up by nothing, so it is a random value
instead (``merged_leads``), and leaks nothing once the payload is purged.

Provisional decisions:

* The key lives in the store, not the environment. Rejected: the Match Key secret
  (``LEADFORGE_MATCH_KEY_SECRET``): when it is absent the run's key is random, and a
  per-run key would make every stored identity unmatchable by the next run (duplicate
  contributions and Leads). A digest is useless without the store, and whoever holds
  the store (and so the key) already holds the contribution rows themselves, in
  plaintext, for as long as the digest exists (the log is append-only).
* Rejected: a random id per contribution instead of a digest. It cannot recognise a
  re-fetch of the same observation, which is the point of ``content_sha``.
* ``store_digest`` keys a text that is already an identity (``contributions.
  contribution_sha``), the same step migration 0007 applied to the plain hex it
  found, so ids stay consistent across the revision.
* The key is read once per session (cached in ``session.info``) and never logged.
"""

import hashlib
import hmac

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.store.models import StoreSecret

__all__ = ["store_digest"]

KEY_NAME = "content_digest"
_CACHE = "leadforge.store_key"


def _key(session: Session) -> bytes:
    cached = session.info.get(_CACHE)
    if isinstance(cached, bytes):
        return cached
    value = session.scalar(
        sa.select(StoreSecret.value).where(StoreSecret.name == KEY_NAME)
    )
    if value is None:
        raise LookupError("the store has no content key: migrate it to head")
    key = bytes.fromhex(value)
    session.info[_CACHE] = key
    return key


def store_digest(session: Session, identity: str) -> str:
    """HMAC-SHA256 (hex) of ``identity`` under the store's key."""
    return hmac.new(_key(session), identity.encode("utf-8"), hashlib.sha256).hexdigest()
