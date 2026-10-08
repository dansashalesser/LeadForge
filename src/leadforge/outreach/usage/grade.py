"""Company Usage grade: independent-class evidence counting (Req 6.1-6.6). Pure."""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from leadforge.outreach.usage.records import (
    EvidenceClass,
    EvidenceRecord,
    Relationship,
    Strength,
    strength_of,
)

_NOT_CONTENT = frozenset(
    {EvidenceClass.TECHNOGRAPHIC, EvidenceClass.PERSON_SELF_STATED}
)
_ORDER = (Strength.WEAK, Strength.MEDIUM, Strength.STRONG)


def _default_strengths() -> dict[EvidenceClass, Strength]:
    strong = {
        EvidenceClass.VENDOR_CUSTOMER_REF,
        EvidenceClass.JOB_POSTING,
        EvidenceClass.CODE_DEPENDENCY,
    }
    weak = {EvidenceClass.THIRD_PARTY_CONTENT}
    return {
        c: Strength.STRONG
        if c in strong
        else Strength.WEAK
        if c in weak
        else Strength.MEDIUM
        for c in EvidenceClass
    }


class CompanyGrade(StrEnum):
    VERIFIED = "verified"
    LIKELY = "likely"
    UNVERIFIED = "unverified"
    NEGATIVE = "negative"


class GradeConfig(BaseModel):
    """Mirror of `usage.max_evidence_age_days` / `usage.class_strengths`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_evidence_age_days: int = Field(default=730, gt=0)
    class_strengths: Mapping[EvidenceClass, Strength] = Field(
        default_factory=_default_strengths
    )


class CompanyUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    grade: CompanyGrade
    reason: str
    records: tuple[EvidenceRecord, ...]


def _effective(record: EvidenceRecord, cfg: GradeConfig, today: date) -> Strength:
    strength = strength_of(record, cfg.class_strengths)
    idx = _ORDER.index(strength)
    if record.observed_on is None:
        idx = min(idx, _ORDER.index(Strength.MEDIUM))
    elif today - record.observed_on > timedelta(days=cfg.max_evidence_age_days):
        idx = max(idx - 1, 0)
    return _ORDER[idx]


def _when(record: EvidenceRecord) -> date:
    return record.observed_on or date.min


def grade_company(
    records: Sequence[EvidenceRecord],
    cfg: GradeConfig,
    include_ecosystem: bool,
    is_ecosystem: bool,
    today: date,
) -> CompanyUsage:
    """Grade a company/product from its Evidence Records.

    Only `uses_now` and `used_past` count; independence is by Evidence Class.
    Ties between `used_past` and `uses_now` dates resolve to `used_past`.
    """
    now = [r for r in records if r.relationship is Relationship.USES_NOW]
    past = [r for r in records if r.relationship is Relationship.USED_PAST]
    cutoff: date | None = None
    if past:
        newest_past = max(past, key=_when)
        if not now or _when(newest_past) >= max(_when(r) for r in now):
            return CompanyUsage(
                grade=CompanyGrade.NEGATIVE,
                reason="newest_used_past",
                records=(newest_past,),
            )
        cutoff = _when(newest_past)
    counted = tuple(r for r in now if cutoff is None or _when(r) > cutoff)

    strengths = {r: _effective(r, cfg, today) for r in counted}
    classes = {r.evidence_class for r in counted}
    content = [r for r in counted if r.evidence_class not in _NOT_CONTENT]
    has_tech = EvidenceClass.TECHNOGRAPHIC in classes
    strong_content = any(strengths[r] is Strength.STRONG for r in content)
    medium_other = any(
        strengths[r] is not Strength.WEAK
        for r in counted
        if r.evidence_class is not EvidenceClass.TECHNOGRAPHIC
    )

    if len(classes) >= 2 and strong_content:
        grade, reason = CompanyGrade.VERIFIED, "independent_classes"
    elif content:
        grade, reason = CompanyGrade.LIKELY, "content_class"
    elif has_tech and medium_other:
        grade, reason = CompanyGrade.LIKELY, "technographic_plus_medium"
    elif classes == {EvidenceClass.TECHNOGRAPHIC}:
        grade, reason = CompanyGrade.UNVERIFIED, "technographic_only"
    else:
        grade, reason = CompanyGrade.UNVERIFIED, "no_usage_evidence"

    if is_ecosystem and not include_ecosystem and grade is CompanyGrade.VERIFIED:
        grade, reason = CompanyGrade.LIKELY, "ecosystem_cap"
    return CompanyUsage(grade=grade, reason=reason, records=counted)
