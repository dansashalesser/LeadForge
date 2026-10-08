"""The proof pages of the demo dataset (user-recognition requirements 1.2, 4.1, 4.2).

``extend`` reads the answer key and, for every evidence record a company has, writes the
page that says it and the SERP result that points to it. Nothing here is random: each
page follows from the record (class, date, relationship), so the key and the web agree
by construction and the usage classifier can later be scored against the key.

Two tables result:

* ``google_search.families``: per company, the organic results of each query family
  (Req 4.1 order). A family with no proof is absent, which the transport answers as an
  empty search. Every company gets a ``linkedin_public`` family of snippets;
  linkedin.com is never fetched, so no page is stored for it.
* ``pages``: page bodies by URL, plus the one ``robots_txt`` every host serves, with a
  single disallowed path (``/private/``). One page sits under it, so a fetcher has a
  refusal to record and a snippet to classify from instead (Req 4.3).

A person who left a company is observable from a LinkedIn snippet ("Former ..."), since
no provider table holds employment history for the demo people.
"""

from leadforge.lead_ingestion.demo import generator as g

__all__ = ["FAMILIES", "ROBOTS_TXT", "extend"]

# Req 4.1 order, strongest first.
FAMILIES = (
    "vendor_customer",
    "job_posting",
    "code",
    "own_site",
    "third_party",
    "linkedin_public",
)
ROBOTS_TXT = "User-agent: *\nDisallow: /private/\n"
_FAMILY_OF_CLASS = {
    "vendor_customer_ref": "vendor_customer",
    "job_posting": "job_posting",
    "code_dependency": "code",
    "own_domain_content": "own_site",
}
_VENDOR_HOST = "www.datastax.com"
_ECOSYSTEM = "Apache Cassandra"
_PRODUCT = "DataStax Astra DB"

Page = tuple[str, str, str, str, str | None]  # url, title, snippet, body, date


def _html(title: str, paragraphs: list[str]) -> str:
    body = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        "<!doctype html>\n<html><head><title>"
        f"{title}</title></head>\n<body>\n<h1>{title}</h1>\n{body}\n</body></html>\n"
    )


def _product(usage: g.Json) -> str:
    return _ECOSYSTEM if usage["reason"] == "ecosystem_only" else _PRODUCT


def _vendor_customer(name: str, slug: str, date: str, rel: str, usage: g.Json) -> Page:
    url = f"https://{_VENDOR_HOST}/customers/{slug}"
    title = f"{name} | DataStax customer story"
    line = f"{name} customer platform is powered by {_PRODUCT}."
    return (
        url,
        title,
        line,
        _html(
            title,
            [
                f"Published <time datetime='{date}'>{date}</time>.",
                line,
                f"{name} chose DataStax Enterprise and {_PRODUCT} for always-on "
                "workloads.",
            ],
        ),
        date,
    )


def _job_posting(name: str, slug: str, date: str, rel: str, usage: g.Json) -> Page:
    role, product = "Staff Database Engineer", _product(usage)
    url = f"https://boards.greenhouse.io/{slug}/jobs/{4_000_000 + len(slug)}"
    title = f"{role} - {name}"
    line = f"{name} is hiring a {role}. You will run {product} in production."
    return (
        url,
        title,
        line,
        _html(
            title,
            [
                f"Posted <time datetime='{date}'>{date}</time>.",
                line,
                f"Requirements: operating {product} clusters, data modelling, on-call.",
            ],
        ),
        date,
    )


def _code(name: str, slug: str, date: str, rel: str, usage: g.Json) -> Page:
    url = f"https://github.com/{slug}/platform/blob/main/pom.xml"
    title = f"{slug}/platform: pom.xml"
    line = "com.datastax.oss:java-driver-core 4.17.0"
    return (
        url,
        title,
        f"{name} platform service. Dependency {line}",
        _html(
            title,
            [
                f"Platform service of {name}, last commit "
                f"<time datetime='{date}'>{date}</time>.",
                f"&lt;dependency&gt; {line} &lt;/dependency&gt;",
            ],
        ),
        date,
    )


def _own_content(
    name: str,
    domain: str,
    slug: str,
    date: str,
    rel: str,
    usage: g.Json,
    people: list[str],
) -> Page:
    when = f"Published <time datetime='{date}'>{date}</time>."
    if rel == "used_past":
        path, title = "/blog/moving-off-our-old-database", f"How {name} moved on"
        line = f"{name} used DataStax Enterprise for four years and no longer does."
        body = [when, line, "We migrated away in 2026; the old cluster is retired."]
    elif rel == "vendor_or_partner" and usage["reason"] == "vendor":
        path, title = "/company/about", "About DataStax"
        line = f"{name} builds {_PRODUCT}, the database for AI applications."
        body = [when, line, "Our customers run it in every industry."]
    elif rel == "vendor_or_partner":
        path, title = "/partners/datastax", f"{name} is a DataStax partner"
        line = f"{name} is a DataStax partner and reseller, not a user of it."
        body = [when, line, f"Our consultants help clients adopt {_PRODUCT}."]
    elif people:  # unrelated: a team page naming people called Cassandra
        path, title = "/about/team", f"Meet the {name} team"
        line = f"Meet {' and '.join(people)}, designers at {name}."
        body = [when, line, "We make brand and product design for small teams."]
    elif usage["reason"] == "no_evidence":  # unrelated, carrying an instruction
        path, title = "/blog/our-data-platform", f"Notes from {name}"
        line = f"Notes on dashboards and Cassandra. {g._INJECTION}"
        body = [when, line, "Dashboards, pipelines and the odd myth."]
    else:
        raise ValueError(f"no own-site page for {name}: {rel}, {usage['reason']}")
    return f"https://www.{domain}{path}", title, line, _html(title, body), date


def _linkedin_company(name: str, slug: str, company: g.Json) -> g.Json:
    return {
        "title": f"{name} | LinkedIn",
        "link": f"https://www.linkedin.com/company/{slug}",
        "snippet": f"{name} | Company page on LinkedIn. Followers, jobs and updates.",
    }


def _result(
    position: int, title: str, link: str, snippet: str, date: str | None = None
) -> g.Json:
    host = link.replace("https://", "").split("/")[0]
    return {
        "position": position,
        "title": title,
        "link": link,
        "snippet": snippet,
        "date": date,
        "redirect_link": f"https://www.google.com/url?q={link}",
        "displayed_link": host,
        "favicon": None,
        "snippet_highlighted_words": [],
        "source": host,
    }


def extend(tables: dict[str, g.Json]) -> None:
    """Add the families to the Google table and build the ``pages`` table."""
    key = tables[g.ANSWER_KEY]
    pages: dict[str, str] = {}
    entries: dict[str, g.Json] = {}
    for domain, company in key["companies"].items():
        name, usage = company["name"], company["usage"]
        slug = g._slug(name)
        people = [p for p in key["people"] if p["company"] == domain]
        by_family: dict[str, list[Page]] = {}
        for record in usage["evidence"]:
            cls, date, rel = (
                record["class"],
                record["observed_on"],
                record["relationship"],
            )
            if cls not in _FAMILY_OF_CLASS:
                continue  # technographic: Apollo's tag, no page
            if cls == "vendor_customer_ref":
                page = _vendor_customer(name, slug, date, rel, usage)
            elif cls == "job_posting":
                page = _job_posting(name, slug, date, rel, usage)
            elif cls == "code_dependency":
                page = _code(name, slug, date, rel, usage)
            else:
                named = [p["name"] for p in people if p["scenario"] == "cassandra_name"]
                page = _own_content(name, domain, slug, date, rel, usage, named)
            by_family.setdefault(_FAMILY_OF_CLASS[cls], []).append(page)
        results: dict[str, list[g.Json]] = {}
        for family in FAMILIES:
            if family == "linkedin_public":
                continue
            chosen = by_family.get(family, [])
            if domain == "eastgatefreight.com" and family == "third_party":
                chosen = [_private_page(name)]
            pages.update({page[0]: page[3] for page in chosen})
            if chosen:
                results[family] = [
                    _result(i + 1, title, url, snippet, date)
                    for i, (url, title, snippet, _, date) in enumerate(chosen)
                ]
        results["linkedin_public"] = _linkedin(name, slug, domain, people)
        entries[domain] = {"name": name, "results": results}
    tables["google_search"]["families"] = entries
    tables["pages"] = {
        "provider": "demo_pages",
        "generated_on": g.GENERATED_ON,
        "robots_txt": ROBOTS_TXT,
        "pages": dict(sorted(pages.items())),
    }


def _private_page(name: str) -> Page:
    """A page that names a product but sits under the disallowed path and is about no
    company in particular: a fetcher must skip it, and classifying its snippet alone
    must not make anyone a user."""
    url = "https://stackradar.io/private/wide-column-buyers-guide"
    title = "Buyer's guide to wide-column databases"
    line = "Compare DataStax Astra DB and ScyllaDB pricing for wide-column workloads."
    return url, title, line, _html(title, [line, "Pricing tables follow."]), None


def _linkedin(name: str, slug: str, domain: str, people: list[g.Json]) -> list[g.Json]:
    found = [_linkedin_company(name, slug, {})]
    for person in people:
        if person["scenario"] != "left_company":
            continue
        first, last = person["name"].lower().split(" ", 1)
        found.append(
            {
                "title": f"{person['name']} - Former engineer | LinkedIn",
                "link": f"https://www.linkedin.com/in/{g._slug(first)}-{g._slug(last)}",
                "snippet": f"{person['name']} - Former Data Platform lead at {name}. "
                f"Left {name} in 2026 and now works elsewhere. | LinkedIn",
            }
        )
    return [
        _result(i + 1, r["title"], r["link"], r["snippet"]) for i, r in enumerate(found)
    ]
