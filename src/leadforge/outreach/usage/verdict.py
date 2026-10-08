"""Usage Verdict (Req 7.4, 8.1): exclusions first, then the matrix."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from leadforge.outreach.usage.grade import CompanyGrade, CompanyUsage
from leadforge.outreach.usage.person import PersonFit
from leadforge.outreach.usage.records import EvidenceRecord


class VerdictStatus(StrEnum):
    SELECTED = "selected"
    MANUAL_REVIEW = "manual_review"
    REJECTED = "rejected"


class Strictness(StrEnum):
    VERIFIED_ONLY = "verified_only"
    VERIFIED_PLUS_LIKELY = "verified_plus_likely"


class Exclusion(StrEnum):
    VENDOR_STAFF = "vendor_staff"
    VENDOR_PARTNER = "vendor_partner"
    LEFT_COMPANY = "left_company"


class UsageVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    status: VerdictStatus
    reason: str
    company_usage: CompanyUsage
    person_fit: PersonFit
    evidence_refs: tuple[EvidenceRecord, ...]


def _decide(
    company: CompanyUsage, fit: PersonFit, strictness: Strictness
) -> tuple[VerdictStatus, str]:
    S = VerdictStatus  # noqa: N806
    grade = company.grade
    if grade is CompanyGrade.NEGATIVE:
        return S.REJECTED, "usage_negative"
    if grade is CompanyGrade.UNVERIFIED:
        if company.reason == "technographic_only" and fit.grade == "core":
            return S.MANUAL_REVIEW, "technographic_only"
        return S.REJECTED, "usage_unproven"
    if grade is CompanyGrade.LIKELY and strictness is Strictness.VERIFIED_ONLY:
        return S.REJECTED, "strictness_verified_only"
    if fit.grade == "irrelevant":
        return S.REJECTED, "person_irrelevant"
    boosted = fit.boosted or fit.boost is not None
    if grade is CompanyGrade.VERIFIED:
        if fit.grade == "core":
            return S.SELECTED, "verified_core"
        if boosted:
            return S.SELECTED, "verified_adjacent_boosted"
        return S.MANUAL_REVIEW, "person_unboosted"
    # LIKELY: only core qualifies
    if fit.grade != "core":
        return S.REJECTED, "person_not_core"
    if boosted:
        return S.SELECTED, "likely_core_boosted"
    return S.MANUAL_REVIEW, "person_unboosted"


def verdict(
    company: CompanyUsage,
    fit: PersonFit,
    *,
    strictness: Strictness,
    exclusions: Iterable[Exclusion] = (),
) -> UsageVerdict:
    """Exclusions reject first (first one is the reason); else the Req 8.1 matrix."""
    refs = company.records + ((fit.boost,) if fit.boost is not None else ())
    excluded = tuple(exclusions)
    if excluded:
        status, reason = VerdictStatus.REJECTED, excluded[0].value
    else:
        status, reason = _decide(company, fit, strictness)
    return UsageVerdict(
        status=status,
        reason=reason,
        company_usage=company,
        person_fit=fit,
        evidence_refs=refs,
    )
