"""A Search Plan as the in-memory Target Profile an ingestion run takes (1.2, 4.1, 4.2).

Nothing is written to disk. Terms come from the base profile
(``config/target_profile.yaml``), so a term it does not hold is ``UnknownTermError``,
raised before any provider call. The source columns that take a domain or a phrase
are named by ``SourcesConfig``, never here.

* free text and users: the plan's terms, each with its base-profile vocabularies, and
  the base keyword templates, which are what gather Company Signals (4.2). An unmapped
  users plan has no base term: its company name becomes one term searched as a phrase.
* workers: one term for the company, carrying only a domain filter. No technology term
  and no keyword template, so no technology filter reaches a provider (3.1).
"""

import re

from leadforge.lead_ingestion.target_profile import TargetProfile
from leadforge.outreach.config import SourcesConfig
from leadforge.outreach.search_plan import SearchPlan, check_terms

__all__ = ["plan_to_profile"]


def plan_to_profile(
    plan: SearchPlan, base: TargetProfile, sources: SourcesConfig
) -> TargetProfile:
    check_terms(plan, base.terms())
    if plan.mode == "workers":
        return TargetProfile(
            technologies={
                _key(plan.company or plan.query): {
                    sources.domain_filter: {sources.domain_key: list(plan.domains)}
                }
            }
        )
    if plan.unmapped:
        name = plan.company or plan.query
        return TargetProfile(
            technologies={_key(name): {sources.phrase_search: [name]}},
            keyword_templates=base.keyword_templates,
        )
    return TargetProfile(
        technologies={
            t: base.technologies[t] for t in plan.terms if t in base.technologies
        },
        competitors={
            t: base.competitors[t] for t in plan.terms if t in base.competitors
        },
        keyword_templates=base.keyword_templates,
    )


def _key(name: str) -> str:
    """A company name as a term key: lower case, runs of other characters as ``_``."""
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or "company"
