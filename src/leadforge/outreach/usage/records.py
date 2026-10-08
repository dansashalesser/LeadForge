"""Evidence Record: one classified observation about a company's product use."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceClass(StrEnum):
    VENDOR_CUSTOMER_REF = "vendor_customer_ref"
    JOB_POSTING = "job_posting"
    CODE_DEPENDENCY = "code_dependency"
    OWN_DOMAIN_CONTENT = "own_domain_content"
    THIRD_PARTY_CONTENT = "third_party_content"
    LINKEDIN_PUBLIC = "linkedin_public"
    TECHNOGRAPHIC = "technographic"
    PERSON_SELF_STATED = "person_self_stated"


class Relationship(StrEnum):
    USES_NOW = "uses_now"
    USED_PAST = "used_past"
    EVALUATING = "evaluating"
    VENDOR_OR_PARTNER = "vendor_or_partner"
    MENTIONS_ONLY = "mentions_only"
    UNRELATED = "unrelated"


class Strength(StrEnum):
    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClassifierStamp(_Frozen):
    kind: Literal["llm", "offline", "provider"]
    model: str | None
    prompt_version: str
    input_hash: str


class EvidenceRecord(_Frozen):
    company_key: str
    product_key: str
    evidence_class: EvidenceClass
    source: str
    url: str
    observed_on: date | None
    quote: str
    relationship: Relationship
    confidence: Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
    snippet_only: bool
    classifier: ClassifierStamp


def strength_of(
    record: EvidenceRecord, class_strengths: Mapping[EvidenceClass, Strength]
) -> Strength:
    """Strength of a record's class, from the configured `usage.class_strengths`."""
    return class_strengths[record.evidence_class]
