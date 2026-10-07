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
* Role addresses are not recognised by the key reading itself: ``DisqualifiedAddresses``
  (8.14, below) finds them and ``extract_match_keys`` skips them.
* Identity Exclusions (task 16.6, 8.13) are specific normalised LinkedIn URLs and
  verified emails barred from acting as a key; ``extract_match_keys`` skips them. A
  barred key's kind stays in ``MatchKeys.barred_kinds``, because the contribution still
  names that identity: clustering must keep treating it as "has a LinkedIn / email key"
  for 8.2 and 8.3, else barring a key would make its holder look key-less and could
  MERGE people (an exclusion only ever refines the partition). Name+domain values are
  not excludable (no requirement text names a case; add when one does). The set is
  personal data: hidden from ``repr``, errors name no value. The set offers no digest
  of its own (a plain hash of addresses is reversible by dictionary attack); a change
  is observed through projection's keyed ``ProjectionBasis`` (``match_key_digest``).
* Role addresses (task 16.7, 8.14) are disqualified structurally: an address reported
  against two or more DISTINCT names anywhere in the contribution set being clustered.
  ``DisqualifiedAddresses.from_contributions`` is that first pass (a pure function of
  the whole set, so arrival order cannot matter); ``extract_match_keys`` takes it as
  ``disqualified`` and skips the address like a barred key, keeping its kind in
  ``barred_kinds`` for the same reason (plain removal would make the holder look
  key-less and let name+domain merge people). Names are compared as the name key is
  (``_full_name``: NFKC, casefold, whitespace collapse; no reordering or fuzzy match, so
  ``Doe, Jane`` is another name: that only ever under-merges). A missing, blank or
  masked name (any ``*``, as a provider obfuscates last names) is not a name. Addresses
  of EVERY ``email_status`` count, since the status says deliverable, not unshared.
  The set is independent of Identity Exclusions; both bar. The
  address stays on the contribution and in provenance; it just is not a key.
* Role words (user decision 2026-10-06, amends 8.14: "I don't know whats in info@").
  The same set also holds every stated address (``stated_email``, any status) whose
  local part, lowercased and without a ``+tag``, is in ``ROLE_LOCAL_PARTS``: a generic
  inbox is a role address even under one name. ``is_role_address`` is that test; the
  list lives only here. Whole local part only (``information@``, ``ann.info@`` are
  not roles); English words only, so a role inbox in another language still relies
  on the two-names rule. Consumers read ``DisqualifiedAddresses.addresses`` (or the
  cluster's ``role_addresses``), never the list.
* Distinct LinkedIn identities (follow-up, user decision: a different LinkedIn URL is
  a different person). The same first pass also disqualifies an address, and a
  name+domain candidate value, reported together with two or more DISTINCT normalised
  LinkedIn URLs (``linkedin_identity``: raw, so a URL barred by an Identity Exclusion
  still counts). Such a value is evidently shared or wrong. For an address, a bare
  holder would otherwise bridge two people by transitive closure. A name+domain
  candidate cannot bridge (only LinkedIn-less records use it); barring it is a
  deliberate under-merge: two people demonstrably share that name+domain, so it
  cannot tell bare records apart. Barred like 8.14 addresses.
* One-sided name+domain (follow-up, user decision 2026-10-06): a record stating an
  email may join an email-less one on name+domain, never one stating a different
  address. ``stated_email`` is that address (``person.email``, else the CRM ``email``
  path; any status; raw, so barring it changes nothing). A candidate value stated by
  LinkedIn-less records with two or more distinct addresses is disqualified (added to
  ``name_domains``), so a bare record cannot bridge them. Addresses of LinkedIn
  holders do not count, since those never join by name+domain.
* Personal email evidence (user-directed fix 2026-10-06). ``personal_email`` is the
  stated address as identity evidence, and ``emails_conflict`` the one rule both
  passes apply. A shared address (``DisqualifiedAddresses.addresses``: the 8.14 role
  address such as ``info@``, or one seen with two LinkedIn URLs) is not the person's
  own, so it is no evidence at all. Only ``email_status == VERIFIED`` makes an address
  known; any other status (unverified, accept-all, unknown, or none, as on a bare CRM
  address) is a guess. Different addresses conflict when two are verified, or when none
  is verified and two guesses differ (neither is known); a guess beside one verified
  address is no conflict. A shared address still never acts as a Match Key.
* Values are read at the paths the adapters write (``person.*``, ``company.*``).
"""

from collections.abc import Collection, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import IntEnum
from unicodedata import normalize
from urllib.parse import unquote, urlsplit

import tldextract

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.models import EmailStatus, UntrustedText

__all__ = [
    "ROLE_LOCAL_PARTS",
    "DisqualifiedAddresses",
    "IdentityExclusions",
    "MatchKey",
    "MatchKeyKind",
    "MatchKeys",
    "PersonalEmail",
    "corroborates",
    "emails_conflict",
    "extract_match_keys",
    "is_role_address",
    "linkedin_identity",
    "normalize_email",
    "normalize_linkedin_url",
    "normalized_person_name",
    "personal_email",
    "registrable_domains",
    "stated_email",
]

_LINKEDIN = "person.linkedin_url"
_EMAIL = "person.email"
_BARE_EMAIL = "email"  # a CRM path; projection reads it as person.email
_EMAIL_STATUS = "person.email_status"
_FULL_NAME = "person.full_name"
_FIRST_NAME = "person.first_name"
_LAST_NAME = "person.last_name"
_TITLE = "person.title"
_COMPANY_NAME = "company.name"
_COMPANY_DOMAIN = "company.domain"

_MASK = "*"
_NAME_DOMAIN_SEPARATOR = "\x1f"
_CORROBORATING_STATUSES = frozenset({EmailStatus.UNVERIFIED, EmailStatus.ACCEPT_ALL})
# Generic inbox local parts (role words, user decision 2026-10-06). The one list:
# lowercase, compared with the whole local part before any ``+tag``.
ROLE_LOCAL_PARTS = frozenset(
    {
        "accounts",
        "admin",
        "billing",
        "careers",
        "contact",
        "enquiries",
        "finance",
        "hello",
        "help",
        "hr",
        "info",
        "inquiries",
        "jobs",
        "legal",
        "marketing",
        "media",
        "no-reply",
        "noreply",
        "office",
        "postmaster",
        "press",
        "privacy",
        "recruiting",
        "sales",
        "security",
        "service",
        "support",
        "team",
        "webmaster",
    }
)

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
    # Kinds the contribution offered but an Identity Exclusion barred (task 16.6).
    barred_kinds: frozenset[MatchKeyKind] = field(default=frozenset(), repr=False)


@dataclass(frozen=True)
class IdentityExclusions:
    """Normalised values barred from acting as a Match Key (8.13); personal data."""

    linkedin_urls: frozenset[str] = field(default=frozenset(), repr=False)
    emails: frozenset[str] = field(default=frozenset(), repr=False)

    @classmethod
    def from_values(
        cls, *, linkedin_urls: Iterable[str] = (), emails: Iterable[str] = ()
    ) -> "IdentityExclusions":
        """Normalise as key extraction does; an unusable value is an error."""
        urls_in, emails_in = tuple(linkedin_urls), tuple(emails)
        if not all(isinstance(v, str) for v in (*urls_in, *emails_in)):
            raise ValueError("an Identity Exclusion must name a usable key value")
        urls = {normalize_linkedin_url(u) for u in urls_in}
        addresses = {normalize_email(e) for e in emails_in}
        if None in urls or None in addresses:
            raise ValueError("an Identity Exclusion must name a usable key value")
        return cls(
            frozenset(u for u in urls if u), frozenset(a for a in addresses if a)
        )

    def bars(self, key: MatchKey) -> bool:
        if key.kind is MatchKeyKind.LINKEDIN_URL:
            return key.value in self.linkedin_urls
        if key.kind is MatchKeyKind.VERIFIED_EMAIL:
            return key.value in self.emails
        return False


@dataclass(frozen=True)
class DisqualifiedAddresses:
    """Key values evidently shared by different people; personal data.

    ``addresses``: reported against two or more distinct names (8.14), together with
    two or more distinct normalised LinkedIn URLs, or stated with a role-word local
    part (``is_role_address``). ``name_domains``: name+domain
    candidate values reported together with two or more distinct LinkedIn URLs, or by
    LinkedIn-less records whose personal addresses conflict (``emails_conflict``).
    """

    addresses: frozenset[str] = field(default=frozenset(), repr=False)
    name_domains: frozenset[str] = field(default=frozenset(), repr=False)

    @classmethod
    def from_contributions(
        cls, contributions: Iterable[LeadContribution]
    ) -> "DisqualifiedAddresses":
        """The values any contributions attach to different people; order-free."""
        names: dict[str, set[str]] = {}
        address_urls: dict[str, set[str]] = {}
        candidate_urls: dict[str, set[str]] = {}
        candidate_emails: dict[str, set[PersonalEmail]] = {}
        listed: set[str] = set()
        for contribution in contributions:
            values = contribution.values
            if (role := stated_email(values)) and is_role_address(role):
                listed.add(role)
            address = normalize_email(_text(values, _EMAIL))
            name = normalized_person_name(values)
            url = linkedin_identity(values)
            if address and name:
                names.setdefault(address, set()).add(name)
            if url:
                if address:
                    address_urls.setdefault(address, set()).add(url)
                for candidate in _name_domain_values(values):
                    candidate_urls.setdefault(candidate, set()).add(url)
            elif stated := personal_email(values):
                # 8.3 follow-up: only LinkedIn-less records join by name+domain, so
                # only their addresses can make a candidate ambiguous.
                for candidate in _name_domain_values(values):
                    candidate_emails.setdefault(candidate, set()).add(stated)
        # Shared addresses are known only once the whole set is read, so the
        # candidate test runs second and skips them.
        addresses = _shared(names) | _shared(address_urls) | listed
        ambiguous = frozenset(
            candidate
            for candidate, stated in candidate_emails.items()
            if emails_conflict(s for s in stated if s.address not in addresses)
        )
        return cls(addresses, _shared(candidate_urls) | ambiguous)


def _shared(found: Mapping[str, set[str]]) -> frozenset[str]:
    return frozenset(value for value, seen in found.items() if len(seen) > 1)


def corroborates(a: MatchKeys, b: MatchKeys) -> bool:
    """True when two contributions share a title or an employer (8.3's further gate)."""
    return bool(a.titles & b.titles) or bool(a.employers & b.employers)


def extract_match_keys(
    contribution: LeadContribution,
    exclusions: IdentityExclusions | None = None,
    disqualified: DisqualifiedAddresses | None = None,
) -> MatchKeys:
    """The Match Keys of ``contribution``, strongest kind first, and its evidence.

    Keys named by ``exclusions`` are skipped (8.13); their kinds go to ``barred_kinds``.
    So are ``disqualified`` addresses (8.14), identically.
    """
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

    for candidate in _name_domain_values(values):
        keys.add(MatchKey(MatchKeyKind.NAME_DOMAIN, candidate))

    barred = (
        {k for k in keys if exclusions.bars(k)} if exclusions is not None else set()
    )
    if disqualified is not None:
        barred |= {k for k in keys if _disqualifies(disqualified, k)}
    return MatchKeys(
        keys=tuple(sorted(keys - barred)),
        barred_kinds=frozenset(k.kind for k in barred),
        corroborating_emails=corroborating,
        titles=_fold_set(_text(values, _TITLE)),
        employers=_fold_set(_text(values, _COMPANY_NAME)),
    )


def _disqualifies(disqualified: DisqualifiedAddresses, key: MatchKey) -> bool:
    if key.kind is MatchKeyKind.VERIFIED_EMAIL:
        return key.value in disqualified.addresses
    if key.kind is MatchKeyKind.NAME_DOMAIN:
        return key.value in disqualified.name_domains
    return False


def _name_domain_values(values: Mapping[str, object]) -> list[str]:
    """The name+domain candidate values (8.3), one per registrable domain."""
    name = _full_name(values)
    if not name:
        return []
    domains = registrable_domains(values.get(_COMPANY_DOMAIN))
    return [name + _NAME_DOMAIN_SEPARATOR + domain for domain in domains]


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


_LINKEDIN_HOST = "linkedin.com"


def normalize_linkedin_url(url: str | None) -> str | None:
    if url is None or not url.strip():
        return None
    text = url.strip()
    parts = urlsplit(text if "//" in text else "//" + text)
    host = (parts.hostname or "").lower().rstrip(".")
    # www., country (uk., de.) and mobile subdomains all serve one profile.
    if host == _LINKEDIN_HOST or host.endswith("." + _LINKEDIN_HOST):
        host = _LINKEDIN_HOST
    segments = [s for s in unquote(parts.path).casefold().split("/") if s]
    if not host or not segments:
        return None
    return host + "/" + "/".join(segments)


def linkedin_identity(values: Mapping[str, object]) -> str | None:
    """The normalised LinkedIn URL a contribution names, barred or not."""
    return normalize_linkedin_url(_text(values, _LINKEDIN))


@dataclass(frozen=True)
class PersonalEmail:
    """A stated address as identity evidence for 8.3; personal data."""

    address: str = field(repr=False)
    verified: bool


def personal_email(
    values: Mapping[str, object], disqualified: DisqualifiedAddresses | None = None
) -> PersonalEmail | None:
    """The stated address unless it is shared (``disqualified.addresses``), with
    whether it is verified (``person.email_status``, read for the stated address
    as projection reads it, the bare CRM path included)."""
    address = stated_email(values)
    if address is None or (
        disqualified is not None and address in disqualified.addresses
    ):
        return None
    verified = values.get(_EMAIL_STATUS) == EmailStatus.VERIFIED
    return PersonalEmail(address, verified)


def emails_conflict(emails: Iterable[PersonalEmail]) -> bool:
    """Whether ``emails`` name two people: two verified addresses, or two guesses
    with no verified address. A guess beside a verified address is no conflict."""
    verified: set[str] = set()
    guessed: set[str] = set()
    for email in emails:
        (verified if email.verified else guessed).add(email.address)
    return len(verified) > 1 or (not verified and len(guessed) > 1)


def stated_email(values: Mapping[str, object]) -> str | None:
    """The normalised address a contribution states, of any ``email_status``.

    ``person.email``, else the bare CRM ``email`` path (as projection reads it). This
    is evidence for the 8.3 one-sided rule, never a Match Key itself.
    """
    path = _EMAIL if values.get(_EMAIL) is not None else _BARE_EMAIL
    return normalize_email(_text(values, path))


def is_role_address(address: str | None) -> bool:
    """Whether ``address`` has a role-word local part (``ROLE_LOCAL_PARTS``)."""
    normalised = normalize_email(address)
    if normalised is None:
        return False
    local = normalised.partition("@")[0].partition("+")[0]
    return local in ROLE_LOCAL_PARTS


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


def normalized_person_name(values: Mapping[str, object]) -> str | None:
    """The name as the name key and 8.14 compare it; ``None`` when it is not a name.

    Missing, blank and masked (any ``*``) names are not names (task 16.8 reuses 8.14's
    rule so both agree on what "distinct" means).
    """
    name = _full_name(values)
    return None if name is None or _MASK in name else name


def registrable_domains(value: object) -> list[str]:
    """Sorted registrable domains of ``company.domain`` (str or collection of text)."""
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
