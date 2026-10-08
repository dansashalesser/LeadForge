from datetime import date, timedelta
from decimal import Decimal

import pytest

from leadforge.outreach.usage.grade import (
    CompanyGrade,
    GradeConfig,
    grade_company,
)
from leadforge.outreach.usage.records import (
    ClassifierStamp,
    EvidenceRecord,
    Relationship,
)
from leadforge.outreach.usage.records import (
    EvidenceClass as C,
)

R = Relationship
TODAY = date(2026, 10, 8)
FRESH = TODAY - timedelta(days=30)
STALE = TODAY - timedelta(days=731)
CFG = GradeConfig()


def rec(cls, rel=R.USES_NOW, on=FRESH, url="u"):
    return EvidenceRecord(
        company_key="acme",
        product_key="astra",
        evidence_class=cls,
        source="s",
        url=url,
        observed_on=on,
        quote="q",
        relationship=rel,
        confidence=Decimal("0.9"),
        snippet_only=False,
        classifier=ClassifierStamp(
            kind="offline", model=None, prompt_version="v", input_hash="h"
        ),
    )


def grade(records, **kw):
    kw.setdefault("include_ecosystem", True)
    kw.setdefault("is_ecosystem", False)
    return grade_company(records, CFG, today=TODAY, **kw)


V, L, U, N = (
    CompanyGrade.VERIFIED,
    CompanyGrade.LIKELY,
    CompanyGrade.UNVERIFIED,
    CompanyGrade.NEGATIVE,
)


# Verifies: specs/user-recognition/requirements.md#6.2
@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([], U),
        ([rec(C.JOB_POSTING)], L),
        ([rec(C.JOB_POSTING), rec(C.TECHNOGRAPHIC)], V),
        ([rec(C.JOB_POSTING), rec(C.OWN_DOMAIN_CONTENT)], V),
        ([rec(C.OWN_DOMAIN_CONTENT), rec(C.THIRD_PARTY_CONTENT)], L),
        ([rec(C.OWN_DOMAIN_CONTENT), rec(C.TECHNOGRAPHIC)], L),
        ([rec(C.PERSON_SELF_STATED), rec(C.TECHNOGRAPHIC)], L),
        ([rec(C.TECHNOGRAPHIC)], U),
        ([rec(C.PERSON_SELF_STATED)], U),
        ([rec(C.THIRD_PARTY_CONTENT), rec(C.TECHNOGRAPHIC)], L),
        ([rec(C.VENDOR_CUSTOMER_REF), rec(C.CODE_DEPENDENCY)], V),
        # five hosts, one class
        ([rec(C.JOB_POSTING, url=f"u{i}") for i in range(5)], L),
    ],
)
def test_grade_boundaries(records, expected):
    assert grade(records).grade is expected


# Verifies: specs/user-recognition/requirements.md#6.2
def test_technographic_only_reason():
    result = grade([rec(C.TECHNOGRAPHIC)])
    assert (result.grade, result.reason) == (U, "technographic_only")
    assert grade([]).reason == "no_usage_evidence"


# Verifies: specs/user-recognition/requirements.md#6.2
def test_provider_tag_counts_as_second_class():
    result = grade([rec(C.JOB_POSTING), rec(C.TECHNOGRAPHIC)])
    assert result.grade is V
    assert {r.evidence_class for r in result.records} == {
        C.JOB_POSTING,
        C.TECHNOGRAPHIC,
    }


# Verifies: specs/user-recognition/requirements.md#6.2
def test_newest_used_past_is_negative():
    old = rec(C.JOB_POSTING, on=TODAY - timedelta(days=90))
    past = rec(C.OWN_DOMAIN_CONTENT, R.USED_PAST, on=TODAY - timedelta(days=10))
    result = grade([old, rec(C.TECHNOGRAPHIC, on=old.observed_on), past])
    assert (result.grade, result.reason) == (N, "newest_used_past")
    assert result.records == (past,)


# Verifies: specs/user-recognition/requirements.md#6.2
def test_older_used_past_does_not_negate_newer_uses():
    past = rec(C.OWN_DOMAIN_CONTENT, R.USED_PAST, on=TODAY - timedelta(days=200))
    assert grade([past, rec(C.JOB_POSTING)]).grade is L


# Verifies: specs/user-recognition/requirements.md#6.2
def test_used_past_newer_than_one_class_blocks_it_from_verified():
    mid = TODAY - timedelta(days=100)
    records = [
        rec(C.JOB_POSTING, on=mid - timedelta(days=50)),
        rec(C.OWN_DOMAIN_CONTENT, R.USED_PAST, on=mid),
        rec(C.TECHNOGRAPHIC, on=FRESH),
    ]
    result = grade(records)
    assert result.grade is U
    assert result.reason == "technographic_only"


# Verifies: specs/user-recognition/requirements.md#6.4
def test_stale_record_downgrades_one_level():
    stale_job = rec(C.JOB_POSTING, on=STALE)
    assert grade([stale_job, rec(C.TECHNOGRAPHIC)]).grade is L
    assert grade([stale_job, rec(C.OWN_DOMAIN_CONTENT)]).grade is L
    assert (
        grade(
            [rec(C.JOB_POSTING, on=TODAY - timedelta(days=730)), rec(C.TECHNOGRAPHIC)]
        ).grade
        is V
    )


# Verifies: specs/user-recognition/requirements.md#6.4
def test_stale_self_stated_goes_weak_and_stops_pairing():
    stale = rec(C.PERSON_SELF_STATED, on=STALE)
    assert grade([stale, rec(C.TECHNOGRAPHIC)]).grade is U


# Verifies: specs/user-recognition/requirements.md#6.4
def test_undated_strong_capped_at_medium():
    undated = rec(C.JOB_POSTING, on=None)
    assert grade([undated, rec(C.TECHNOGRAPHIC)]).grade is L
    assert grade([undated, rec(C.OWN_DOMAIN_CONTENT)]).grade is L
    assert grade([undated]).grade is L


# Verifies: specs/user-recognition/requirements.md#6.4
def test_max_age_is_configurable():
    cfg = GradeConfig(max_evidence_age_days=10)
    two_classes = [rec(C.JOB_POSTING), rec(C.TECHNOGRAPHIC)]
    assert grade_company(two_classes, cfg, True, False, TODAY).grade is L


# Verifies: specs/user-recognition/requirements.md#6.5
def test_ecosystem_cap():
    records = [rec(C.JOB_POSTING), rec(C.TECHNOGRAPHIC)]
    capped = grade(records, is_ecosystem=True, include_ecosystem=False)
    assert (capped.grade, capped.reason) == (L, "ecosystem_cap")
    assert grade(records, is_ecosystem=True, include_ecosystem=True).grade is V
    assert grade(records, is_ecosystem=False, include_ecosystem=False).grade is V
    low = grade([rec(C.TECHNOGRAPHIC)], is_ecosystem=True, include_ecosystem=False)
    assert low.grade is U


# Verifies: specs/user-recognition/requirements.md#6.6
@pytest.mark.parametrize(
    "rel", [R.EVALUATING, R.VENDOR_OR_PARTNER, R.MENTIONS_ONLY, R.UNRELATED]
)
def test_non_usage_relationships_not_counted(rel):
    assert grade([rec(C.THIRD_PARTY_CONTENT, rel)]).grade is U
    result = grade([rec(C.JOB_POSTING, rel), rec(C.TECHNOGRAPHIC)])
    assert result.grade is U
    assert result.records == (rec(C.TECHNOGRAPHIC),)


# Verifies: specs/user-recognition/requirements.md#6.6
def test_non_usage_records_do_not_trigger_negative():
    assert (
        grade([rec(C.JOB_POSTING), rec(C.OWN_DOMAIN_CONTENT, R.EVALUATING)]).grade is L
    )
