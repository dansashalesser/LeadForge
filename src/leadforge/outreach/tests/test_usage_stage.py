"""Usage stage (Req 4.4, 4.5, 7.3): early stop, budget, match credits, exclusions."""

import asyncio
import uuid
from datetime import date

from leadforge.lead_ingestion.catalog import Alias, CatalogProduct, CatalogVendor
from leadforge.lead_ingestion.models import (
    CanonicalLead,
    CompanySignal,
    Employment,
    TechSignal,
)
from leadforge.outreach.usage.budget import UsageBudget
from leadforge.outreach.usage.classify import Judgement
from leadforge.outreach.usage.fetch import Passages, Skipped
from leadforge.outreach.usage.grade import CompanyGrade
from leadforge.outreach.usage.person import RoleVocabulary
from leadforge.outreach.usage.queries import FAMILY_ORDER
from leadforge.outreach.usage.records import ClassifierStamp, Relationship
from leadforge.outreach.usage.serp import SearchBudgetExhaustedError, SearchResult
from leadforge.outreach.usage.stage import (
    StageConfig,
    StageDeps,
    StageLead,
    run_usage_stage,
)
from leadforge.outreach.usage.verdict import VerdictStatus

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
    partner_domains=("partner.example",),
    products=(PRODUCT,),
    ecosystem=(),
)
ROLES = RoleVocabulary(core=("platform engineer",), irrelevant=("sales",))
TODAY = date(2026, 10, 8)


class FakeSerp:
    """Answers per family; spends the real budget like the real client."""

    def __init__(self, budget, by_family):
        self.budget, self.by_family, self.log = budget, by_family, []

    async def search(self, spec):
        if not self.budget.spend("searches"):
            raise SearchBudgetExhaustedError
        self.log.append(spec)
        q = spec.query.lower()
        rows = self.by_family.get(spec.family, [])
        return [r for r in rows if r.title.startswith("x ") or r.title.split()[0] in q]


class FakeFetcher:
    def fetch_passages(self, url, aliases):
        if "blocked" in url:
            return Skipped(url, "robots_disallowed")
        return Passages(url, (f"{url} says it uses Product A in production",))


class FakeClassifier:
    def stamp(self, target, product, passages):
        return ClassifierStamp(
            kind="offline", model=None, prompt_version="t", input_hash="h"
        )

    def classify(self, target, product, passages):
        text = " ".join(passages.passages)
        if "uses Product A" not in text and "Product A" not in text:
            return None
        return Judgement(
            subject_is_target_company=True,
            product_key=product.key,
            relationship=Relationship.USES_NOW,
            quotes=("uses Product A",),
            confidence=0.9,
        )


class FakeStore:
    def __init__(self):
        self.evidence, self.grades = [], {}

    def append_evidence(self, search_id, record):
        self.evidence.append(record)
        return uuid.uuid4()

    def upsert_company_grade(self, search_id, company, product, grade, reason, ids):
        self.grades[(company, product)] = (grade, reason, list(ids))


def hit(family, name="x"):
    return SearchResult(
        title=f"{name} and Product A",
        url=f"https://{name}.example/{family}",
        snippet="runs Product A",
        date="2026-09-01",
    )


def make_lead(domain, *, name="Pat", current=True, uid=True, title="Platform Engineer"):
    signals = (
        (
            TechSignal(
                label="Product A",
                strength=0.8,
                source="prov",
                current=True,
                uid="uid-a",
            ),
        )
        if uid
        else ()
    )
    company = CompanySignal(
        company_id=f"c-{domain}",
        name=domain.split(".")[0].title(),
        domains=(domain,),
        tech_signals=signals,
    )
    return StageLead(
        lead_id=uuid.uuid4(),
        lead=CanonicalLead(
            full_name=name,
            employments=(Employment(company=company, title=title, is_current=current),),
        ),
        role_fields=(title,),
    )


def run(leads, by_family=None, *, searches=50, match=None, cfg=None):
    budget = UsageBudget(searches=searches, fetches=50, llm_calls=50)
    serp = FakeSerp(budget, by_family or {})
    store = FakeStore()
    deps = StageDeps(
        serp=serp,
        fetcher=FakeFetcher(),
        classifier=FakeClassifier(),
        store=store,
        budget=budget,
        search_id=uuid.uuid4(),
        today=TODAY,
        match_person=match,
    )
    cfg = cfg or StageConfig(roles=ROLES)
    out = asyncio.run(run_usage_stage(leads, VENDOR, ["prod_a"], deps, cfg))
    return out, serp, store


# Verifies: specs/user-recognition/requirements.md#4.4
def test_verified_by_first_family_has_no_later_queries():
    lead = make_lead("acme.example")
    out, serp, store = run([lead], {"vendor_customer": [hit("vendor_customer")]})
    assert [s.family for s in serp.log] == ["vendor_customer"]
    v = out[lead.lead_id]
    assert v.company_usage.grade is CompanyGrade.VERIFIED
    assert v.status is VerdictStatus.SELECTED
    assert store.grades[("acme.example", "prod_a")][0] == "verified"
    assert len(store.evidence) == 2  # technographic + vendor page


# Verifies: specs/user-recognition/requirements.md#4.4
def test_unproven_company_walks_every_family_in_order():
    lead = make_lead("acme.example", uid=False)
    out, serp, _ = run([lead])
    assert tuple(s.family for s in serp.log) == FAMILY_ORDER
    assert out[lead.lead_id].status is VerdictStatus.REJECTED


def test_one_company_is_searched_once_for_many_leads():
    leads = [make_lead("acme.example", name=n) for n in ("Pat", "Sam")]
    _, serp, _ = run(leads, {"vendor_customer": [hit("vendor_customer")]})
    assert len(serp.log) == 1


# Verifies: specs/user-recognition/requirements.md#4.5
def test_search_budget_of_five_caps_searches_and_marks_rest_exhausted():
    leads = [make_lead(f"co{i}.example", uid=False) for i in range(3)]
    out, serp, store = run(leads, searches=5)
    assert len(serp.log) == 5
    reasons = [out[x.lead_id].company_usage.reason for x in leads]
    assert reasons[1:] == ["budget_exhausted", "budget_exhausted"]
    assert store.grades[("co2.example", "prod_a")][1] == "budget_exhausted"
    assert all(
        out[x.lead_id].company_usage.grade is CompanyGrade.UNVERIFIED for x in leads[1:]
    )


# Verifies: specs/user-recognition/requirements.md#7.3
def test_match_calls_only_for_verified_or_likely_companies():
    calls = []

    def match(sl):
        calls.append(sl.lead_id)
        return ()

    good = make_lead("good.example")
    bad = make_lead("bad.example", uid=False)
    run(
        [good, bad],
        {"vendor_customer": [hit("vendor_customer", "good")]},
        match=match,
    )
    assert calls == [good.lead_id]


def test_match_never_called_when_every_company_is_unverified():
    calls = []
    run([make_lead("bad.example", uid=False)], match=lambda sl: calls.append(1) or ())
    assert calls == []


def test_blocked_page_is_graded_from_its_snippet_and_flagged():
    lead = make_lead("acme.example", uid=False)
    blocked = SearchResult(
        "x Product A at acme", "https://blocked.example/p", "runs Product A", None
    )
    _, _, store = run([lead], {"third_party": [blocked]})
    assert [r.snippet_only for r in store.evidence] == [True]


def test_vendor_staff_partner_and_left_company_are_rejected_with_codes():
    staff = make_lead("vend.example")
    partner = make_lead("partner.example")
    left = make_lead("acme.example", current=False)
    out, _, _ = run(
        [staff, partner, left], {"vendor_customer": [hit("vendor_customer")]}
    )
    assert out[staff.lead_id].reason == "vendor_staff"
    assert out[partner.lead_id].reason == "vendor_partner"
    assert out[left.lead_id].reason == "left_company"


def test_linkedin_former_snippet_marks_left_company():
    lead = make_lead("acme.example", name="Pat Lee", uid=False)
    snippet = SearchResult(
        "x Pat Lee - Former engineer | LinkedIn",
        "https://www.linkedin.com/in/pat",
        "Pat Lee - Former Data lead at Acme. Left Acme in 2026. | LinkedIn",
        None,
    )
    out, _, _ = run([lead], {"linkedin_public": [snippet]})
    assert out[lead.lead_id].reason == "left_company"
