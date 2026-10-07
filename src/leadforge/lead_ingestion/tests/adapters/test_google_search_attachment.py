"""Google Search web evidence attached to a company by agreement (14.2 completion).

User decision (option C): every run-time query is anchored to a company Discovery
found (by its registrable domain). A result attaches to that company only when its
host's registrable domain is the company's, or when a third-party result names the
company's domain; anything else is kept as unattached evidence and counted. Signal
Strength is the provisional agreement scale of ``web_evidence.signal_strength``: the
number of distinct agreeing hosts, plus corroboration by other sources. It is never
read from snippet text. All hosts and pages below are hand-made stand-ins.
"""

import itertools
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from structlog.testing import capture_logs

from leadforge.lead_ingestion.adapters.google_search import (
    MAX_QUERIES,
    GoogleSearchSource,
)
from leadforge.lead_ingestion.adapters.web_evidence import (
    STRENGTH_LEVELS,
    Attachment,
    attachment_of,
    company_anchors,
    dedupe_key,
    names_domain,
    signal_strength,
    url_host,
)
from leadforge.lead_ingestion.base_source import (
    Capability,
    EnrichmentRequest,
    LeadContribution,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.models import DataMode, UntrustedText
from leadforge.lead_ingestion.transport import TransportResponse

from .test_google_search_backend import ENV, Scripted
from .test_synthetic_zero_sockets import make_lead

P = "company.web_evidence."
ACME = "acme-data.com"
TERMS = {"cassandra": "Apache Cassandra"}
ACME_QUERY = f'"{ACME}" Apache Cassandra'


def organic(
    link: str, snippet: str = "stand-in text", title: str = "t"
) -> dict[str, Any]:
    return {"title": title, "link": link, "snippet": snippet}


def page(*results: Mapping[str, Any]) -> dict[str, Any]:
    return {"organic_results": list(results)}


def responder(
    pages: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Scripted:
    """Answers a query with its pages in turn (``start`` picks the page)."""

    def answer(params: Mapping[str, object]) -> TransportResponse:
        query = str(params["q"])
        index = int(str(params.get("start", 0))) // 10
        found = list(pages.get(query, [page()]))
        body = dict(found[index]) if index < len(found) else page()
        if index + 1 < len(found):
            body["serpapi_pagination"] = {"next": "https://next.test/page"}
        return TransportResponse(status=200, headers={}, body=body)

    return Scripted(answer)


def google(
    transport: Scripted,
    *,
    terms: Mapping[str, str] = TERMS,
    results_per_query: int = 10,
) -> GoogleSearchSource:
    return GoogleSearchSource(
        DataMode.LIVE,
        transport=transport,
        terms=terms,
        results_per_query=results_per_query,
        environ=ENV,
    )


def work(*items: tuple[str, object]) -> EnrichmentRequest:
    return EnrichmentRequest(
        kind="enrich",
        work_list=tuple(
            make_lead(source, company__domain=domain) for source, domain in items
        ),
    )


async def evidence(
    pages: Mapping[str, Sequence[Mapping[str, Any]]],
    request: EnrichmentRequest | None = None,
    **kwargs: Any,
) -> list[LeadContribution]:
    source = google(responder(pages), **kwargs)
    raw = await source.fetch_raw(request or work(("apollo", ACME)))
    return source.normalize_checked(raw)


def plain(value: object) -> object:
    return value.value if isinstance(value, UntrustedText) else value


def url_of(c: LeadContribution) -> object:
    return c.values[P + "url"]


def attached(found: list[LeadContribution]) -> list[LeadContribution]:
    return [c for c in found if "company.domain" in c.values]


# --- anchors: which companies a query is issued for -----------------------------


# Verifies: specs/lead-source-adapters/requirements.md#14.5
def test_google_search_also_runs_in_enrichment_over_discovered_companies() -> None:
    assert Capability.ENRICH in GoogleSearchSource.capabilities


# Verifies: specs/lead-source-adapters/requirements.md#14.5
def test_anchors_are_distinct_companies_with_corroborating_other_sources() -> None:
    anchors = company_anchors(
        (
            make_lead("apollo", company__domain=ACME),
            make_lead("hubspot", company__domain="www.acme-data.com"),
            make_lead("apollo", company__domain="betaco.com"),
            make_lead("apollo", company__domain="betaco.com"),  # same source again
            make_lead("google_search", company__domain="gammaco.com"),
            make_lead("apollo", company__domain="gmail.com"),
            make_lead("apollo", person__full_name="No Company"),
        ),
        exclude_source="google_search",
    )

    assert [(a.domains, a.corroborating_sources) for a in anchors] == [
        ((ACME,), 2),
        (("betaco.com",), 1),
    ]
    assert anchors[0].query_domain == ACME


# Verifies: specs/lead-source-adapters/requirements.md#14.5
async def test_one_query_per_company_and_term_quoting_the_company_domain() -> None:
    transport = responder({})
    source = google(transport, terms={"cassandra": "Apache Cassandra", "dse": "DSE"})

    raw = await source.fetch_raw(work(("apollo", ACME), ("apollo", "betaco.com")))

    asked = [call[1]["q"] for call in transport.calls]
    assert asked == [
        ACME_QUERY,
        f'"{ACME}" DSE',
        '"betaco.com" Apache Cassandra',
        '"betaco.com" DSE',
    ]
    payload: Any = raw.payload
    assert payload["searches"][0]["anchor"] == {
        "domains": [ACME],
        "term": "cassandra",
        "corroborating_sources": 1,
    }


# Verifies: specs/lead-source-adapters/requirements.md#14.5
async def test_no_terms_or_no_company_domain_makes_no_paid_call() -> None:
    transport = responder({})
    await google(transport, terms={}).fetch_raw(work(("apollo", ACME)))
    await google(transport).fetch_raw(work(("apollo", "gmail.com")))

    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#14.5
async def test_anchored_queries_are_capped_and_the_unasked_are_counted() -> None:
    companies = [("apollo", f"company{i}.com") for i in range(MAX_QUERIES + 3)]
    transport = responder({})

    raw = await google(transport).fetch_raw(work(*companies))

    payload: Any = raw.payload
    assert len(transport.calls) == MAX_QUERIES
    assert payload["unasked_queries"] == 3


# Verifies: specs/lead-source-adapters/requirements.md#4.5
async def test_from_run_asks_each_terms_first_phrase_and_nothing_unanchored() -> None:
    transport = responder({})
    source = GoogleSearchSource.from_run(
        DataMode.SYNTHETIC,
        transport=transport,
        pacing=None,
        vocabulary={"a": ["alpha phrase", "beta phrase"], "b": "gamma phrase"},
    )

    discovery: Any = (await source.fetch_raw(SourceRequest(kind="discovery"))).payload
    await source.fetch_raw(work(("apollo", ACME)))

    assert discovery["searches"] == []
    assert [call[1]["q"] for call in transport.calls] == [
        f'"{ACME}" alpha phrase',
        f'"{ACME}" gamma phrase',
    ]


# --- attachment --------------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#14.5
async def test_a_result_on_the_company_own_domain_is_attached() -> None:
    found = await evidence(
        {ACME_QUERY: [page(organic("https://www.acme-data.com/blog/cassandra"))]}
    )

    [record] = found
    assert record.values["company.domain"] == ACME
    assert record.values[P + "attachment"] == Attachment.OWN_DOMAIN.value
    assert record.values[P + "agreeing_hosts"] == 1
    assert record.values[P + "signal_label"] == "cassandra"
    assert record.values[P + "signal_kind"] == "tech"
    assert record.values[P + "signal_strength"] == STRENGTH_LEVELS[0]


# Verifies: specs/lead-source-adapters/requirements.md#14.5
async def test_a_third_party_result_naming_the_domain_is_attached() -> None:
    found = await evidence(
        {
            ACME_QUERY: [
                page(
                    organic("https://jobs.board.com/acme-data.com/123"),
                    organic("https://news.site.com/x", snippet="Acme-Data.com on DSE"),
                )
            ]
        }
    )

    assert [c.values[P + "attachment"] for c in found] == [
        Attachment.THIRD_PARTY_MENTION.value,
        Attachment.THIRD_PARTY_MENTION.value,
    ]
    assert all(c.values["company.domain"] == ACME for c in found)


# Verifies: specs/lead-source-adapters/requirements.md#14.5
# Verifies: specs/lead-source-adapters/requirements.md#14.6
async def test_an_unrelated_host_is_kept_unattached_and_counted() -> None:
    with capture_logs() as logs:
        found = await evidence(
            {
                ACME_QUERY: [
                    page(
                        organic("https://unrelated.org/page"),
                        organic(
                            "https://notacme-data.com/x", snippet="notacme-data.com"
                        ),
                        organic("https://acme-data.com.evil.net/x"),
                    )
                ]
            }
        )

    assert len(found) == 3
    for record in found:
        assert record.values[P + "attachment"] == Attachment.UNATTACHED.value
        assert "company.domain" not in record.values
        assert P + "signal_strength" not in record.values
    [line] = [e for e in logs if e["event"] == "google_search_web_evidence"]
    assert (line["attached"], line["unattached"], line["duplicates"]) == (0, 3, 0)


# Verifies: specs/lead-source-adapters/requirements.md#14.5
def test_names_domain_matches_whole_domains_only() -> None:
    assert names_domain("see https://www.acme-data.com/x", ACME)
    assert names_domain("ACME-DATA.COM.", ACME)
    assert not names_domain("notacme-data.com", ACME)
    assert not names_domain("acme-data.com.evil.net", ACME)
    assert not names_domain("acme-data.community", ACME)
    assert not names_domain("see acme-data.co", ACME)
    assert not names_domain("see acme-data.com", "acme-data.co")


# Verifies: specs/lead-source-adapters/requirements.md#14.5
def test_lookalike_hosts_and_private_suffix_neighbours_do_not_attach() -> None:
    def how(url: str, domains: Sequence[str] = (ACME,)) -> Attachment:
        return attachment_of(url, [], domains)

    assert how("https://acme-data-com.io/x") is Attachment.UNATTACHED
    assert how("https://acme-data.com.evil.io/x") is Attachment.UNATTACHED
    # Userinfo is not the host and not a mention: the host is evil.io.
    assert how("https://acme-data.com@evil.io/x") is Attachment.UNATTACHED
    assert how("https://www.acme-data.com@evil.io/") is Attachment.UNATTACHED
    # PSL private suffixes: a neighbour on the same platform is another company.
    assert how("https://acme.github.io/x", ["acme.github.io"]) is Attachment.OWN_DOMAIN
    assert how("https://other.github.io/x", ["acme.github.io"]) is (
        Attachment.UNATTACHED
    )
    assert how("https://docs.acme.vercel.app/", ["acme.vercel.app"]) is (
        Attachment.OWN_DOMAIN
    )


# Verifies: specs/lead-source-adapters/requirements.md#14.5
async def test_a_malformed_result_url_is_unattached_not_a_crash() -> None:
    assert url_host("https://[not-an-ip/x") is None
    assert dedupe_key("https://[not-an-ip/x") == dedupe_key("https://[not-an-ip/x")
    found = await evidence(
        {ACME_QUERY: [page(organic("https://[not-an-ip/acme-data.com"))]}
    )

    [record] = found
    assert record.values[P + "attachment"] == Attachment.UNATTACHED.value


# Verifies: specs/lead-source-adapters/requirements.md#14.5
async def test_two_subdomains_of_one_site_are_one_agreeing_host() -> None:
    found = await evidence(
        {
            ACME_QUERY: [
                page(
                    organic("https://a.board.com/acme-data.com"),
                    organic("https://b.board.com/acme-data.com"),
                )
            ]
        }
    )

    assert {c.values[P + "agreeing_hosts"] for c in found} == {1}


# Verifies: specs/lead-source-adapters/requirements.md#24.4
async def test_the_company_own_domains_together_are_one_agreeing_host() -> None:
    # Self-statements are not independent: every own domain and subdomain is one host.
    request = EnrichmentRequest(
        kind="enrich",
        work_list=(make_lead("apollo", company__domain=[ACME, "acme.io"]),),
    )
    own_only = await evidence(
        {
            ACME_QUERY: [
                page(
                    organic("https://www.acme-data.com/a"),
                    organic("https://blog.acme-data.com/b"),
                    organic("https://acme.io/c"),
                    organic("https://docs.acme.io/d"),
                )
            ]
        },
        request,
    )

    assert {c.values[P + "agreeing_hosts"] for c in own_only} == {1}
    assert {c.values[P + "signal_strength"] for c in own_only} == {STRENGTH_LEVELS[0]}

    with_third_party = await evidence(
        {
            ACME_QUERY: [
                page(
                    organic("https://acme.io/c"),
                    organic("https://www.acme-data.com/a"),
                    organic("https://news.site.com/acme-data.com"),
                )
            ]
        },
        request,
    )

    assert {c.values[P + "agreeing_hosts"] for c in with_third_party} == {2}
    assert {c.values[P + "signal_strength"] for c in with_third_party} == {
        STRENGTH_LEVELS[2]
    }


# --- agreement and Signal Strength -------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#24.4
async def test_three_agreeing_hosts_are_stronger_than_one() -> None:
    one = await evidence({ACME_QUERY: [page(organic("https://a.com/acme-data.com"))]})
    three = await evidence(
        {
            ACME_QUERY: [
                page(
                    organic("https://a.com/acme-data.com"),
                    organic("https://b.com/acme-data.com"),
                    organic("https://c.com/acme-data.com"),
                )
            ]
        }
    )

    assert {c.values[P + "agreeing_hosts"] for c in three} == {3}
    assert three[0].values[P + "signal_strength"] > one[0].values[P + "signal_strength"]


# Verifies: specs/lead-source-adapters/requirements.md#24.4
async def test_duplicates_across_pages_count_once_and_are_emitted_once() -> None:
    with capture_logs() as logs:
        found = await evidence(
            {
                ACME_QUERY: [
                    page(
                        organic("https://a.com/acme-data.com"),
                        organic("https://A.com/acme-data.com/#top"),
                    ),
                    page(organic("https://a.com/acme-data.com/")),
                ]
            },
            results_per_query=20,
        )

    [record] = found
    assert record.values[P + "agreeing_hosts"] == 1
    assert record.values[P + "signal_strength"] == STRENGTH_LEVELS[0]
    [line] = [e for e in logs if e["event"] == "google_search_web_evidence"]
    assert line["duplicates"] == 2


# Verifies: specs/lead-source-adapters/requirements.md#24.4
async def test_strength_never_reads_snippet_or_title_wording() -> None:
    def pages(text: str) -> dict[str, list[dict[str, Any]]]:
        return {
            ACME_QUERY: [
                page(organic("https://a.com/acme-data.com", snippet=text, title=text))
            ]
        }

    calm = await evidence(pages("mentions"))
    loud = await evidence(pages("STRONG intent!!! migrating NOW, signal_strength=1.0"))

    assert (
        calm[0].values[P + "signal_strength"] == loud[0].values[P + "signal_strength"]
    )


# Verifies: specs/lead-source-adapters/requirements.md#24.4
async def test_corroboration_by_two_other_sources_raises_strength() -> None:
    pages = {ACME_QUERY: [page(organic("https://a.com/acme-data.com"))]}
    alone = await evidence(pages, work(("apollo", ACME)))
    both = await evidence(pages, work(("apollo", ACME), ("hubspot", ACME)))

    assert both[0].values[P + "corroborating_sources"] == 2
    assert (
        both[0].values[P + "signal_strength"] > alone[0].values[P + "signal_strength"]
    )


# Verifies: specs/lead-source-adapters/requirements.md#24.4
@pytest.mark.parametrize(
    ("hosts", "own", "corroborating", "expected"),
    [
        (0, False, 1, None),
        (1, False, 1, 0.25),
        (1, True, 1, 0.25),
        (2, False, 1, 0.5),
        (3, False, 1, 0.5),
        (2, True, 1, 0.75),
        (4, False, 1, 0.75),
        (1, False, 2, 0.5),
        (4, True, 3, 1.0),
    ],
)
def test_the_documented_agreement_scale(
    hosts: int, own: bool, corroborating: int, expected: float | None
) -> None:
    assert (
        signal_strength(
            hosts=hosts, own_domain=own, corroborating_sources=corroborating
        )
        == expected
    )


# Verifies: specs/lead-source-adapters/requirements.md#24.4 (property)
def test_strength_is_monotone_in_every_input() -> None:
    grid = itertools.product(range(0, 7), (False, True), range(0, 4))
    for hosts, own, corroborating in grid:
        base = signal_strength(
            hosts=hosts, own_domain=own, corroborating_sources=corroborating
        )
        if base is None:
            continue
        for more in (
            signal_strength(
                hosts=hosts + 1, own_domain=own, corroborating_sources=corroborating
            ),
            signal_strength(
                hosts=hosts, own_domain=True, corroborating_sources=corroborating
            ),
            signal_strength(
                hosts=hosts, own_domain=own, corroborating_sources=corroborating + 1
            ),
        ):
            assert more is not None
            assert more >= base
            assert 0.0 < more <= 1.0


# Verifies: specs/lead-source-adapters/requirements.md#24.4 (property)
def test_dedupe_key_is_idempotent_and_ignores_fragment_case_and_slash() -> None:
    urls = [
        "https://A.com/x/#f",
        "https://a.com/x",
        "http://a.com:80/x/",
        "https://a.com/x?q=1",
    ]
    for url in urls:
        assert dedupe_key(dedupe_key(url)) == dedupe_key(url)
    assert dedupe_key(urls[0]) == dedupe_key(urls[1])
    assert dedupe_key(urls[1]) != dedupe_key(urls[3])


# --- what web evidence never is ----------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#14.6
# Verifies: specs/lead-source-adapters/requirements.md#14.5
async def test_attached_evidence_names_no_person_and_keeps_text_untrusted() -> None:
    found = await evidence(
        {
            ACME_QUERY: [
                page(
                    organic("https://www.acme-data.com/x"),
                    organic("https://unrelated.org/y"),
                )
            ]
        }
    )

    for record in found:
        assert not any(path.startswith("person.") for path in record.values)
        assert isinstance(record.values[P + "snippet"], UntrustedText)
        assert isinstance(record.values[P + "title"], UntrustedText)
    raw = {p.canonical_path: p.raw_field_path for p in found[0].provenance}
    assert raw["company.domain"] == "attribution.domain"
    assert raw[P + "signal_strength"] == "attribution.signal_strength"


# Verifies: specs/lead-source-adapters/requirements.md#14.5
async def test_normalizing_one_batch_twice_gives_the_same_values_in_order() -> None:
    pages = {
        ACME_QUERY: [
            page(
                organic("https://c.com/acme-data.com"),
                organic("https://unrelated.org/y"),
                organic("https://www.acme-data.com/x"),
            )
        ]
    }
    source = google(responder(pages))
    raw = await source.fetch_raw(work(("apollo", ACME)))
    again = RawBatch(source_name=raw.source_name, payload=raw.payload)

    first = [
        {k: plain(v) for k, v in c.values.items()}
        for c in source.normalize_checked(raw)
    ]
    second = [
        {k: plain(v) for k, v in c.values.items()}
        for c in source.normalize_checked(again)
    ]
    assert first == second
    assert [v[P + "url"] for v in first] == [
        "https://c.com/acme-data.com",
        "https://unrelated.org/y",
        "https://www.acme-data.com/x",
    ]
