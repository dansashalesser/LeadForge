"""Query families for finding company evidence pages (Req 4.1, 9.2).

One query per family, strongest first, with the product aliases OR-joined. The
shapes are the ones the demo transport routes on.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = ["FAMILY_ORDER", "Candidate", "QuerySpec", "anchored_candidates", "families"]

FAMILY_ORDER = (
    "vendor_customer",
    "job_posting",
    "code",
    "own_site",
    "third_party",
    "linkedin_public",
)
_ATS_SITES = (
    "boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
)
_LINKEDIN_PATHS = ("in", "company", "posts")


@dataclass(frozen=True)
class QuerySpec:
    family: str
    query: str


@dataclass(frozen=True)
class Candidate:
    """A result worth reading, before any fetch."""

    family: str
    url: str
    title: str
    snippet: str


def families(
    company: str,
    aliases: Sequence[str],
    vendor_domain: str,
    company_domain: str,
) -> list[QuerySpec]:
    """The family queries for one company and product, in Req 4.1 order."""
    cleaned = [a.strip() for a in aliases if a.strip()]
    if not company.strip() or not cleaned:
        raise ValueError("company and at least one alias are required")
    if not vendor_domain or not company_domain:
        raise ValueError("vendor_domain and company_domain are required")
    terms = f'"{company.strip()}" (' + " OR ".join(f'"{a}"' for a in cleaned) + ")"
    job_sites = [f"site:{h}" for h in _ATS_SITES] + [f"site:{company_domain}/careers"]
    linkedin = [f"site:linkedin.com/{p}" for p in _LINKEDIN_PATHS]
    scopes = {
        "vendor_customer": f"site:{vendor_domain}",
        "job_posting": "(" + " OR ".join(job_sites) + ")",
        "code": "site:github.com",
        "own_site": f"site:{company_domain}",
        "third_party": "",
        "linkedin_public": "(" + " OR ".join(linkedin) + ")",
    }
    return [QuerySpec(f, f"{scopes[f]} {terms}".strip()) for f in FAMILY_ORDER]


def anchored_candidates(results: Iterable[Mapping[str, str]]) -> list[Candidate]:
    """Ingestion's anchored results, reused as ``third_party`` candidates at no cost."""
    out: list[Candidate] = []
    for r in results:
        url = r.get("link") or r.get("url") or ""
        if url:
            out.append(
                Candidate("third_party", url, r.get("title", ""), r.get("snippet", ""))
            )
    return out
