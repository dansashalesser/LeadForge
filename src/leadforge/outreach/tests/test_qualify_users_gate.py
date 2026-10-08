"""Users-mode qualify: the verdict gates, the score only ranks (Req 7.4, 8.2, 8.3)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from leadforge.lead_ingestion.store.lead_reader import CrmState
from leadforge.outreach.config import load_outreach_config
from leadforge.outreach.decisions import Decision, Reason
from leadforge.outreach.qualify import MissingVerdictError, decide, decide_all
from leadforge.outreach.tests.support import (
    make_employment,
    make_lead,
    make_plan,
    make_stored,
)
from leadforge.outreach.usage.grade import CompanyGrade, CompanyUsage
from leadforge.outreach.usage.person import PersonFit
from leadforge.outreach.usage.records import (
    ClassifierStamp,
    EvidenceClass,
    EvidenceRecord,
    Relationship,
)
from leadforge.outreach.usage.verdict import UsageVerdict, VerdictStatus

CONFIG = load_outreach_config(
    Path(__file__).resolve().parents[4] / "config" / "outreach.yaml"
).qualify
LABELS = ("term a", "alias a")


def _rec(cls: EvidenceClass, url: str) -> EvidenceRecord:
    return EvidenceRecord(
        company_key="acme",
        product_key="astra",
        evidence_class=cls,
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


def _verdict(status: VerdictStatus, reason: str = "verified_core") -> UsageVerdict:
    refs = (
        _rec(EvidenceClass.TECHNOGRAPHIC, "https://a.example/1"),
        _rec(EvidenceClass.JOB_POSTING, "https://b.example/2"),
    )
    return UsageVerdict(
        status=status,
        reason=reason,
        company_usage=CompanyUsage(
            grade=CompanyGrade.VERIFIED, reason="r", records=refs
        ),
        person_fit=PersonFit("core"),
        evidence_refs=refs,
    )


def _codes(d: Decision) -> list[str]:
    return [r.code for r in d.reasons]


# Verifies: specs/user-recognition/requirements.md#7.4
def test_a_selected_verdict_passes_to_ranking_and_cites_its_evidence() -> None:
    d = decide(
        make_stored(),
        CrmState(),
        make_plan(),
        CONFIG,
        LABELS,
        _verdict(VerdictStatus.SELECTED),
    )
    assert d.status == "selected"
    refs = [ref for r in d.reasons for ref in r.evidence_refs]
    assert {ref.evidence_class for ref in refs} == {"technographic", "job_posting"}
    dumped = [ref.model_dump(mode="json", by_alias=True) for ref in refs]
    assert set(dumped[0]) == {
        "class",
        "source",
        "url",
        "observed_on",
        "quote",
        "relationship",
    }


# Verifies: specs/user-recognition/requirements.md#8.2
def test_the_users_score_leaves_out_competitor_evidence_and_intent() -> None:
    d = decide(
        make_stored(),
        CrmState(),
        make_plan(),
        CONFIG,
        LABELS,
        _verdict(VerdictStatus.SELECTED),
    )
    by_code = {r.code: r for r in d.reasons}
    assert by_code["competitor_evidence"].value is None
    assert by_code["intent"].value is None
    assert by_code["icp_fit"].value is not None


# Verifies: specs/user-recognition/requirements.md#8.2
def test_a_low_score_does_not_reject_a_selected_verdict_in_users_mode() -> None:
    stored = make_stored(
        make_lead(employments=(make_employment(tech=None, intent=None, title=None),))
    )
    d = decide(
        stored,
        CrmState(),
        make_plan(),
        CONFIG.model_copy(update={"threshold": Decimal(1)}),
        LABELS,
        _verdict(VerdictStatus.SELECTED),
    )
    assert d.status == "selected"
    assert "below_threshold" not in _codes(d)


# Verifies: specs/user-recognition/requirements.md#7.4
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (VerdictStatus.MANUAL_REVIEW, "manual_review"),
        (VerdictStatus.REJECTED, "rejected"),
    ],
)
def test_a_verdict_that_is_not_selected_is_never_selected_whatever_the_score(
    status: VerdictStatus, expected: str
) -> None:
    d = decide(
        make_stored(),
        CrmState(),
        make_plan(),
        CONFIG,
        LABELS,
        _verdict(status, "unverified_company"),
    )
    assert d.status == expected
    assert d.reasons[0].code == "unverified_company"
    assert isinstance(d.reasons[0], Reason)


# Verifies: specs/user-recognition/requirements.md#7.4
def test_hard_rejections_still_come_before_the_gate() -> None:
    stored = make_stored()
    crm = CrmState(has_open_deal=True)
    d = decide(
        stored, crm, make_plan(), CONFIG, LABELS, _verdict(VerdictStatus.SELECTED)
    )
    assert d.status == "rejected"
    assert _codes(d) == ["open_deal"]


# Verifies: specs/user-recognition/requirements.md#7.4
def test_a_users_lead_without_a_verdict_fails_fast() -> None:
    stored = make_stored()
    with pytest.raises(MissingVerdictError):
        decide_all([stored], {}, make_plan(), CONFIG, LABELS, {})


# Verifies: specs/user-recognition/requirements.md#8.3
def test_workers_mode_ignores_verdicts_and_keeps_its_score_terms() -> None:
    d = decide(make_stored(), CrmState(), make_plan(mode="workers"), CONFIG, LABELS)
    by_code = {r.code: r for r in d.reasons}
    assert by_code["intent"].value is not None
    assert all(not r.evidence_refs for r in d.reasons)
