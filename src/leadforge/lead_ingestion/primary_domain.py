"""Display-only primary domain of a company, by trust-weighted vote (task 16.10; 8.17).

A pure function over one ``CompanyCluster`` and a Source Trust Rank mapping: no I/O, no
globals, no model call, inputs not mutated. The primary domain is for display only and
appears in no match rule: nothing in ``clustering``, ``match_keys``, ``companies``,
``projection`` or the orchestrator's dedupe imports or accepts it, so electing a
different one cannot change a cluster, a key, a ``company_id`` or a dedupe outcome.
Provisional decisions (choices.md, 16.10):

* A vote is one source naming one registrable domain. The source is each
  ``provider_ids`` source of the Company Signal that names the domain; a Signal with no
  ``provider_ids`` is cast by one shared undeclared voter. Domains are read through
  ``companies.company_domains``, so webmail casts no vote and ``www.a.com`` votes for
  ``a.com``.
* A source votes for a domain once, however many of its records repeat it; it may vote
  for several domains.
* Weight is ``rank - LOWEST_TRUST_RANK + 1``: a higher rank weighs more, and the lowest
  rank (and a source missing from the mapping, as in ``conflicts``) still weighs 1, so
  a cluster whose sources are all unranked is decided by how many sources name a
  domain instead of tying on all zeros. Ranks must be ints, not below the lowest.
* The greatest total weight wins. An exact tie is reported as data (``tied`` and the
  tied ``tied_domains``, sorted) for the constrained resolution of 8.18 (task 16.11,
  not built here); the provisional winner is the lowest-sorted tied domain, which is
  what 8.18 prescribes in synthetic mode, so a run never blocks. The result is a
  total, order-independent function of the set of votes.
* A cluster with no usable domain has no primary domain (``None``), not tied.
* Signal Strength and names are never read (Requirement 24.4).
* A domain is personal data when it is a person's own: it is hidden from ``repr``.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from leadforge.lead_ingestion.companies import CompanyCluster, company_domains
from leadforge.lead_ingestion.conflicts import validate_trust_ranks
from leadforge.lead_ingestion.registry import LOWEST_TRUST_RANK

__all__ = ["PrimaryDomain", "elect_primary_domain"]

_UNDECLARED_VOTER = None  # the one voter for a Signal that names no source


@dataclass(frozen=True)
class PrimaryDomain:
    """The elected domain; ``tied_domains`` is non-empty only on an exact tie."""

    domain: str | None = field(repr=False)
    tied: bool = False
    tied_domains: tuple[str, ...] = field(default=(), repr=False)


def elect_primary_domain(
    cluster: CompanyCluster, trust_ranks: Mapping[str, int]
) -> PrimaryDomain:
    """Elect the display domain of ``cluster``; the result ignores signal order."""
    validate_trust_ranks(trust_ranks)

    votes: set[tuple[str | None, str]] = set()
    for signal in cluster.signals:
        domains = company_domains(signal.domains)
        if not domains:
            continue
        voters: set[str | None] = {p.source for p in signal.provider_ids} or {
            _UNDECLARED_VOTER
        }
        votes.update((voter, domain) for voter in voters for domain in domains)

    weights: dict[str, int] = defaultdict(int)
    for voter, domain in votes:
        rank = LOWEST_TRUST_RANK
        if voter is not _UNDECLARED_VOTER:
            rank = trust_ranks.get(voter, LOWEST_TRUST_RANK)
        weights[domain] += rank - LOWEST_TRUST_RANK + 1

    if not weights:
        return PrimaryDomain(None)
    best = max(weights.values())
    leaders = tuple(sorted(d for d, w in weights.items() if w == best))
    if len(leaders) == 1:
        return PrimaryDomain(leaders[0])
    return PrimaryDomain(leaders[0], True, leaders)
