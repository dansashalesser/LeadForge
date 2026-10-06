# Enrichment tiers feed later tiers (amends ADR-0002)

ADR-0002 built the Enrichment work list from Discovery output alone, and later tiers
could only prune it. In a real run that meant: people or addresses HubSpot or Hunter
added never reached Apollo; the company domain Apollo's match returned
(`organization.primary_domain`) never reached Google Search, which only asks about a
company it has a domain for, so Google made no call; and HubSpot and Hunter saw only
what Discovery produced. The user decided (2026-10-06) that sources should enhance each
other, that HubSpot and Hunter "have information we can cross reference or append to
the lead", that a Lead is a person, and that Hunter runs before Apollo so a Hunter 451
prunes the person before Apollo's paid match.

We keep ADR-0002's two phases and declaration-derived tiers, and amend one rule: when a
tier finishes, its contributions are appended to the work list before the next tier is
called. This is one bounded forward pass. Every tier is called at most once, nothing is
fed back to an earlier tier, and the order is deterministic (tier order, then source
name).

A new declaration, `evidence_only` (default `False`; Google Search sets it), sorts after
`yields_suppression` and before `charge_unit`. Before this change Google (paid,
per-call) ran ahead of Apollo (paid, per-lead) because fewer billable events ran first.
Neither source prunes for the other, so swapping them moves no spend, but Google can
only use Apollo's domain if it runs after Apollo. Shipped order: HubSpot, Hunter,
Apollo, Google Search.

## Considered Options

- **Re-run tiers until nothing changes.** Rejected. Spend would be unbounded and hard to
  predict, and a paid source could be asked twice in one run.
- **Swap the ranks of `per_call` and `per_lead`.** Rejected. That ordering rule is about
  how many billable events a source causes, not about what it needs to read, and the
  swap would move every per-call source, not only evidence sources.
- **Merge the records of one person into a single work-list item in the orchestrator.**
  Rejected. A contribution's provenance belongs to exactly one source, and the
  orchestrator does no merging (the merge stage runs after the run). Each adapter
  dedupes per person instead.

## Consequences

- Pruning is cumulative. Every suppression or opt-out reported so far prunes the list
  before each later tier, so a person suppressed early stays out even if a later tier's
  record adds them back under an address or LinkedIn URL the suppression named. The
  block is not transitive: a record naming the person only by another identity (a
  LinkedIn URL that an unflagged record links to the suppressed address) stays on the
  list. Suppression-yielding sources still run first within their cost class.
- Apollo asks once per person. Records sharing an address or LinkedIn identity get one
  ladder (strongest rung first) and one attachment, made on the strongest anchor any
  of them holds. A group that names two LinkedIn identities, or two distinct person
  names (a shared role address, 8.14), is treated as separate people, as before.
  Per-run caches still stop any lookup from being paid for twice.
- `per_company_work_list` drops a fed record that names no company when another record
  of the same person names one, instead of treating it as a new domainless Lead. A
  person's records never fuse two companies. Per-company dedupe holds on the fed list.
- Apollo's match maps `person.organization.primary_domain` to `company.domain` as one
  registrable domain under the pinned Public Suffix List. Webmail, bare suffixes and
  `localhost` are dropped. A weak (name-rung or name-anchor) hit never contributes
  Apollo's own domain, and the requester's echoed domain wins. The field name is
  UNVERIFIED: no captured response or quoted field table names it.
- Google's company corroboration ignores a domain that only echoes what the requester
  asked (raw path `asked.*`). Such a domain still creates an anchor.
- Known gaps: (1) A person found only by a later tier is never asked of an earlier one.
  HubSpot (tier 1) never sees an address Hunter or Apollo found, so a CRM opt-out for
  that address is applied only at merge, not before Apollo spends. (2) Hunter routes
  each record on its own. Two fed records of one person, one with an address and one
  with name and domain, would cost a verifier call and a finder call. No shipped tier
  before Hunter emits `person.email`, so this cannot happen today. (3) HubSpot's record
  names the address with no verification status and no name, so the merge (8.2: only
  verified addresses are keys) keeps it as a separate Lead, as it did before this
  change.
