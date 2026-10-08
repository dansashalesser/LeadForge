"""The usage stage: find evidence per company, grade it, decide per person.

Req 4.4, 4.5, 7.3.

Leads are grouped by employer domain. Each company gets a technographic record from
provider signals, then its query families are walked strongest first until it is
`verified` or the budget runs out. Records are persisted, then every Lead gets a
Person Fit and a `UsageVerdict`. Provider match credits are spent (through
``StageDeps.match_person``) only for people at `verified` or `likely` companies.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from leadforge.lead_ingestion.catalog import CatalogProduct, CatalogVendor
from leadforge.lead_ingestion.models import CanonicalLead, Employment
from leadforge.outreach.usage.budget import BUDGET_EXHAUSTED, UsageBudget
from leadforge.outreach.usage.classify import Classifier
from leadforge.outreach.usage.fetch import Passages, Skipped, should_fetch
from leadforge.outreach.usage.grade import (
    CompanyGrade,
    CompanyUsage,
    GradeConfig,
    grade_company,
)
from leadforge.outreach.usage.person import PersonFit, RoleVocabulary, grade_person_fit
from leadforge.outreach.usage.queries import QuerySpec, families
from leadforge.outreach.usage.records import (
    ClassifierStamp,
    EvidenceClass,
    EvidenceRecord,
    Relationship,
)
from leadforge.outreach.usage.serp import (
    SearchBudgetExhaustedError,
    SearchFailedError,
    SearchResult,
)
from leadforge.outreach.usage.store import UsageStore
from leadforge.outreach.usage.verdict import (
    Exclusion,
    Strictness,
    UsageVerdict,
    verdict,
)

__all__ = ["StageConfig", "StageDeps", "StageLead", "run_usage_stage"]

_FAMILY_CLASS = {
    "vendor_customer": EvidenceClass.VENDOR_CUSTOMER_REF,
    "job_posting": EvidenceClass.JOB_POSTING,
    "code": EvidenceClass.CODE_DEPENDENCY,
    "own_site": EvidenceClass.OWN_DOMAIN_CONTENT,
    "third_party": EvidenceClass.THIRD_PARTY_CONTENT,
    "linkedin_public": EvidenceClass.LINKEDIN_PUBLIC,
}
_FORMER = re.compile(r"\bformer\b", re.I)
_LEFT = re.compile(r"\bleft\b[^.]*\bin\s+(?:19|20)\d{2}\b", re.I)
_RANK = {
    CompanyGrade.VERIFIED: 3,
    CompanyGrade.LIKELY: 2,
    CompanyGrade.UNVERIFIED: 1,
    CompanyGrade.NEGATIVE: 0,
}
_PROVIDER_VERSION = "provider_v1"


class StageConfig(BaseModel):
    """Stage parameters; the defaults are the design defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    grade: GradeConfig = Field(default_factory=GradeConfig)
    strictness: Strictness = Strictness.VERIFIED_PLUS_LIKELY
    include_ecosystem: bool = False
    # True when classify() costs an LLM call, so the stage spends `llm_calls`.
    paid_classifier: bool = False
    roles: RoleVocabulary = Field(default_factory=RoleVocabulary)


@dataclass(frozen=True)
class StageLead:
    """One Lead as the stage sees it."""

    lead_id: uuid.UUID
    lead: CanonicalLead
    # title, headline, departments, functions, seniority of the person
    role_fields: tuple[str, ...] = ()
    # provider technology UIDs matched on this person (`matched_technology_uids`)
    matched_uids: tuple[str, ...] = ()


class _Searcher(Protocol):
    async def search(self, spec: QuerySpec) -> list[SearchResult]: ...


class _Fetcher(Protocol):
    def fetch_passages(self, url: str, aliases: list[str]) -> Passages | Skipped: ...


class _StampedClassifier(Classifier, Protocol):
    def stamp(
        self, target: str, product: CatalogProduct, passages: Passages
    ) -> ClassifierStamp: ...


@dataclass
class StageDeps:
    serp: _Searcher
    fetcher: _Fetcher
    classifier: _StampedClassifier
    store: UsageStore
    budget: UsageBudget
    search_id: uuid.UUID
    today: date
    # Spends provider match credits for one person; called only at verified/likely
    # companies. Returns extra role text the match found (may be empty).
    match_person: Callable[[StageLead], Sequence[str]] | None = None
    _spent: list[uuid.UUID] = field(default_factory=list, repr=False)


@dataclass
class _Company:
    key: str
    name: str
    domain: str
    leads: list[StageLead] = field(default_factory=list)
    signals: list[Any] = field(default_factory=list)  # TechSignal
    records: dict[str, list[EvidenceRecord]] = field(default_factory=dict)
    left_names: set[str] = field(default_factory=set)
    exhausted: bool = False


def _domain(raw: str) -> str:
    return raw.strip().lower().removeprefix("www.")


def _employer(lead: CanonicalLead) -> Employment | None:
    for e in lead.employments:
        if e.is_current is True:
            return e
    for e in lead.employments:
        if e.is_current is None:
            return e
    return lead.employments[0] if lead.employments else None


def _company_key(emp: Employment) -> tuple[str, str, str]:
    domains = emp.company.domains
    name = emp.company.name or (domains[0] if domains else "")
    if domains:
        d = _domain(domains[0])
        return d, name, d
    return name.strip().lower(), name, ""


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _technographic(
    co: _Company, product: CatalogProduct, uids: set[str]
) -> EvidenceRecord | None:
    wanted = set(product.technology_uids)
    hits = set(uids & wanted)
    source = "provider"
    for s in co.signals:
        if s.uid in wanted and s.current is not False:
            hits.add(s.uid)
            source = s.source or source
    if not hits:
        return None
    uid = sorted(hits)[0]
    return EvidenceRecord(
        company_key=co.key,
        product_key=product.key,
        evidence_class=EvidenceClass.TECHNOGRAPHIC,
        source=source,
        url=f"technographic:{source}",
        observed_on=None,
        quote=f"technology {uid}",
        relationship=Relationship.USES_NOW,
        confidence=Decimal("0.6"),
        snippet_only=False,
        classifier=ClassifierStamp(
            kind="provider",
            model=None,
            prompt_version=_PROVIDER_VERSION,
            input_hash=_hash(co.key, product.key, uid, source),
        ),
    )


def _when(result: SearchResult) -> date | None:
    try:
        return date.fromisoformat((result.date or "")[:10])
    except ValueError:
        return None


def _grade(
    co: _Company,
    product: CatalogProduct,
    ecosystem: bool,
    cfg: StageConfig,
    deps: StageDeps,
) -> CompanyUsage:
    return grade_company(
        co.records[product.key],
        cfg.grade,
        cfg.include_ecosystem,
        ecosystem,
        deps.today,
    )


def _classify(
    co: _Company,
    product: CatalogProduct,
    family: str,
    result: SearchResult,
    deps: StageDeps,
    cfg: StageConfig,
) -> EvidenceRecord | None:
    aliases = [a.text for a in product.aliases]
    got = deps.fetcher.fetch_passages(result.url, aliases)
    snippet_only = isinstance(got, Skipped)
    if isinstance(got, Skipped):
        if got.reason == BUDGET_EXHAUSTED:
            co.exhausted = True
            return None
        got = Passages(result.url, (f"{result.title}. {result.snippet}",))
    if cfg.paid_classifier and not deps.budget.spend("llm_calls"):
        co.exhausted = True
        return None
    j = deps.classifier.classify(co.name, product, got)
    if j is None or not j.subject_is_target_company:
        return None
    if j.relationship is Relationship.UNRELATED:
        return None
    return EvidenceRecord(
        company_key=co.key,
        product_key=product.key,
        evidence_class=_FAMILY_CLASS[family],
        source=family,
        url=result.url,
        observed_on=_when(result),
        quote=j.quotes[0] if j.quotes else result.snippet,
        relationship=j.relationship,
        confidence=Decimal(str(j.confidence)),
        snippet_only=snippet_only,
        classifier=deps.classifier.stamp(co.name, product, got),
    )


def _note_departures(co: _Company, results: list[SearchResult]) -> None:
    for r in results:
        text = f"{r.title}. {r.snippet}"
        if _FORMER.search(text) and _LEFT.search(text):
            co.left_names.add(text.lower())


async def _walk(
    co: _Company,
    product: CatalogProduct,
    ecosystem: bool,
    vendor: CatalogVendor,
    cfg: StageConfig,
    deps: StageDeps,
) -> None:
    if not co.domain or not vendor.domains:
        return
    aliases = [a.text for a in product.aliases]
    specs = families(co.name, aliases, vendor.domains[0], co.domain)
    for spec in specs:
        if _grade(co, product, ecosystem, cfg, deps).grade is CompanyGrade.VERIFIED:
            return
        try:
            results = await deps.serp.search(spec)
        except SearchBudgetExhaustedError:
            co.exhausted = True
            return
        except SearchFailedError:
            continue
        if spec.family == "linkedin_public":
            _note_departures(co, results)
        for r in results:
            if not should_fetch(f"{r.title} {r.snippet}", aliases):
                continue
            rec = _classify(co, product, spec.family, r, deps, cfg)
            if co.exhausted:
                return
            if rec is not None:
                co.records[product.key].append(rec)
                if (
                    _grade(co, product, ecosystem, cfg, deps).grade
                    is CompanyGrade.VERIFIED
                ):
                    return


def _resolve(
    vendor: CatalogVendor, keys: Sequence[str]
) -> list[tuple[CatalogProduct, bool]]:
    known = {p.key: (p, False) for p in vendor.products}
    known.update({p.key: (p, True) for p in vendor.ecosystem})
    missing = [k for k in keys if k not in known]
    if missing:
        raise ValueError(f"unknown product keys: {missing}")
    return [known[k] for k in keys]


def _persist(co: _Company, usage: dict[str, CompanyUsage], deps: StageDeps) -> None:
    for product_key, records in co.records.items():
        ids = [str(deps.store.append_evidence(deps.search_id, r)) for r in records]
        u = usage[product_key]
        deps.store.upsert_company_grade(
            deps.search_id, co.key, product_key, u.grade.value, u.reason, ids
        )


def _exclusions(
    sl: StageLead,
    co: _Company,
    vendor: CatalogVendor,
    records: Sequence[EvidenceRecord],
) -> list[Exclusion]:
    out: list[Exclusion] = []
    if co.domain and co.domain in {_domain(d) for d in vendor.domains}:
        out.append(Exclusion.VENDOR_STAFF)
    partner = co.domain in {_domain(d) for d in vendor.partner_domains} or any(
        r.relationship is Relationship.VENDOR_OR_PARTNER for r in records
    )
    if partner:
        out.append(Exclusion.VENDOR_PARTNER)
    emp = _employer(sl.lead)
    name = (sl.lead.full_name or "").lower()
    if (emp is not None and emp.is_current is False) or (
        name and any(name in text for text in co.left_names)
    ):
        out.append(Exclusion.LEFT_COMPANY)
    return out


def _person_fit(
    sl: StageLead,
    co: _Company,
    product: CatalogProduct,
    usage: CompanyUsage,
    cfg: StageConfig,
    deps: StageDeps,
) -> PersonFit:
    fields = list(sl.role_fields)
    if usage.grade in (CompanyGrade.VERIFIED, CompanyGrade.LIKELY):
        if deps.match_person is not None:
            fields += list(deps.match_person(sl))
    else:
        return PersonFit("irrelevant")
    aliases = [a.text for a in product.aliases]
    mention = next((f for f in fields if should_fetch(f, aliases)), None)
    confirmed: EvidenceRecord | None = None
    if mention is not None and (
        not cfg.paid_classifier or deps.budget.spend("llm_calls")
    ):
        got = Passages(str(sl.lead.linkedin_url or ""), (mention,))
        j = deps.classifier.classify(co.name, product, got)
        if (
            j
            and j.subject_is_target_company
            and j.relationship is Relationship.USES_NOW
        ):
            confirmed = EvidenceRecord(
                company_key=co.key,
                product_key=product.key,
                evidence_class=EvidenceClass.PERSON_SELF_STATED,
                source="person",
                url=got.url or f"person:{sl.lead_id}",
                observed_on=None,
                quote=j.quotes[0] if j.quotes else mention,
                relationship=Relationship.USES_NOW,
                confidence=Decimal(str(j.confidence)),
                snippet_only=True,
                classifier=deps.classifier.stamp(co.name, product, got),
            )
    fit = grade_person_fit(
        fields,
        cfg.roles,
        self_stated=mention is not None,
        self_stated_confirmed=confirmed is not None,
    )
    if fit.boosted and confirmed is not None:
        fit = replace(fit, boost=confirmed)
        deps.store.append_evidence(deps.search_id, confirmed)
    return fit


async def run_usage_stage(
    leads: Sequence[StageLead],
    vendor: CatalogVendor,
    product_keys: Sequence[str],
    deps: StageDeps,
    cfg: StageConfig | None = None,
) -> dict[uuid.UUID, UsageVerdict]:
    """Verdict per lead id. Companies are walked in first-seen order."""
    cfg = cfg or StageConfig()
    products = _resolve(vendor, product_keys)
    companies: dict[str, _Company] = {}
    employer_of: dict[uuid.UUID, str] = {}
    for sl in leads:
        emp = _employer(sl.lead)
        if emp is None:
            continue
        key, name, domain = _company_key(emp)
        co = companies.setdefault(key, _Company(key, name, domain))
        co.leads.append(sl)
        co.signals += list(emp.company.tech_signals) + list(sl.lead.tech_signals)
        employer_of[sl.lead_id] = key

    usage: dict[str, dict[str, CompanyUsage]] = {}
    for co in companies.values():
        uids = {u for sl in co.leads for u in sl.matched_uids}
        for product, _ in products:
            tech = _technographic(co, product, uids)
            co.records[product.key] = [tech] if tech else []
        for product, eco in products:
            if not co.exhausted:
                await _walk(co, product, eco, vendor, cfg, deps)
        per: dict[str, CompanyUsage] = {}
        for product, eco in products:
            u = _grade(co, product, eco, cfg, deps)
            if co.exhausted and u.grade in (CompanyGrade.UNVERIFIED,):
                u = u.model_copy(update={"reason": BUDGET_EXHAUSTED})
            per[product.key] = u
        usage[co.key] = per
        _persist(co, per, deps)

    out: dict[uuid.UUID, UsageVerdict] = {}
    for sl in leads:
        key = employer_of.get(sl.lead_id)
        if key is None:
            continue
        co = companies[key]
        best_product, _ = max(products, key=lambda p: _RANK[usage[key][p[0].key].grade])
        best = usage[key][best_product.key]
        fit = _person_fit(sl, co, best_product, best, cfg, deps)
        records = [r for rs in co.records.values() for r in rs]
        out[sl.lead_id] = verdict(
            best,
            fit,
            strictness=cfg.strictness,
            exclusions=_exclusions(sl, co, vendor, records),
        )
    return out
