"""The outreach part of the demo dataset: DataStax workers, and what each person should
become (hunter-outreach requirements 15.1, 15.2).

``extend`` adds one company, DataStax, and ten of its people to the four provider tables
and to the answer key, drawn from a random generator of their own so the 242 people the
base dataset already holds are untouched. Then ``outreach_expectation`` gives every
person in the key, old and new, an ``outreach`` entry: the Decision the pipeline should
reach, whether the invite is accepted (and after how many days), and the sequence that
follows. The scorecard in ``leadforge.outreach`` compares stored Decisions with it.

The ten workers cover every outcome a search can reach:

* ``worker_accepts`` (3): selected, invite accepted after 1, 2 and 3 days, then email.
* ``worker_ignores`` (2): selected, invite ignored, Verified Email, so a fallback email.
* ``worker_ignores_no_email`` (1): selected, ignored, no email at all, so stalled.
* ``worker_no_linkedin`` (2): an email and no LinkedIn URL, so needs_enrichment.
* ``worker_opted_out`` (1) and ``worker_customer`` (1): rejected from the CRM.
"""

import random

from leadforge.lead_ingestion.demo import generator as g

__all__ = [
    "TARGET_DOMAIN",
    "WORKER_NOTES",
    "WORKER_SCENARIOS",
    "extend",
    "outreach_expectation",
]

TARGET_DOMAIN = "datastax.com"
WORKER_SCENARIOS: tuple[tuple[str, int], ...] = (
    ("worker_accepts", 3),
    ("worker_ignores", 2),
    ("worker_ignores_no_email", 1),
    ("worker_no_linkedin", 2),
    ("worker_opted_out", 1),
    ("worker_customer", 1),
)
_ACCEPT_DAYS = (1, 2, 3)
_REJECTED = frozenset(
    {
        "hubspot_opted_out",
        "hubspot_duplicate_contacts",
        "hubspot_open_deal",
        "hubspot_customer",
        "hunter_claimed_email",
        "worker_opted_out",
        "worker_customer",
    }
)
WORKER_NOTES = {
    "worker_accepts": "A DataStax worker; the invite is accepted, then the email fires",
    "worker_ignores": "A DataStax worker; ignored invite, a Verified Email falls back",
    "worker_ignores_no_email": "A DataStax worker with no email; ignored, so stalled",
    "worker_no_linkedin": "A DataStax worker with an email and no LinkedIn URL",
    "worker_opted_out": "A DataStax worker who opted out in the CRM",
    "worker_customer": "A DataStax worker who is already a customer in the CRM",
}


def extend(tables: dict[str, g.Json]) -> None:
    """Add the DataStax workers to the tables and the key; add ``outreach`` entries."""
    key = tables[g.ANSWER_KEY]
    rng = random.Random(f"{key['seed']}:outreach")
    ids = g._Ids(rng)
    ids._hubspot = 90_000  # beyond the base dataset's HubSpot ids
    company = g.Company(
        key="c41",
        name="DataStax",
        domain=TARGET_DOMAIN,
        apollo_org_id=ids.apollo(),
        industry="computer software",
        employees=900,
        revenue=240_000_000,
        city=("Santa Clara", "California", "United States"),
        founded=2010,
        technologies=["datastax", "cassandra"],
        web="own_site",
        pattern="{first}.{last}",
        accept_all=False,
    )
    taken = {p["name"] for p in key["people"]}
    people = _workers(rng, ids, company, taken, start=len(key["people"]) + 1)

    apollo = g._apollo_table(people, ids)
    for record in apollo["records"]:
        record["domain"] = TARGET_DOMAIN
    tables["apollo"]["records"].extend(apollo["records"])

    hunter = g._hunter_table(rng, [company], people)
    tables["hunter"]["domain_search"].update(hunter["domain_search"])
    tables["hunter"]["email_finder"].extend(hunter["email_finder"])
    tables["hunter"]["email_verifier"].update(hunter["email_verifier"])

    google = g._google_table(rng, [company])
    tables["google_search"]["by_domain"].update(google["by_domain"])

    contacts = tables["hubspot"]["contacts"]
    for p in people:
        if p.scenario in ("worker_opted_out", "worker_customer") and p.email:
            contacts.append(
                g._contact(
                    rng,
                    ids,
                    email=p.email,
                    first=p.first,
                    last=p.last,
                    title=p.title,
                    company=company.name,
                    website=company.domain,
                    lifecycle="customer" if p.scenario == "worker_customer" else "lead",
                    optout=p.scenario == "worker_opted_out",
                )
            )

    key["companies"][company.domain] = {"name": company.name, "web_profile": "own_site"}
    key["scenarios"] = {**key["scenarios"], **WORKER_NOTES}
    for p in people:
        entry = {
            "subject": p.subject,
            "scenario": p.scenario,
            "name": f"{p.first} {p.last}",
            "company": company.domain,
            "apollo_id": p.apollo_id,
            "expect": _worker_expectation(p),
        }
        key["people"].append(entry)
    days = iter(_ACCEPT_DAYS)
    for entry in key["people"]:
        entry["outreach"] = outreach_expectation(
            entry["scenario"],
            entry["expect"],
            next(days) if entry["scenario"] == "worker_accepts" else None,
        )


def _workers(
    rng: random.Random,
    ids: g._Ids,
    company: g.Company,
    taken_names: set[str],
    *,
    start: int,
) -> list[g.Person]:
    people: list[g.Person] = []
    number = start
    for scenario, count in WORKER_SCENARIOS:
        for _ in range(count):
            while True:
                first, last = rng.choice(g._FIRST), rng.choice(g._LAST)
                if f"{first} {last}" not in taken_names:
                    taken_names.add(f"{first} {last}")
                    break
            title, seniority, department = rng.choice(g._TITLES)
            slug = f"{g._slug(first)}-{g._slug(last)}-{ids.hex(4)}"
            person = g.Person(
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
                uids=["datastax"],
            )
            if scenario == "worker_ignores_no_email":
                person.email, person.email_status = None, "unavailable"
            if scenario == "worker_no_linkedin":
                person.linkedin = None  # type: ignore[assignment]
            if scenario in ("worker_opted_out", "worker_customer"):
                person.hubspot = {"scenario": scenario}
            people.append(person)
            number += 1
    return people


def _worker_expectation(p: g.Person) -> g.Json:
    expect = g._expectation(p)
    expect["linkedin_url"] = p.linkedin
    expect["opt_out"] = p.scenario == "worker_opted_out"
    expect["suppressed"] = p.scenario == "worker_opted_out"
    return expect


def outreach_expectation(
    scenario: str, expect: g.Json, accepts_after_days: int | None
) -> g.Json:
    """What a search should do with this person, given the lead they should have."""
    if expect.get("lead") == "absent":
        return {"decision": "absent"}
    if scenario in _REJECTED:
        return {"decision": "rejected", "accepts_after_days": None}
    if scenario == "worker_no_linkedin":
        return {"decision": "needs_enrichment", "accepts_after_days": None}
    usable = (
        bool(expect.get("email"))
        and expect.get("email_status") == "verified"
        and not expect.get("email_is_role_address")
    )
    if accepts_after_days is not None:
        sequence = "email"
    else:
        sequence = "fallback_email" if usable else "stalled"
    return {
        "decision": "selected",
        "accepts_after_days": accepts_after_days,
        "sequence": sequence,
    }
