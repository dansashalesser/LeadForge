"""A Search Plan as the in-memory Target Profile an ingestion run takes (1.2, 4.1, 4.2).

Nothing is written to disk. Terms come from the base profile
(built from the Product Catalog), so a term it does not hold is ``UnknownTermError``,
raised before any provider call. The source columns that take a domain or a phrase
are named by ``SourcesConfig``, never here.

* free text and users: the plan's terms, each with its base-profile vocabularies, and
  the base keyword templates, which are what gather Company Signals (4.2). An unmapped
  users plan has no base term: its company name becomes one term searched as a phrase.
* workers: one term for the company, carrying only a domain filter. No technology term
  and no keyword template, so no technology filter reaches a provider (3.1).
"""

import re

from leadforge.lead_ingestion.catalog import Catalog
from leadforge.lead_ingestion.target_profile import TargetProfile
from leadforge.outreach.config import SourcesConfig
from leadforge.outreach.search_plan import SearchPlan, check_terms

__all__ = ["KEYWORD_TEMPLATES", "catalog_base_profile", "plan_to_profile"]

# Search phrasings that gather Company Signals; "{term}" is the term key. Generic: no
# vendor or product is named here.
KEYWORD_TEMPLATES = (
    "{term} migration",
    "hiring {term} engineer",
    "{term} alternative",
)


def catalog_base_profile(catalog: Catalog, sources: SourcesConfig) -> TargetProfile:
    """Every product and ecosystem entry of every vendor, as the base profile.

    Per term, ``sources.domain_filter`` gets the technology UIDs and
    ``sources.phrase_search`` the alias texts (the columns ``to_profile`` writes).
    """
    technologies: dict[str, object] = {}
    for vendor in catalog.vendors():
        technologies.update(
            catalog.to_profile(
                vendor.key,
                uid_source=sources.domain_filter,
                alias_source=sources.phrase_search,
            ).technologies
        )
    return TargetProfile(
        technologies=technologies,  # type: ignore[arg-type]
        keyword_templates=KEYWORD_TEMPLATES,
    )


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
