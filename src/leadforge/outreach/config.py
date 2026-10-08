"""Outreach settings read from ``config/outreach.yaml`` (requirement 14.1).

Every weight, threshold, delay, timeout, length limit, retry bound, customer stage and
the outbox path comes from this file; none is written in code. A missing file, an
unknown key, or a bad value is an ``OutreachConfigError`` naming the file and the key
path (never the value), raised before anything else runs.
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from leadforge.outreach.errors import OutreachConfigError
from leadforge.outreach.usage.cues import UsageCues
from leadforge.outreach.usage.grade import GradeConfig
from leadforge.outreach.usage.records import EvidenceClass, Strength
from leadforge.outreach.usage.verdict import Strictness

__all__ = [
    "DEFAULT_OUTREACH_CONFIG_PATH",
    "LlmConfig",
    "MessageConfig",
    "OutreachConfig",
    "QualifyConfig",
    "QualifyWeights",
    "SimulationConfig",
    "SourcesConfig",
    "TriggerConfig",
    "UsageBudgetConfig",
    "UsageClassifierConfig",
    "UsageConfig",
    "UsageFetchConfig",
    "load_outreach_config",
    "read_yaml_mapping",
]

DEFAULT_OUTREACH_CONFIG_PATH = Path("config/outreach.yaml")

Weight = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Unit = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
Days = Annotated[int, Field(ge=0)]
Count = Annotated[int, Field(ge=1)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)


class QualifyWeights(_Frozen):
    competitor_evidence: Weight
    icp_fit: Weight
    intent: Weight
    contactability: Weight
    source_agreement: Weight

    def total(self) -> Decimal:
        return (
            self.competitor_evidence
            + self.icp_fit
            + self.intent
            + self.contactability
            + self.source_agreement
        )


class QualifyConfig(_Frozen):
    weights: QualifyWeights
    threshold: Unit
    customer_stages: tuple[Annotated[str, Field(min_length=1)], ...]


class TriggerConfig(_Frozen):
    accept_delay_days: Days
    invite_timeout_days: Days

    @property
    def accept_delay(self) -> timedelta:
        return timedelta(days=self.accept_delay_days)

    @property
    def invite_timeout(self) -> timedelta:
        return timedelta(days=self.invite_timeout_days)


class MessageConfig(_Frozen):
    invite_max_chars: Count
    email_subject_max_chars: Count
    email_max_chars: Count
    max_regenerations: Annotated[int, Field(ge=0)]
    # How many tech, intent and web-evidence facts of each kind a writer is shown.
    max_hook_facts: Count
    banned_phrases: tuple[Annotated[str, Field(min_length=1)], ...]


class LlmConfig(_Frozen):
    """Model defaults; the ``LEADFORGE_LLM_*`` environment variables override them."""

    provider: Annotated[str, Field(min_length=1)]
    model: Annotated[str, Field(min_length=1)]
    timeout_s: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    # Extra tries when the model returns a plan that does not validate.
    compile_retries: Annotated[int, Field(ge=0)]


class SourcesConfig(_Frozen):
    """Which source columns of a Target Profile take which kind of search.

    The outreach code names no source: it writes a profile with a domain filter under
    ``domain_filter`` (as ``{domain_key: [domain, ...]}``) and a plain phrase list under
    ``phrase_search``.
    """

    domain_filter: Annotated[str, Field(min_length=1)]
    domain_key: Annotated[str, Field(min_length=1)]
    phrase_search: Annotated[str, Field(min_length=1)]


class SimulationConfig(_Frozen):
    """Synthetic invite acceptance: the same seed gives the same acceptances."""

    seed: int
    accept_rate: Unit
    max_accept_days: Days


class UsageBudgetConfig(_Frozen):
    """Paid calls one users search may make (Req 4.5)."""

    searches: Annotated[int, Field(ge=0)] = 200
    fetches: Annotated[int, Field(ge=0)] = 300
    llm_calls: Annotated[int, Field(ge=0)] = 300


class UsageFetchConfig(_Frozen):
    max_bytes: Count = 1_000_000
    passage_chars: Count = 600


class UsageClassifierConfig(_Frozen):
    effort: Annotated[str, Field(min_length=1)] = "low"
    prompt: Annotated[str, Field(min_length=1)] = "usage_v1"


def _default_strengths() -> dict[EvidenceClass, Strength]:
    return dict(GradeConfig().class_strengths)


class UsageConfig(_Frozen):
    """The ``usage:`` section; every key is optional (design defaults)."""

    budget: UsageBudgetConfig = Field(default_factory=UsageBudgetConfig)
    max_evidence_age_days: Annotated[int, Field(gt=0)] = 730
    class_strengths: dict[EvidenceClass, Strength] = Field(
        default_factory=_default_strengths
    )
    strictness: Strictness = Strictness.VERIFIED_PLUS_LIKELY
    include_ecosystem: bool = False
    fetch: UsageFetchConfig = Field(default_factory=UsageFetchConfig)
    classifier: UsageClassifierConfig = Field(default_factory=UsageClassifierConfig)
    cues: UsageCues = Field(default_factory=UsageCues)

    @field_validator("class_strengths")
    @classmethod
    def _complete(
        cls, given: dict[EvidenceClass, Strength]
    ) -> dict[EvidenceClass, Strength]:
        return {**_default_strengths(), **given}

    def grade_config(self) -> GradeConfig:
        return GradeConfig(
            max_evidence_age_days=self.max_evidence_age_days,
            class_strengths=self.class_strengths,
        )


class OutreachConfig(_Frozen):
    simulation: SimulationConfig
    sources: SourcesConfig
    qualify: QualifyConfig
    triggers: TriggerConfig
    messages: MessageConfig
    llm: LlmConfig
    outbox_path: Path
    usage: UsageConfig = Field(default_factory=UsageConfig)


def read_yaml_mapping(path: str | Path) -> dict[str, object]:
    """The mapping a YAML file holds, or ``OutreachConfigError`` naming the file."""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as error:
        raise OutreachConfigError(
            str(file), key_path="", detail=f"cannot read file ({type(error).__name__})"
        ) from None
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        raise OutreachConfigError(
            str(file), key_path="", detail="not valid YAML"
        ) from None
    if not isinstance(document, dict):
        raise OutreachConfigError(
            str(file), key_path="", detail="the document must be a mapping"
        )
    return document


def load_outreach_config(path: str | Path | None = None) -> OutreachConfig:
    """The outreach settings, from ``path`` or ``DEFAULT_OUTREACH_CONFIG_PATH``."""
    file = Path(path) if path is not None else DEFAULT_OUTREACH_CONFIG_PATH
    document = read_yaml_mapping(file)
    try:
        config = OutreachConfig.model_validate(document)
    except ValidationError as error:
        first = error.errors(include_input=False, include_url=False)[0]
        raise OutreachConfigError(
            str(file),
            key_path=".".join(str(part) for part in first["loc"]),
            detail=str(first["type"]),
        ) from None
    if config.qualify.weights.total() <= 0:
        raise OutreachConfigError(
            str(file), key_path="qualify.weights", detail="the weights sum to zero"
        )
    return config
