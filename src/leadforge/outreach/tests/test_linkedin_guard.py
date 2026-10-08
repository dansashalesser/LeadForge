"""LinkedIn guard (Req 9.2, 9.4): no session credentials, no fetch of linkedin.com."""

import ast
import asyncio
import re
import uuid
from datetime import date
from pathlib import Path

import httpx
import pytest

import leadforge
from leadforge.lead_ingestion.catalog import Alias, CatalogProduct, CatalogVendor
from leadforge.lead_ingestion.models import (
    CanonicalLead,
    CompanySignal,
    Employment,
)
from leadforge.outreach.usage.budget import UsageBudget
from leadforge.outreach.usage.classify import Judgement
from leadforge.outreach.usage.fetch import PageFetcher, Skipped
from leadforge.outreach.usage.person import RoleVocabulary
from leadforge.outreach.usage.records import ClassifierStamp, Relationship
from leadforge.outreach.usage.serp import SearchResult
from leadforge.outreach.usage.stage import (
    StageConfig,
    StageDeps,
    StageLead,
    run_usage_stage,
)

PKG = Path(leadforge.__file__).parent
CREDENTIAL = re.compile(
    r"li_at|jsessionid"
    r"|linkedin[_\-. ]?(cookie|session|passw|token|login|credential|username"
    r"|auth|secret|li_at)",
    re.IGNORECASE,
)
SCANNED = ("lead_ingestion", "outreach")


def _source_files() -> list[Path]:
    return [
        p
        for top in SCANNED
        for p in (PKG / top).rglob("*.py")
        if "tests" not in p.parts
    ]


def _names(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.arg) or (isinstance(node, ast.keyword) and node.arg):
            found.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
    return found


# Verifies: specs/user-recognition/requirements.md#9.4
def test_scan_covers_adapters_config_and_fetch_code():
    names = {p.name for p in _source_files()}
    assert {"fetch.py", "config.py"} <= names
    assert len(names) > 20


# Verifies: specs/user-recognition/requirements.md#9.4
def test_no_module_names_a_linkedin_session_credential():
    hits = []
    for path in _source_files():
        for name in _names(ast.parse(path.read_text(encoding="utf-8"))):
            if CREDENTIAL.search(name):
                hits.append(f"{path.relative_to(PKG)}: {name[:60]!r}")
    assert hits == []


# Verifies: specs/user-recognition/requirements.md#9.4
def test_no_config_file_holds_linkedin_credentials():
    root = PKG.parents[1] / "config"
    texts = [p.read_text(encoding="utf-8") for p in root.rglob("*.y*ml")]
    assert texts
    assert [t for t in texts if CREDENTIAL.search(t)] == []


# Verifies: specs/user-recognition/requirements.md#9.4 (guard self-check)
@pytest.mark.parametrize(
    "bad",
    ["li_at", "JSESSIONID", "linkedin_cookie", "LINKEDIN_PASSWORD", "linkedin-token"],
)
def test_credential_pattern_matches_known_names(bad):
    assert CREDENTIAL.search(bad)


def _fetcher():
    log: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(str(request.url))
        return httpx.Response(200, text="Acme Forge is used here")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    budget = UsageBudget(searches=1, fetches=20, llm_calls=1)
    return PageFetcher(client, budget), log, budget


LINKEDIN_URLS = [
    "https://www.linkedin.com/in/pat",
    "https://linkedin.com/company/acme",
    "https://LinkedIn.COM/posts/x",
    "https://WWW.LINKEDIN.COM/in/pat",
    "https://m.linkedin.com/in/pat",
    "https://uk.linkedin.com/in/pat",
    "https://linkedin.com:443/in/pat",
    "https://www.linkedin.com:8443/in/pat",
    "https://linkedin.com./in/pat",
    "http://linkedin.com/in/pat",
    "https://user:pw@linkedin.com/in/pat",
    "https://evil.com@www.linkedin.com/in/pat",
]


# Verifies: specs/user-recognition/requirements.md#9.2
@pytest.mark.parametrize("url", LINKEDIN_URLS)
def test_linkedin_urls_are_skipped_with_no_request_at_all(url):
    f, log, budget = _fetcher()
    out = f.fetch_passages(url, ["acme forge"])
    assert out == Skipped(url, "linkedin")
    assert log == []  # not even robots.txt
    assert budget.spend("fetches")  # no fetch budget was consumed


NOT_LINKEDIN = [
    "https://linkedin.com.evil.com/in/pat",
    "https://notlinkedin.com/in/pat",
    "https://evil.com/linkedin.com/in/pat",
    "https://evil.com/?u=https://www.linkedin.com/in/pat",
    "https://linkedin.com@evil.com/in/pat",
]


# Verifies: specs/user-recognition/requirements.md#9.2
@pytest.mark.parametrize("url", NOT_LINKEDIN)
def test_lookalike_hosts_are_not_mistaken_for_linkedin(url):
    f, log, _ = _fetcher()
    out = f.fetch_passages(url, ["acme forge"])
    assert not (isinstance(out, Skipped) and out.reason == "linkedin")
    assert log, "the lookalike host should have been fetched normally"
    hosts = {httpx.URL(u).host for u in log}
    assert not any(h == "linkedin.com" or h.endswith(".linkedin.com") for h in hosts)


# Verifies: specs/user-recognition/requirements.md#9.2
def test_schemeless_linkedin_url_never_reaches_the_network():
    f, log, _ = _fetcher()
    f.fetch_passages("linkedin.com/in/pat", ["acme forge"])
    f.fetch_passages("//www.linkedin.com/in/pat", ["acme forge"])
    assert log == []


# --- the stage reaches linkedin only through search snippets -----------------

PRODUCT = CatalogProduct(
    key="prod_a",
    name="Product A",
    aliases=(Alias(text="Product A"),),
    technology_uids=("uid-a",),
)
VENDOR = CatalogVendor(
    key="vend",
    name="Vend",
    domains=("vend.example",),
    partner_domains=(),
    products=(PRODUCT,),
    ecosystem=(),
)


class _Serp:
    def __init__(self, budget):
        self.budget, self.specs = budget, []

    async def search(self, spec):
        self.budget.spend("searches")
        self.specs.append(spec)
        if spec.family == "linkedin_public":
            return [
                SearchResult(
                    "Pat - Platform Engineer | LinkedIn",
                    "https://www.linkedin.com/in/pat",
                    "Pat is a platform engineer using Product A at Acme.",
                    None,
                )
            ]
        return []


class _Classifier:
    def stamp(self, target, product, passages):
        return ClassifierStamp(
            kind="offline", model=None, prompt_version="t", input_hash="h"
        )

    def classify(self, target, product, passages):
        return Judgement(
            subject_is_target_company=True,
            product_key=product.key,
            relationship=Relationship.USES_NOW,
            quotes=("Product A",),
            confidence=0.9,
        )


class _Store:
    def __init__(self):
        self.evidence = []

    def append_evidence(self, search_id, record):
        self.evidence.append(record)
        return uuid.uuid4()

    def upsert_company_grade(self, *args):
        pass


# Verifies: specs/user-recognition/requirements.md#9.2
def test_linkedin_family_is_searched_never_fetched():
    http_log: list[str] = []

    def handler(request):
        http_log.append(str(request.url))
        return httpx.Response(404)

    budget = UsageBudget(searches=20, fetches=20, llm_calls=20)
    serp, store = _Serp(budget), _Store()
    fetcher = PageFetcher(httpx.Client(transport=httpx.MockTransport(handler)), budget)
    deps = StageDeps(
        serp=serp,
        fetcher=fetcher,
        classifier=_Classifier(),
        store=store,
        budget=budget,
        search_id=uuid.uuid4(),
        today=date(2026, 10, 8),
    )
    company = CompanySignal(company_id="c", name="Acme", domains=("acme.example",))
    lead = StageLead(
        lead_id=uuid.uuid4(),
        lead=CanonicalLead(
            full_name="Pat",
            employments=(
                Employment(company=company, title="Engineer", is_current=True),
            ),
        ),
        role_fields=("Platform Engineer",),
    )
    cfg = StageConfig(roles=RoleVocabulary(core=("platform engineer",), irrelevant=()))
    asyncio.run(run_usage_stage([lead], VENDOR, ["prod_a"], deps, cfg))

    linkedin_specs = [s for s in serp.specs if s.family == "linkedin_public"]
    assert linkedin_specs
    for spec in linkedin_specs:
        for scope in ("linkedin.com/in", "linkedin.com/company", "linkedin.com/posts"):
            assert f"site:{scope}" in spec.query
    assert all("linkedin" not in u.lower() for u in http_log)
    assert any(s.url == "https://www.linkedin.com/in/pat" for s in fetcher.skipped)
    assert all(
        r.snippet_only for r in store.evidence if "linkedin" in str(r.url).lower()
    )
