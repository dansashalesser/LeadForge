"""Query families (user-recognition requirements 4.1, 9.2)."""

from leadforge.lead_ingestion.demo import transport as demo
from leadforge.outreach.usage.queries import (
    FAMILY_ORDER,
    QuerySpec,
    anchored_candidates,
    families,
)

ALIASES = ("Astra DB", "DataStax Astra")


def _specs() -> list[QuerySpec]:
    return families("Acme Corp", ALIASES, "datastax.com", "acme.com")


# Verifies: specs/user-recognition/requirements.md#4.1
def test_families_run_strongest_first() -> None:
    assert [s.family for s in _specs()] == list(FAMILY_ORDER)
    assert FAMILY_ORDER == (
        "vendor_customer",
        "job_posting",
        "code",
        "own_site",
        "third_party",
        "linkedin_public",
    )


# Verifies: specs/user-recognition/requirements.md#4.1
def test_each_family_is_one_query_with_aliases_or_joined() -> None:
    for spec in _specs():
        assert '"Acme Corp"' in spec.query
        assert '("Astra DB" OR "DataStax Astra")' in spec.query


# Verifies: specs/user-recognition/requirements.md#4.1
def test_family_shapes() -> None:
    by = {s.family: s.query for s in _specs()}
    assert by["vendor_customer"].startswith("site:datastax.com ")
    assert "site:boards.greenhouse.io" in by["job_posting"]
    assert "site:acme.com/careers" in by["job_posting"]
    assert by["code"].startswith("site:github.com ")
    assert by["own_site"].startswith("site:acme.com ")
    assert "site:" not in by["third_party"]


# Verifies: specs/user-recognition/requirements.md#9.2
def test_linkedin_family_searches_in_company_and_posts() -> None:
    q = {s.family: s.query for s in _specs()}["linkedin_public"]
    for part in ("in", "company", "posts"):
        assert f"site:linkedin.com/{part}" in q


# Verifies: specs/user-recognition/requirements.md#4.1
def test_anchored_results_become_third_party_candidates() -> None:
    got = anchored_candidates(
        [
            {"link": "https://a.example/x", "title": "T", "snippet": "S"},
            {"url": "https://b.example/y", "title": "U"},
            {"title": "no url"},
        ]
    )
    assert [(c.family, c.url) for c in got] == [
        ("third_party", "https://a.example/x"),
        ("third_party", "https://b.example/y"),
    ]
    assert got[1].snippet == ""


# Verifies: specs/user-recognition/requirements.md#4.1
def test_demo_router_answers_every_family_shape() -> None:
    tables = demo._Tables(demo.DATA_DIR)
    transport = demo.DemoTransport(
        "google_search", {}, tables=tables, log=demo.DemoLog()
    )
    domain, entry = next(iter(tables.families.items()))
    seen = set()
    for spec in families(entry["name"], ALIASES, "datastax.com", domain):
        found = transport._family_results(spec.query)
        assert found is not None
        seen.add(spec.family)
    assert seen == set(FAMILY_ORDER)
