"""Company Signal clustering on a registrable-domain set (task 16.9; Requirement 8.16).

A pure function over Company Signals: no I/O, no globals, input not mutated. A company's
identity is the SET of its normalised registrable domains, and two records whose sets
overlap are one company (transitive closure). Union-find over the domains, as for Leads
(``clustering``), so the partition cannot depend on arrival order (8.8). Registrable
domains come from ``match_keys.registrable_domains``, the one normaliser: the Public
Suffix List snapshot bundled with the pinned tldextract, private suffixes included,
never fetched at run time. Provisional decisions (choices.md, 16.9):

* ``company_id`` is ``co-`` plus 16 hex of the sha256 of the sorted domain set joined by
  a unit separator. It depends on the set alone, never on a name or on arrival order. A
  cluster that later gains a domain gets a new id (the set is the identity).
* No usable domain (none given, a bare suffix, an IP, ``localhost``, or webmail only):
  the record is a singleton, never dropped and never merged by name alone. Its id hashes
  the record's canonical JSON; byte-identical records keep separate clusters, the later
  ones suffixed ``-2``, ``-3`` ... as ``clustering`` does. Rejected: keying on the name
  (two distinct companies called "Acme" would merge: the costly direction, ADR-0003).
* Webmail is not a company. Neither requirement nor ADR names it, so
  ``WEBMAIL_DOMAINS`` is a short explicit set of well-known free-mail registrable
  domains, dropped from every company's domain set: a record naming only gmail.com is
  a singleton and
  ``{acme.com, gmail.com}`` clusters on acme.com. Rejected: no guard (every freelancer
  with a gmail address would be one company, and a per-company Credit would be skipped
  for all but the first). A personal domain (a freelancer's own name.com) cannot be
  recognised and is treated as a company.
* The primary domain (8.17) is not chosen here and appears in no rule.
* Cluster members are in canonical-JSON order and clusters in ``company_id`` order.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from hashlib import sha256

from leadforge.lead_ingestion.clustering import _UnionFind, canonical_value_json
from leadforge.lead_ingestion.match_keys import registrable_domains
from leadforge.lead_ingestion.models import CompanySignal

__all__ = [
    "WEBMAIL_DOMAINS",
    "CompanyCluster",
    "cluster_company_signals",
    "company_domains",
    "company_id_for",
    "domain_components",
]

# Registrable domains of well-known free-mail providers (not exhaustive by design).
WEBMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "aol.com",
        "fastmail.com",
        "gmail.com",
        "googlemail.com",
        "gmx.com",
        "gmx.de",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "mail.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "proton.me",
        "protonmail.com",
        "qq.com",
        "yahoo.com",
        "yandex.com",
    }
)


@dataclass(frozen=True)
class CompanyCluster:
    """Company Signals judged to be one company, in canonical order."""

    company_id: str
    domains: tuple[str, ...] = field(repr=False)
    signals: tuple[CompanySignal, ...] = field(repr=False)


def company_domains(value: object) -> frozenset[str]:
    """The normalised registrable domains naming a company, webmail excluded.

    ``value`` is a ``company.domain`` value (text or a collection of text); anything
    else is a ``TypeError``. A name that has no registrable part is not in the set.
    """
    return frozenset(registrable_domains(value)) - WEBMAIL_DOMAINS


def company_id_for(domains: Iterable[str]) -> str:
    """The canonical id of the company whose registrable-domain set is ``domains``."""
    basis = "\x1f".join(sorted(set(domains)))
    return "co-" + sha256(basis.encode("utf-8")).hexdigest()[:16]


def domain_components(domain_sets: Sequence[frozenset[str]]) -> tuple[int, ...]:
    """Label each set with the lowest index of its component.

    Sets sharing a domain (transitively) share a label; an empty set is its own
    component. The labels are the connected components of the domain graph, so they do
    not depend on the order of the sets, only on which sets there are.
    """
    forest = _UnionFind(len(domain_sets))
    first_holder: dict[str, int] = {}
    for index, domains in enumerate(domain_sets):
        for domain in domains:
            forest.union(first_holder.setdefault(domain, index), index)
    lowest: dict[int, int] = {}
    for index in range(len(domain_sets)):  # ascending, so the first seen is lowest
        lowest.setdefault(forest.find(index), index)
    return tuple(lowest[forest.find(i)] for i in range(len(domain_sets)))


def cluster_company_signals(
    signals: Iterable[CompanySignal],
) -> tuple[CompanyCluster, ...]:
    """Cluster ``signals`` by registrable-domain overlap; the result ignores order."""
    items = sorted(
        ((canonical_value_json(s), s) for s in signals), key=lambda pair: pair[0]
    )
    sets = [company_domains(s.domains) for _, s in items]
    labels = domain_components(sets)

    groups: dict[int, list[int]] = defaultdict(list)
    for index, label in enumerate(labels):
        groups[label].append(index)

    seen: dict[str, int] = {}
    clusters: list[CompanyCluster] = []
    for group in groups.values():  # ascending labels, so canonical-JSON order
        domains = frozenset().union(*(sets[i] for i in group))
        if domains:
            company_id = company_id_for(domains)
        else:
            # A singleton: one record, keyed by its own canonical JSON.
            base = "co-" + sha256(items[group[0]][0].encode("utf-8")).hexdigest()[:16]
            seen[base] = seen.get(base, 0) + 1
            company_id = base if seen[base] == 1 else f"{base}-{seen[base]}"
        clusters.append(
            CompanyCluster(
                company_id,
                tuple(sorted(domains)),
                tuple(items[i][1] for i in group),
            )
        )
    return tuple(sorted(clusters, key=lambda c: c.company_id))
