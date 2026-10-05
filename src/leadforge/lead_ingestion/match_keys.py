"""Match Keys, ordered by durability (task 16.1; Requirements 8.1, 8.2, 8.3, 8.11).

Pure extraction of the identity keys a ``LeadContribution`` offers; clustering over
them is a later task. Provisional decisions (choices.md, 16.1):

* Kinds are declared strongest first (ADR-0003): LinkedIn URL, verified email, then
  ``(name, registrable domain)``. ``extract_match_keys`` returns keys in that order and,
  within a kind, sorted by value, so the result never depends on insertion order.
* A key's value is a normalised string; ``MatchKey`` and ``MatchKeys`` withhold every
  personal value from ``repr``, and errors name the canonical path only.
* LinkedIn: scheme, query, fragment and trailing slash are dropped, host and path are
  lowercased; the key is ``host/path``. ``www.`` and locale forms are NOT folded (the
  requirement does not say so; keeping them apart under-merges, never over-merges). A
  URL with no path names no profile and gives no key.
* Email: only ``person.email_status == VERIFIED`` gives a key (8.11); lowercased (not
  casefolded: ``ß`` would merge distinct mailboxes) and trimmed; ``+tag`` addresses
  are kept as written. ``UNVERIFIED``/``ACCEPT_ALL`` addresses are exposed as
  ``corroborating_emails`` only.
* Name: ``person.full_name`` if present, else ``person.first_name`` +
  ``person.last_name`` (both required); NFKC-normalised, casefolded, whitespace
  collapsed. One key per distinct registrable domain of ``company.domain`` (str or
  collection), via the PSL snapshot bundled with the pinned tldextract (private
  suffixes such as ``github.io`` included), never fetched at run time; punycode and
  Unicode spellings of a domain agree. A domain with no registrable part (a bare
  suffix, an IP, ``localhost``) is skipped.
* Key 3 is only a candidate. ``corroborates`` is the pairwise gate; employment dates
  have no canonical path yet, so only title and employer name corroborate.
* Role addresses are not recognised here: 8.14 disqualifies them structurally by
  names reported against them (task 16.7), with no curated role-word list.
* Values are read at the paths the adapters write (``person.*``, ``company.*``).
"""

from collections.abc import Collection, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import IntEnum
from unicodedata import normalize
from urllib.parse import urlsplit

import tldextract

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.models import EmailStatus, UntrustedText

__all__ = [
    "MatchKey",
    "MatchKeyKind",
    "MatchKeys",
    "corroborates",
    "extract_match_keys",
    "normalize_email",
    "normalize_linkedin_url",
]

_LINKEDIN = "person.linkedin_url"
_EMAIL = "person.email"
_EMAIL_STATUS = "person.email_status"
_FULL_NAME = "person.full_name"
_FIRST_NAME = "person.first_name"
_LAST_NAME = "person.last_name"
_TITLE = "person.title"
_COMPANY_NAME = "company.name"
_COMPANY_DOMAIN = "company.domain"

_NAME_DOMAIN_SEPARATOR = "\x1f"
_CORROBORATING_STATUSES = frozenset({EmailStatus.UNVERIFIED, EmailStatus.ACCEPT_ALL})

# Offline: only the Public Suffix List snapshot shipped in the pinned tldextract.
# Private suffixes (github.io, blogspot.com) count as suffixes, so two tenants of a
# shared host are never one registrable domain.
_PSL = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
)


class MatchKeyKind(IntEnum):
    """Key kinds; a lower number is more durable (survives more change)."""

    LINKEDIN_URL = 1
    VERIFIED_EMAIL = 2
    NAME_DOMAIN = 3


@dataclass(frozen=True, order=True)
class MatchKey:
    kind: MatchKeyKind
    value: str = field(repr=False)


@dataclass(frozen=True)
class MatchKeys:
    """What one contribution offers for identity matching; empty is a valid result."""

    keys: tuple[MatchKey, ...] = ()
    corroborating_emails: frozenset[str] = field(default=frozenset(), repr=False)
    titles: frozenset[str] = field(default=frozenset(), repr=False)
    employers: frozenset[str] = field(default=frozenset(), repr=False)


def corroborates(a: MatchKeys, b: MatchKeys) -> bool:
    """True when two contributions share a title or an employer (8.3's further gate)."""
    return bool(a.titles & b.titles) or bool(a.employers & b.employers)


def extract_match_keys(contribution: LeadContribution) -> MatchKeys:
    """The Match Keys of ``contribution``, strongest kind first, and its evidence."""
    values = contribution.values
    keys: set[MatchKey] = set()

    linkedin = normalize_linkedin_url(_text(values, _LINKEDIN))
    if linkedin:
        keys.add(MatchKey(MatchKeyKind.LINKEDIN_URL, linkedin))

    email = normalize_email(_text(values, _EMAIL))
    status = values.get(_EMAIL_STATUS)
    corroborating: frozenset[str] = frozenset()
    if email:
        if status == EmailStatus.VERIFIED:
            keys.add(MatchKey(MatchKeyKind.VERIFIED_EMAIL, email))
        elif status in _CORROBORATING_STATUSES:
            corroborating = frozenset({email})

    name = _full_name(values)
    if name:
        for domain in _registrable_domains(values.get(_COMPANY_DOMAIN)):
            keys.add(
                MatchKey(
                    MatchKeyKind.NAME_DOMAIN, name + _NAME_DOMAIN_SEPARATOR + domain
                )
            )

    return MatchKeys(
        keys=tuple(sorted(keys)),
        corroborating_emails=corroborating,
        titles=_fold_set(_text(values, _TITLE)),
        employers=_fold_set(_text(values, _COMPANY_NAME)),
    )


def _text(values: Mapping[str, object], path: str) -> str | None:
    value = values.get(path)
    if value is None:
        return None
    return _as_text(value, path)


def _as_text(value: object, path: str) -> str:
    if isinstance(value, UntrustedText):
        return value.value
    if isinstance(value, str):
        return value
    raise TypeError(f"{path} must be text, got {type(value).__name__}")


def _fold(text: str) -> str:
    return " ".join(normalize("NFKC", text).casefold().split())


def _fold_set(text: str | None) -> frozenset[str]:
    folded = _fold(text) if text else ""
    return frozenset({folded}) if folded else frozenset()


def normalize_linkedin_url(url: str | None) -> str | None:
    if url is None or not url.strip():
        return None
    text = url.strip()
    parts = urlsplit(text if "//" in text else "//" + text)
    host = (parts.hostname or "").lower()
    path = parts.path.lower().rstrip("/")
    if not host or not path:
        return None
    return host + path


def normalize_email(address: str | None) -> str | None:
    if address is None:
        return None
    # lower(), not casefold(): casefold folds "ß" to "ss", merging distinct mailboxes.
    lowered = address.strip().lower()
    local, at, domain = lowered.partition("@")
    return lowered if at and local and domain else None


def _full_name(values: Mapping[str, object]) -> str | None:
    full = _text(values, _FULL_NAME)
    if full is not None:
        return _fold(full) or None
    first, last = _text(values, _FIRST_NAME), _text(values, _LAST_NAME)
    joined = _fold(f"{first or ''} {last or ''}") if first and last else ""
    return joined or None


def _registrable_domains(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items: tuple[object, ...] = (value,)
    elif isinstance(value, Collection):
        items = tuple(value)
    else:
        raise TypeError(
            f"{_COMPANY_DOMAIN} must be text or a collection of text, "
            f"got {type(value).__name__}"
        )
    found: set[str] = set()
    for item in items:
        text = _as_text(item, _COMPANY_DOMAIN).strip()
        if text:
            registrable = _PSL(text).top_domain_under_public_suffix
            if registrable:
                found.add(_unicode_domain(registrable))
    return sorted(found)


def _unicode_domain(domain: str) -> str:
    """Lowercased domain with punycode labels decoded, so both spellings agree."""
    labels = []
    for label in normalize("NFKC", domain).lower().split("."):
        if label.startswith("xn--"):
            # Invalid punycode keeps the label as written.
            with suppress(UnicodeError):
                label = label[4:].encode("ascii").decode("punycode")
        labels.append(label)
    return ".".join(labels)
