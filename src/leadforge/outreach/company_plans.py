"""Search Plans for the two company modes (requirements 3.1, 3.3, 4.1, 4.2).

Both are rule-based lookups in ``CompanyTerms``, so their plans carry compiler
``offline``: no model is asked.

* workers: the people at a company, searched by the company's domains. A domain given
  in the request wins over the file; with neither, ``MissingDomainError`` stops the
  search before ingestion.
* users: the people and companies that use a company's product, searched by the
  company's term keys. A company with no entry is searched by its own name and the plan
  is flagged ``unmapped``.
"""

from leadforge.outreach.company_terms import CompanyTerms
from leadforge.outreach.errors import MissingDomainError
from leadforge.outreach.search_plan import SearchPlan, SearchRequest

__all__ = ["users_plan", "workers_plan"]


def workers_plan(request: SearchRequest, companies: CompanyTerms) -> SearchPlan:
    entry = companies.entry(request.query)
    domains = request.domains or (entry.domains if entry is not None else ())
    if not domains:
        raise MissingDomainError(request.query)
    return SearchPlan(
        mode="workers",
        query=request.query,
        company=request.query,
        domains=tuple(dict.fromkeys(domains)),
        compiler="offline",
    )


def users_plan(request: SearchRequest, companies: CompanyTerms) -> SearchPlan:
    entry = companies.entry(request.query)
    mapped = entry is not None and bool(entry.terms)
    return SearchPlan(
        mode="users",
        query=request.query,
        company=request.query,
        terms=entry.terms if entry is not None and mapped else (),
        compiler="offline",
        unmapped=not mapped,
    )
