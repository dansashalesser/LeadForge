# Two-phase run with cost-ordered enrichment

Qualification is out of scope for this feature, so nothing was marking leads for
enrichment and the enrich work list was always empty — which left enrich-only adapters
idle and meant no lead ever reached a verified email, killing the strongest dedupe key
in the one demo a reviewer actually runs. We made the run explicitly two-phase:
**Discovery** runs every search-capable source, then **Enrichment** runs every
enrich-capable source over every Lead Discovery produced. The work list is mechanical,
not a qualification judgment, so the scope boundary holds.

Enrichment order is derived from three declared adapter attributes — `cost_class`,
`charge_unit`, `yields_suppression` — rather than a hand-maintained list, so adding a
source cannot silently land it in the wrong tier.

## Consequences

- Free suppression-bearing sources run first and suppressed leads leave the work list
  before any credit is spent. This is documented behaviour, not a heuristic: the only
  suppression-bearing providers are also the only free ones.
- `charge_unit` keeps per-company operations off the per-lead loop.
- Hunter is batched by domain: one domain search returns many addresses with
  verification status, where per-lead verification would pay repeatedly for the same
  answer.
- Residual cost that cannot be designed away: Hunter's 451 opt-out is only discoverable
  by spending a credit.

## Amendment

Amended by ADR-0006 (2026-10-06): each finished Enrichment tier's contributions now feed the work list of later tiers (one forward pass), and an `evidence_only` declaration orders Google Search after Apollo. The work list is no longer Discovery output only.
