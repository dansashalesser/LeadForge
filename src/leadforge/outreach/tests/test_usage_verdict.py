"""Usage Verdict matrix (Req 7.4, 8.1): exclusions first, then every cell."""

from datetime import date
from decimal import Decimal

import pytest

from leadforge.outreach.usage.grade import CompanyGrade, CompanyUsage
from leadforge.outreach.usage.person import PersonFit
from leadforge.outreach.usage.records import (
    ClassifierStamp,
    EvidenceClass,
    EvidenceRecord,
    Relationship,
)
from leadforge.outreach.usage.verdict import (
    Exclusion,
    Strictness,
    VerdictStatus,
    verdict,
)

G = CompanyGrade
S = VerdictStatus
BOTH = Strictness.VERIFIED_PLUS_LIKELY


def rec(url="u"):
    return EvidenceRecord(
        company_key="acme",
        product_key="astra",
        evidence_class=EvidenceClass.TECHNOGRAPHIC,
        source="s",
        url=url,
        observed_on=date(2026, 9, 1),
        quote="q",
        relationship=Relationship.USES_NOW,
        confidence=Decimal("0.9"),
        snippet_only=False,
        classifier=ClassifierStamp(
            kind="offline", model=None, prompt_version="v", input_hash="h"
        ),
    )


def usage(grade, reason="r"):
    return CompanyUsage(grade=grade, reason=reason, records=(rec("c"),))


def fit(grade, boosted=False, boost=None):
    return PersonFit(grade, boosted=boosted, boost=boost)


# Verifies: specs/user-recognition/requirements.md#8.1
@pytest.mark.parametrize(
    ("company", "person", "strictness", "status", "reason"),
    [
        (usage(G.VERIFIED), fit("core"), BOTH, S.SELECTED, "verified_core"),
        (usage(G.VERIFIED), fit("core", True), BOTH, S.SELECTED, "verified_core"),
        (
            usage(G.VERIFIED),
            fit("adjacent", True),
            BOTH,
            S.SELECTED,
            "verified_adjacent_boosted",
        ),
        (usage(G.VERIFIED), fit("adjacent"), BOTH, S.MANUAL_REVIEW, "person_unboosted"),
        (usage(G.VERIFIED), fit("irrelevant"), BOTH, S.REJECTED, "person_irrelevant"),
        (usage(G.LIKELY), fit("core", True), BOTH, S.SELECTED, "likely_core_boosted"),
        (usage(G.LIKELY), fit("core"), BOTH, S.MANUAL_REVIEW, "person_unboosted"),
        (usage(G.LIKELY), fit("adjacent", True), BOTH, S.REJECTED, "person_not_core"),
        (usage(G.LIKELY), fit("adjacent"), BOTH, S.REJECTED, "person_not_core"),
        (usage(G.LIKELY), fit("irrelevant"), BOTH, S.REJECTED, "person_irrelevant"),
        (
            usage(G.UNVERIFIED, "technographic_only"),
            fit("core"),
            BOTH,
            S.MANUAL_REVIEW,
            "technographic_only",
        ),
        (
            usage(G.UNVERIFIED, "technographic_only"),
            fit("core", True),
            BOTH,
            S.MANUAL_REVIEW,
            "technographic_only",
        ),
        (
            usage(G.UNVERIFIED, "technographic_only"),
            fit("adjacent"),
            BOTH,
            S.REJECTED,
            "usage_unproven",
        ),
        (
            usage(G.UNVERIFIED, "technographic_only"),
            fit("irrelevant"),
            BOTH,
            S.REJECTED,
            "usage_unproven",
        ),
        (
            usage(G.UNVERIFIED, "no_usage_evidence"),
            fit("core"),
            BOTH,
            S.REJECTED,
            "usage_unproven",
        ),
        (
            usage(G.UNVERIFIED, "no_usage_evidence"),
            fit("adjacent"),
            BOTH,
            S.REJECTED,
            "usage_unproven",
        ),
        (
            usage(G.NEGATIVE, "newest_used_past"),
            fit("core", True),
            BOTH,
            S.REJECTED,
            "usage_negative",
        ),
        (
            usage(G.NEGATIVE, "newest_used_past"),
            fit("irrelevant"),
            BOTH,
            S.REJECTED,
            "usage_negative",
        ),
        # strictness verified only: every likely cell rejected
        (
            usage(G.LIKELY),
            fit("core", True),
            Strictness.VERIFIED_ONLY,
            S.REJECTED,
            "strictness_verified_only",
        ),
        (
            usage(G.LIKELY),
            fit("core"),
            Strictness.VERIFIED_ONLY,
            S.REJECTED,
            "strictness_verified_only",
        ),
        # verified cells and technographic review unaffected
        (
            usage(G.VERIFIED),
            fit("core"),
            Strictness.VERIFIED_ONLY,
            S.SELECTED,
            "verified_core",
        ),
        (
            usage(G.VERIFIED),
            fit("adjacent"),
            Strictness.VERIFIED_ONLY,
            S.MANUAL_REVIEW,
            "person_unboosted",
        ),
        (
            usage(G.UNVERIFIED, "technographic_only"),
            fit("core"),
            Strictness.VERIFIED_ONLY,
            S.MANUAL_REVIEW,
            "technographic_only",
        ),
    ],
)
def test_matrix_cell(company, person, strictness, status, reason):
    out = verdict(company, person, strictness=strictness)
    assert (out.status, out.reason) == (status, reason)
    assert out.company_usage is company
    assert out.person_fit is person


# Verifies: specs/user-recognition/requirements.md#7.4
@pytest.mark.parametrize("excl", list(Exclusion))
def test_exclusion_beats_best_cell(excl):
    out = verdict(
        usage(G.VERIFIED), fit("core", True), strictness=BOTH, exclusions=(excl,)
    )
    assert out.status is S.REJECTED
    assert out.reason == excl.value


def test_exclusion_codes():
    assert {e.value for e in Exclusion} == {
        "vendor_staff",
        "vendor_partner",
        "left_company",
    }


def test_first_exclusion_is_reason():
    out = verdict(
        usage(G.VERIFIED),
        fit("core"),
        strictness=BOTH,
        exclusions=(Exclusion.LEFT_COMPANY, Exclusion.VENDOR_STAFF),
    )
    assert out.reason == "left_company"


def test_evidence_refs_include_boost_record():
    boost = rec("boost")
    out = verdict(usage(G.VERIFIED), fit("adjacent", True, boost), strictness=BOTH)
    assert out.status is S.SELECTED
    assert [r.url for r in out.evidence_refs] == ["c", "boost"]


def test_evidence_refs_without_boost():
    out = verdict(usage(G.VERIFIED), fit("core"), strictness=BOTH)
    assert [r.url for r in out.evidence_refs] == ["c"]
