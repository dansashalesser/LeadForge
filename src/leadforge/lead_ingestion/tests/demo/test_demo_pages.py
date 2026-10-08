"""The demo's proof pages: SERP answers per query family, page bodies, robots.txt.

The synthetic usage flow searches one query per family for each company and fetches the
pages whose snippet names a product. These tests pin that the demo world can answer it:
every URL a SERP answer returns has a body, every evidence record the answer key holds
has a page that says it, and the fetch rules (robots, linkedin) have something to hit.
"""

import json
import re
from urllib.robotparser import RobotFileParser

import pytest

from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
from leadforge.lead_ingestion.demo import generator
from leadforge.lead_ingestion.demo.transport import (
    DemoLog,
    DemoPages,
    DemoTransport,
    _Tables,
)

FAMILIES = (
    "vendor_customer",
    "job_posting",
    "code",
    "own_site",
    "third_party",
    "linkedin_public",
)
_CLASS_FAMILY = {
    "vendor_customer_ref": "vendor_customer",
    "job_posting": "job_posting",
    "code_dependency": "code",
    "own_domain_content": "own_site",
}


@pytest.fixture(scope="module")
def key() -> generator.Json:
    return generator.load(generator.ANSWER_KEY)


@pytest.fixture(scope="module")
def serp() -> generator.Json:
    return generator.load("google_search")["families"]


@pytest.fixture(scope="module")
def pages() -> generator.Json:
    return generator.load("pages")


def _urls(serp: generator.Json, domain: str) -> dict[str, list[str]]:
    return {
        family: [r["link"] for r in results]
        for family, results in serp[domain]["results"].items()
    }


async def _search(q: str) -> generator.Json:
    transport = DemoTransport(
        GoogleSearchSource.name,
        GoogleSearchSource.endpoints,
        tables=_Tables(generator.DATA_DIR),
        log=DemoLog(),
    )
    answer = await transport.send(
        GoogleSearchSource.endpoints["search"],
        params={"q": q, "num": 10},
        json_body=None,
        headers={},
    )
    assert isinstance(answer.body, dict)
    return answer.body


# Verifies: specs/user-recognition/requirements.md#4.2
def test_every_url_a_serp_answer_returns_has_a_page_body(
    serp: generator.Json, pages: generator.Json
) -> None:
    returned = {
        r["link"]
        for entry in serp.values()
        for results in entry["results"].values()
        for r in results
    }
    fetchable = {u for u in returned if "linkedin.com" not in u}
    assert fetchable
    assert fetchable <= set(pages["pages"])  # nothing dangling
    assert not {u for u in pages["pages"] if "linkedin.com" in u}


# Verifies: specs/user-recognition/requirements.md#1.2
def test_every_evidence_record_has_a_page_that_says_it(
    key: generator.Json, serp: generator.Json, pages: generator.Json
) -> None:
    checked = 0
    for domain, company in key["companies"].items():
        dated = [e for e in company["usage"]["evidence"] if e["class"] in _CLASS_FAMILY]
        for record in dated:
            family = _CLASS_FAMILY[record["class"]]
            hits = [
                r
                for r in serp[domain]["results"].get(family, [])
                if record["observed_on"] in pages["pages"][r["link"]]
            ]
            assert hits, (domain, record)
            checked += 1
    assert checked >= 18


# Verifies: specs/user-recognition/requirements.md#4.1
def test_the_serp_answers_every_family_that_has_proof_and_linkedin_for_all(
    key: generator.Json, serp: generator.Json
) -> None:
    assert set(serp) == set(key["companies"])
    for domain in key["companies"]:
        assert "linkedin_public" in serp[domain]["results"], domain
        assert set(serp[domain]["results"]) <= set(FAMILIES)
        for r in serp[domain]["results"]["linkedin_public"]:
            assert "linkedin.com/" in r["link"]
    ingleby = _urls(serp, "inglebypayments.com")
    assert set(ingleby) == {"vendor_customer", "code", "linkedin_public"}


# Verifies: specs/user-recognition/requirements.md#1.2
def test_a_person_who_left_is_observable_from_a_linkedin_snippet(
    key: generator.Json, serp: generator.Json
) -> None:
    left = [p for p in key["people"] if p["scenario"] == "left_company"]
    assert len(left) == 2
    for person in left:
        snippets = [
            r["snippet"]
            for r in serp[person["company"]]["results"]["linkedin_public"]
            if person["name"] in r["snippet"]
        ]
        assert snippets, person["name"]
        assert all("Former" in s for s in snippets)


# Verifies: specs/user-recognition/requirements.md#1.2
def test_cassandra_the_person_has_a_page_that_names_her(
    key: generator.Json, serp: generator.Json, pages: generator.Json
) -> None:
    people = [p for p in key["people"] if p["scenario"] == "cassandra_name"]
    for person in people:
        bodies = [
            pages["pages"][r["link"]]
            for r in serp[person["company"]]["results"]["own_site"]
        ]
        assert any(person["name"] in b for b in bodies), person["name"]


# Verifies: specs/user-recognition/requirements.md#1.2
def test_the_injection_company_page_carries_the_instruction(
    serp: generator.Json, pages: generator.Json
) -> None:
    link = serp["fenwickanalytics.io"]["results"]["own_site"][0]["link"]
    assert "Ignore all previous instructions" in pages["pages"][link]


# Verifies: specs/user-recognition/requirements.md#4.2
def test_robots_txt_disallows_exactly_one_path_and_one_page_sits_under_it(
    pages: generator.Json,
) -> None:
    disallowed = re.findall(r"(?mi)^disallow:\s*(\S+)", pages["robots_txt"])
    assert disallowed == ["/private/"]
    parser = RobotFileParser()
    parser.parse(pages["robots_txt"].splitlines())
    blocked = [u for u in pages["pages"] if not parser.can_fetch("*", u)]
    assert len(blocked) == 1


# Verifies: specs/user-recognition/requirements.md#4.2
def test_the_demo_serves_a_page_and_robots_txt_and_counts_them() -> None:
    log = DemoLog()
    served = DemoPages(_Tables(generator.DATA_DIR), log)
    url = next(iter(generator.load("pages")["pages"]))
    status, body = served.fetch(url)
    assert status == 200
    assert body.startswith("<!doctype html>")
    status, robots = served.fetch("https://www.datastax.com/robots.txt")
    assert status == 200
    assert robots.count("Disallow:") == 1
    assert served.fetch("https://nowhere.example/missing")[0] == 404
    assert log.requests["pages.fetch"] == 3


# Verifies: specs/user-recognition/requirements.md#4.1
async def test_the_transport_routes_each_query_family_to_its_results() -> None:
    brack = "Brackenridge Retail"
    cases = {
        f'site:datastax.com "{brack}" DataStax OR "Astra DB"': "datastax.com/customers",
        f'(site:boards.greenhouse.io OR site:jobs.lever.co) "{brack}" DataStax': (
            "boards.greenhouse.io"
        ),
        f'site:linkedin.com/company "{brack}" DataStax': "linkedin.com/company",
    }
    for q, expected in cases.items():
        body = await _search(q)
        assert expected in body["organic_results"][0]["link"], q
    own = await _search("site:callowaylogistics.com DataStax")
    assert "callowaylogistics.com" in own["organic_results"][0]["link"]
    missing = await _search('site:github.com "Brackenridge Retail" DataStax')
    assert "organic_results" not in missing  # no code proof: an empty answer


# Verifies: specs/user-recognition/requirements.md#1.2
def test_the_pages_table_is_deterministic_for_the_seed() -> None:
    first = json.dumps(generator.build()["pages"], sort_keys=True)
    assert json.dumps(generator.build()["pages"], sort_keys=True) == first
