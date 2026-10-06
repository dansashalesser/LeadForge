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

__all__ = ["INDEX_KEY_NAME", "KEY_NAME", "store_digest", "store_key"]

KEY_NAME = "content_digest"
# The match-key index's own key (0011): a second key, so an index digest and a
# content digest never share one.
INDEX_KEY_NAME = "match_key_index"
_CACHE = "leadforge.store_key"


def store_key(session: Session, name: str) -> bytes:
    """The store's secret ``name``, read once per session; never logged."""
    cache = session.info.setdefault(_CACHE, {})
    cached = cache.get(name)
    if isinstance(cached, bytes):
        return cached
    value = session.scalar(sa.select(StoreSecret.value).where(StoreSecret.name == name))
    if value is None:
        raise LookupError(f"the store has no {name} key: migrate it to head")
    key = cache[name] = bytes.fromhex(value)
    return key


def store_digest(session: Session, identity: str) -> str:
    """HMAC-SHA256 (hex) of ``identity`` under the store's content key."""
    key = store_key(session, KEY_NAME)
    return hmac.new(key, identity.encode("utf-8"), hashlib.sha256).hexdigest()
