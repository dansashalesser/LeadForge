"""Attaching web evidence to a company by agreement (task 14.2 completion, option C).

Pure functions, no I/O. Used by the Google Search adapter; holds no provider schema.

User decision (option C, 2026-10-06): a search is issued for one company Discovery
found (its *anchor*, named by registrable domain). A result attaches to that company
only when the result host's registrable domain is one of the company's, or when a
result on a third-party host names one of the company's domains (in its URL, title or
snippet) and the query was anchored to that company. Anything else is unattached
evidence: kept and counted, never attached.

Provisional decisions (see choices.md, task 14.2 completion):

* Registrable domains come from ``match_keys.registrable_domains`` (the Public Suffix
  List bundled with the pinned tldextract, never fetched). Company domains go through
  ``companies.company_domains`` (webmail excluded); a company with none is no anchor.
* "Names the domain" is a whole-domain, case-insensitive match: the character before
  must not be a letter, digit or hyphen, and the domain must not continue with a
  letter, digit, hyphen or a further label (``acme.com.evil.net`` does not name
  ``acme.com``). In a URL only the part after the host is read, so userinfo
  (``acme.com@evil.io``) is no mention. The text is only matched, never classified.
* Duplicates are the same ``dedupe_key``: scheme, fragment, default port, host case
  and a trailing slash are ignored; the query string is kept.
* Agreeing hosts are counted by ``agreeing_host``: each distinct third-party
  registrable domain is one host, and the company's own domains and subdomains
  together are ONE host (user decision 2026-10-06: self-statements are not
  independent sources). Own-domain-only evidence therefore stays at level 1.
* Signal Strength (``signal_strength``) is an agreement count, recorded at ingestion
  (24.4) and provisional. Level 1 (0.25): one agreeing host. Level 2 (0.5): two or
  three. Level 3 (0.75): four or more, or the company's own domain plus at least one
  third-party host. Corroboration by two or more other sources naming the company
  adds one level, capped at level 4 (1.0). No agreeing host: no strength. Monotone in
  every input; it never reads result text.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import SplitResult, urlsplit, urlunsplit

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.companies import company_domains, domain_components
from leadforge.lead_ingestion.match_keys import registrable_domains
from leadforge.lead_ingestion.models import REQUEST_ECHO_PREFIX

__all__ = [
    "STRENGTH_LEVELS",
    "Attachment",
    "CompanyAnchor",
    "agreeing_host",
    "attachment_of",
    "company_anchors",
    "dedupe_key",
    "names_domain",
    "signal_strength",
    "url_host",
]

STRENGTH_LEVELS: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0)
_MEDIUM_HOSTS = 2
_STRONG_HOSTS = 4
_CORROBORATING = 2
_COMPANY_DOMAIN = "company.domain"
_DEFAULT_PORTS = {"80", "443"}
# Every own-domain result counts as this one host (cannot clash: not a domain).
_OWN_HOST = "<own domain>"


class Attachment(StrEnum):
    OWN_DOMAIN = "own_domain"
    THIRD_PARTY_MENTION = "third_party_mention"
    UNATTACHED = "unattached"


@dataclass(frozen=True)
class CompanyAnchor:
    """One company a query is issued for: its registrable domains, sorted."""

    domains: tuple[str, ...]
    # Distinct other sources whose contributions name this company.
    corroborating_sources: int

    @property
    def query_domain(self) -> str:
        return self.domains[0]


def company_anchors(
    work_list: Sequence[LeadContribution], *, exclude_source: str
) -> tuple[CompanyAnchor, ...]:
    """Distinct companies of the work list, in order of first appearance.

    Companies are clustered on overlapping registrable-domain sets, as everywhere in
    the slice. Contributions of ``exclude_source`` (the searching source) and those
    naming no usable domain are skipped. A domain that only echoes what its requester
    asked (raw path ``asked.*``, ADR-0006) names the company but is no independent
    source, so it adds no corroboration.
    """
    items = [
        (c.source_name, domains, _echoed_domain(c))
        for c in work_list
        if c.source_name != exclude_source
        and (domains := company_domains(c.values.get(_COMPANY_DOMAIN)))
    ]
    labels = domain_components([domains for _, domains, _ in items])
    groups: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        groups.setdefault(label, []).append(index)
    return tuple(
        CompanyAnchor(
            domains=tuple(sorted(frozenset().union(*(items[i][1] for i in members)))),
            corroborating_sources=len(
                {items[i][0] for i in members if not items[i][2]}
            ),
        )
        for members in groups.values()  # labels ascend: first appearance first
    )


def _echoed_domain(contribution: LeadContribution) -> bool:
    """Whether the contribution's company domain is its requester's echo."""
    return any(
        p.canonical_path == _COMPANY_DOMAIN
        and p.raw_field_path.startswith(REQUEST_ECHO_PREFIX)
        for p in contribution.provenance
    )


def _parts(url: str) -> SplitResult | None:
    """The URL's parts; ``None`` when it cannot be parsed (a malformed IPv6 host)."""
    try:
        return urlsplit(url if "://" in url else "//" + url)
    except ValueError:
        return None


def _split(url: str) -> tuple[str, str, str]:
    parts = _parts(url)
    if parts is None:
        return url, "", ""
    host = (parts.hostname or "").rstrip(".").casefold()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None and str(port) not in _DEFAULT_PORTS:
        host = f"{host}:{port}"
    return host, parts.path.rstrip("/"), parts.query


def dedupe_key(url: str) -> str:
    """The form two URLs share when they are the same result."""
    host, path, query = _split(url)
    return f"{host}{path}" + (f"?{query}" if query else "")


def url_host(url: str) -> str | None:
    """The registrable domain of the URL's host; ``None`` for an IP or none."""
    parts = _parts(url)
    host = None if parts is None else parts.hostname
    if not host:
        return None
    found = registrable_domains(host)
    return found[0] if found else None


def names_domain(text: str, domain: str) -> bool:
    """Whether ``text`` names ``domain`` as a whole domain (case-insensitive)."""
    pattern = (
        r"(?<![a-z0-9-])" + re.escape(domain.casefold()) + r"(?![a-z0-9-]|\.[a-z0-9])"
    )
    return re.search(pattern, text.casefold()) is not None


def attachment_of(
    url: str | None, texts: Iterable[str], domains: Sequence[str]
) -> Attachment:
    """How one result relates to the anchored company (``domains``)."""
    if url is None:
        return Attachment.UNATTACHED
    host = url_host(url)
    if host is None:
        return Attachment.UNATTACHED
    if host in domains:
        return Attachment.OWN_DOMAIN
    # The URL names the domain only past its host: userinfo (``acme.com@evil.io``)
    # is a lookalike, not a mention.
    parts = _parts(url)
    rest = "" if parts is None else urlunsplit(("", "", *parts[2:]))
    candidates = (rest, *texts)
    if any(names_domain(text, d) for text in candidates for d in domains):
        return Attachment.THIRD_PARTY_MENTION
    return Attachment.UNATTACHED


def agreeing_host(url: str | None, how: Attachment) -> str | None:
    """The host an attached result counts as; ``None`` when it does not agree.

    All of the company's own domains are one host; a third-party result is its
    registrable domain, so two subdomains of one site are one host.
    """
    if how is Attachment.UNATTACHED or url is None:
        return None
    if how is Attachment.OWN_DOMAIN:
        return _OWN_HOST
    return url_host(url)


def signal_strength(
    *, hosts: int, own_domain: bool, corroborating_sources: int
) -> float | None:
    """The provisional agreement scale (module docstring); ``None`` with no host."""
    if hosts < 1:
        return None
    level = 1
    if hosts >= _MEDIUM_HOSTS:
        level = 2
    if hosts >= _STRONG_HOSTS or (own_domain and hosts >= _MEDIUM_HOSTS):
        level = 3
    if corroborating_sources >= _CORROBORATING:
        level += 1
    return STRENGTH_LEVELS[min(level, len(STRENGTH_LEVELS)) - 1]
