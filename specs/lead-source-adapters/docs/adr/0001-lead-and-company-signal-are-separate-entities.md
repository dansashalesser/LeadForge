# Lead and Company Signal are separate entities

Several providers return company-level records with no person in them. The original
design modelled these as a `CanonicalLead` with a `lead_scope` attribute and four null
identity fields, which left those records unmatchable (every dedupe key requires person
identity) and pushed a scope branch onto every downstream stage. We split them: a
**Lead** is always one human, a **Company Signal** is company-level data, and an
**Employment** relates them. `lead_scope` is removed.

## Consequences

- Every Lead now has a usable identity key, so "never contact the same person twice" is
  enforceable rather than aspirational.
- Per-company billing becomes expressible. Technology enrichment and Hunter's domain
  search charge per company, not per lead; driving them off the Lead set would pay
  repeatedly for one company.
- Employment being its own relation is what lets a stale employer domain from one
  provider still match a historical employment from another.
