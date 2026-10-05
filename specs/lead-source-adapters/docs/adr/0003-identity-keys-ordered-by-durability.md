# Identity keys ordered by durability, not contactability

The original key order was verified email, then LinkedIn URL, then name plus company
domain. Two of those three are employer-coupled: a work email and a company domain both
change when someone changes jobs, so the same human splits into two Leads. LinkedIn URL
is the only identifier that survives a job change. We reordered: **LinkedIn URL,
verified email, then name plus any employment domain with a second agreeing attribute**.

Key three widened from the current employer to any known employment, current or
historical, which closes the stale-domain split. Because widening a key raises
over-merge risk and no unmerge exists, the same change adds a corroboration gate.

## Consequences

- Over-merge has no repair by rule change alone. A rule edit is global and cannot undo
  one bad cluster, so the per-value **Identity Exclusion** list is the actual unmerge
  mechanism and is specified as a requirement rather than left as a mitigation note.
- Verified-email-only keying was adopted against catch-all domains but does not stop
  role addresses, which verify as deliverable. An address any provider reports against
  two or more distinct person names is structurally disqualified as a key — this needs
  no curated vocabulary of role words and so does not break on non-English domains.
- An over-merge detector flags clusters carrying conflicting person names on the run
  report. It does not block. Since no unmerge exists, visibility is the whole defence.
