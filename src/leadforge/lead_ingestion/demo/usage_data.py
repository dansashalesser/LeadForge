"""The usage part of the demo dataset: who is a user of the product, and who only looks
like one (user-recognition requirements 1.1, 1.2).

``extend`` adds twelve companies and twenty-four people to the provider tables and the
answer key, drawn from a random generator of their own so every record the base dataset
holds is untouched. Each company says what the world holds about it using the product
(``usage``): the true grade and the proof that exists, with dates. Each person's
``expect.usage`` follows from it.

Ten scenarios, two people each, are adversarial: none is a user (``is_user`` false).
The tenth, ``vendor_staff``, is DataStax's own staff, added by ``outreach_data`` since
DataStax is the vendor company it already builds.

* ``non_technical_role``: sales and marketing at a company that does use the product.
* ``migrated_away``: it used the product, then published that it moved off.
* ``cassandra_name``: "Cassandra" is a first name, on a page not about a database.
* ``technographic_only``: an Apollo tag and nothing else.
* ``injection_page``: the company page carries instructions aimed at the reader.
* ``stale_evidence``: a job posting from 2022 is the only proof.
* ``ecosystem_only``: Apache Cassandra without the vendor's product.
* ``left_company``: the company is a user and the role is right; the person has left.
* ``vendor_partner``: a consultancy whose pages praise the product.

``verified_user`` (6 people, 3 companies) is the positive side: a core role at a company
with two independent kinds of proof.
"""

import random

from leadforge.lead_ingestion.demo import generator as g

__all__ = ["USAGE_COMPANIES", "extend"]

_FRESH = "2026-08-14"
_RECENT = "2026-06-02"
_OLD = "2022-11-02"  # beyond the 730-day freshness window


def _record(cls: str, relationship: str, observed_on: str | None) -> g.Json:
    return {"class": cls, "observed_on": observed_on, "relationship": relationship}


def _usage(grade: str, reason: str, *records: g.Json) -> g.Json:
    return {"grade": grade, "reason": reason, "evidence": list(records)}


_TAG = _record("technographic", "uses_now", None)
_SALES = ("VP of Sales", "vp", "sales")
_MARKETING = ("Head of Marketing", "head", "marketing")
_CORE = (g._TITLES[1], g._TITLES[3])  # Head of Data Platform, Staff Database Engineer

# (scenario, company name, domain suffix, Apollo tags, web profile, usage, titles)
USAGE_COMPANIES: tuple[
    tuple[
        str, str, str, list[str], str, g.Json, tuple[tuple[str, str, str], ...] | None
    ],
    ...,
] = (
    (
        "non_technical_role",
        "Brackenridge Retail",
        "com",
        ["datastax"],
        "strong",
        _usage(
            "verified",
            "two_classes",
            _record("job_posting", "uses_now", _FRESH),
            _record("vendor_customer_ref", "uses_now", _RECENT),
            _TAG,
        ),
        (_SALES, _MARKETING),
    ),
    (
        "migrated_away",
        "Calloway Logistics",
        "com",
        ["cassandra"],
        "own_site",
        _usage(
            "negative",
            "used_past",
            _record("vendor_customer_ref", "uses_now", "2023-04-12"),
            _record("own_domain_content", "used_past", _RECENT),
            _TAG,
        ),
        _CORE,
    ),
    (
        "cassandra_name",
        "Dunmore Design",
        "io",
        ["cassandra"],
        "unrelated",
        _usage(
            "unverified",
            "technographic_only",
            _record("own_domain_content", "unrelated", _FRESH),
            _TAG,
        ),
        None,  # the people are named Cassandra
    ),
    (
        "technographic_only",
        "Eastgate Freight",
        "com",
        ["datastax"],
        "empty",
        _usage("unverified", "technographic_only", _TAG),
        _CORE,
    ),
    (
        "injection_page",
        "Fenwick Analytics",
        "io",
        ["cassandra"],
        "injection",
        _usage(
            "unverified",
            "no_evidence",
            _record("own_domain_content", "unrelated", _FRESH),
        ),
        _CORE,
    ),
    (
        "stale_evidence",
        "Garrowby Media",
        "com",
        ["datastax"],
        "own_site",
        _usage(
            "likely",
            "stale_evidence",
            _record("job_posting", "uses_now", _OLD),
            _TAG,
        ),
        _CORE,
    ),
    (
        "ecosystem_only",
        "Hollis Telecom",
        "com",
        ["cassandra"],
        "third_party",
        _usage(
            "likely",
            "ecosystem_only",
            _record("job_posting", "uses_now", _FRESH),
            _TAG,
        ),
        _CORE,
    ),
    (
        "left_company",
        "Ingleby Payments",
        "com",
        ["datastax"],
        "strong",
        _usage(
            "verified",
            "two_classes",
            _record("vendor_customer_ref", "uses_now", _RECENT),
            _record("code_dependency", "uses_now", _FRESH),
            _TAG,
        ),
        _CORE,
    ),
    (
        "vendor_partner",
        "Jessop Data Partners",
        "com",
        ["datastax", "cassandra"],
        "own_site",
        _usage(
            "unverified",
            "partner",
            _record("own_domain_content", "vendor_or_partner", _FRESH),
            _TAG,
        ),
        _CORE,
    ),
    *(
        (
            "verified_user",
            name,
            suffix,
            ["datastax"],
            "strong",
            _usage(
                "verified",
                "two_classes",
                _record("job_posting", "uses_now", _FRESH),
                _record("vendor_customer_ref", "uses_now", _RECENT),
                _TAG,
            ),
            _CORE,
        )
        for name, suffix in (
            ("Kestrel Health", "com"),
            ("Lindqvist Energy", "io"),
            ("Marlowe Gaming", "com"),
        )
    ),
)
_PER_COMPANY = 2


def extend(tables: dict[str, g.Json]) -> None:
    """Add the usage companies and their people to the tables and the key."""
    key = tables[g.ANSWER_KEY]
    rng = random.Random(f"{key['seed']}:usage")
    ids = g._Ids(rng)
    ids._hubspot = 80_000  # beyond the base dataset's HubSpot ids; none is used
    taken = {p["name"] for p in key["people"]}
    number = len(key["people"]) + 1
    first_company = len(key["companies"]) + 1
    companies: list[g.Company] = []
    people: list[g.Person] = []
    for index, spec in enumerate(USAGE_COMPANIES):
        scenario, name, suffix, techs, web, usage, titles = spec
        company = g.Company(
            key=f"c{first_company + index:02d}",
            name=name,
            domain=f"{g._slug(name)}.{suffix}",
            apollo_org_id=ids.apollo(),
            industry=rng.choice(g._INDUSTRIES),
            employees=rng.choice((120, 380, 850, 2400)),
            revenue=rng.choice((18_000_000, 62_000_000, 240_000_000)),
            city=rng.choice(g._CITIES),
            founded=rng.randint(1998, 2021),
            technologies=techs,
            web=web,
            pattern="{first}.{last}",
            accept_all=False,
            usage=usage,
        )
        companies.append(company)
        for seat in range(_PER_COMPANY):
            first, last = _name(rng, taken, cassandra=scenario == "cassandra_name")
            title, seniority, department = (
                titles[seat % len(titles)] if titles else rng.choice(g._TITLES)
            )
            slug = f"{g._slug(first)}-{g._slug(last)}-{ids.hex(4)}"
            people.append(
                g.Person(
                    subject=f"p{number:03d}",
                    scenario=scenario,
                    company=company,
                    first=first,
                    last=last,
                    title=title,
                    seniority=seniority,
                    department=department,
                    apollo_id=ids.apollo(),
                    linkedin=f"http://www.linkedin.com/in/{slug}",
                    email=g._address(company.pattern, first, last, company.domain),
                    email_status="verified",
                    uids=[techs[seat % len(techs)]],
                )
            )
            number += 1

    tables["apollo"]["records"].extend(g._apollo_table(people, ids)["records"])
    hunter = g._hunter_table(rng, companies, people)
    tables["hunter"]["domain_search"].update(hunter["domain_search"])
    tables["hunter"]["email_finder"].extend(hunter["email_finder"])
    tables["hunter"]["email_verifier"].update(hunter["email_verifier"])
    google = g._google_table(rng, companies)
    tables["google_search"]["by_domain"].update(google["by_domain"])

    for company in companies:
        key["companies"][company.domain] = {
            "name": company.name,
            "web_profile": company.web,
            "usage": company.usage,
        }
    for p in people:
        key["people"].append(
            {
                "subject": p.subject,
                "scenario": p.scenario,
                "name": f"{p.first} {p.last}",
                "company": p.company.domain,
                "apollo_id": p.apollo_id,
                "expect": g._expectation(p),
            }
        )


def _name(rng: random.Random, taken: set[str], *, cassandra: bool) -> tuple[str, str]:
    while True:
        first = "Cassandra" if cassandra else rng.choice(g._FIRST)
        last = rng.choice(g._LAST)
        if f"{first} {last}" not in taken:
            taken.add(f"{first} {last}")
            return first, last
