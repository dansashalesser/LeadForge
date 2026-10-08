from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from leadforge.outreach.usage.records import (
    ClassifierStamp,
    EvidenceClass,
    EvidenceRecord,
    Relationship,
    Strength,
    strength_of,
)

STRENGTHS = {c: Strength.MEDIUM for c in EvidenceClass}
STRENGTHS[EvidenceClass.VENDOR_CUSTOMER_REF] = Strength.STRONG
STRENGTHS[EvidenceClass.LINKEDIN_PUBLIC] = Strength.WEAK


def _stamp() -> ClassifierStamp:
    return ClassifierStamp(
        kind="offline", model=None, prompt_version="v1", input_hash="h"
    )


def _record(**kw) -> EvidenceRecord:
    base = dict(
        company_key="acme.com",
        product_key="hubspot",
        evidence_class=EvidenceClass.VENDOR_CUSTOMER_REF,
        source="serp",
        url="https://x.test/a",
        observed_on=date(2026, 1, 1),
        quote="we use HubSpot",
        relationship=Relationship.USES_NOW,
        confidence=Decimal("0.9"),
        snippet_only=False,
        classifier=_stamp(),
    )
    base.update(kw)
    return EvidenceRecord(**base)


# Verifies: specs/user-recognition/requirements.md#5.8
def test_vocabulary_is_exact():
    assert {c.value for c in EvidenceClass} == {
        "vendor_customer_ref",
        "job_posting",
        "code_dependency",
        "own_domain_content",
        "third_party_content",
        "linkedin_public",
        "technographic",
        "person_self_stated",
    }
    assert {r.value for r in Relationship} == {
        "uses_now",
        "used_past",
        "evaluating",
        "vendor_or_partner",
        "mentions_only",
        "unrelated",
    }


# Verifies: specs/user-recognition/requirements.md#6.1
def test_strength_comes_from_mapping():
    assert strength_of(_record(), STRENGTHS) is Strength.STRONG
    r = _record(evidence_class=EvidenceClass.LINKEDIN_PUBLIC)
    assert strength_of(r, STRENGTHS) is Strength.WEAK


def test_missing_class_in_mapping_fails_fast():
    with pytest.raises(KeyError):
        strength_of(_record(), {})


def test_record_is_frozen_and_validated():
    r = _record(observed_on=None)
    assert r.observed_on is None
    with pytest.raises(ValidationError):
        r.quote = "x"
    with pytest.raises(ValidationError):
        _record(confidence=Decimal("1.5"))
    with pytest.raises(ValidationError):
        _record(unknown=1)
    with pytest.raises(ValidationError):
        ClassifierStamp(kind="bogus", model=None, prompt_version="v", input_hash="h")
