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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from leadforge.outreach.errors import OutreachConfigError

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


class OutreachConfig(_Frozen):
    simulation: SimulationConfig
    sources: SourcesConfig
    qualify: QualifyConfig
    triggers: TriggerConfig
    messages: MessageConfig
    llm: LlmConfig
    outbox_path: Path


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
