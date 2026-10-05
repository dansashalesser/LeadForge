"""Canonical entities: Lead, Company Signal, Employment, and Signals.

A Lead is exactly one human. Company-level data is a separate ``CompanySignal``,
joined to a Lead only through an ``Employment``; the Lead itself carries no employer
attribute. One ``CompanySignal`` is shared by every Lead employed there.
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    StrictStr,
    field_validator,
    model_validator,
)

from leadforge.lead_ingestion.errors import ConflictingCompanySignalError

__all__ = [
    "CanonicalLead",
    "CompanySignal",
    "ConfidenceOrigin",
    "ConflictingCompanySignalError",
    "DataMode",
    "EmailStatus",
    "Employment",
    "FieldProvenance",
    "IntentSignal",
    "ProviderCompanyId",
    "Signal",
    "TechSignal",
    "UntrustedText",
    "share_company_signals",
]


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


NonBlank = Annotated[str, AfterValidator(_non_blank)]

# Signal Strength: finite, 0.0 (no weight) to 1.0 (maximum weight).
Strength = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


class _Entity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmailStatus(StrEnum):
    VERIFIED = "verified"
    ACCEPT_ALL = "accept_all"
    UNVERIFIED = "unverified"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class DataMode(StrEnum):
    LIVE = "live"
    SYNTHETIC = "synthetic"


class ConfidenceOrigin(StrEnum):
    """Whether a Field Confidence was stated by the provider or inferred by us."""

    PROVIDER_STATED = "provider_stated"
    HEURISTIC = "heuristic"
    NONE = "none"


class UntrustedText(_Entity):
    """Provider free text (snippets, bios, descriptions), kept apart from ``str``.

    The wrapper is the guardrail: it is not a ``str``, ``str()`` and f-string
    formatting raise ``TypeError``, and ``repr`` hides the payload, so the text cannot
    be interpolated into an instruction by accident. Read it only through ``.value``.
    ``truncated`` and ``original_length`` record any cut made to the configured
    maximum length; the cutting itself belongs to the normalizer.
    """

    value: StrictStr
    truncated: bool
    original_length: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _length_matches_truncation(self) -> Self:
        kept = len(self.value)
        if self.truncated and self.original_length <= kept:
            raise ValueError("truncated text must have been longer than what is kept")
        if not self.truncated and self.original_length != kept:
            raise ValueError("untruncated text must record its own length")
        return self

    def __str__(self) -> str:
        raise TypeError("UntrustedText has no implicit str conversion; use .value")

    def __repr__(self) -> str:
        return (
            f"UntrustedText(<{len(self.value)} chars withheld>, "
            f"truncated={self.truncated}, original_length={self.original_length})"
        )


class FieldProvenance(_Entity):
    """Where one populated canonical field came from, and how sure the source was.

    ``confidence`` is the Field Confidence normalized to 0.0-1.0 for comparison only;
    ``confidence_raw`` and ``confidence_scale`` keep the provider's own value and the
    name of the scale it was expressed on. When ``confidence_origin`` is ``NONE`` the
    provider stated no certainty and all three are ``None`` - never a default number.
    ``superseded`` marks a losing contribution retained rather than dropped.
    ``untrusted`` marks the field as untrusted external provider text.
    """

    canonical_path: NonBlank
    source_name: NonBlank
    data_mode: DataMode
    fetched_at: AwareDatetime
    raw_field_path: NonBlank
    confidence_origin: ConfidenceOrigin
    confidence: Strength | None = None
    confidence_raw: NonBlank | None = None
    confidence_scale: NonBlank | None = None
    superseded: bool = False
    untrusted: bool = False

    @model_validator(mode="after")
    def _confidence_matches_origin(self) -> Self:
        stated = (self.confidence_raw, self.confidence_scale)
        match self.confidence_origin:
            case ConfidenceOrigin.NONE:
                if self.confidence is not None or any(v is not None for v in stated):
                    raise ValueError("origin 'none' must carry no confidence payload")
            case ConfidenceOrigin.PROVIDER_STATED:
                if any(v is None for v in stated):
                    raise ValueError(
                        "a provider-stated confidence needs its raw value and scale"
                    )
            case ConfidenceOrigin.HEURISTIC:
                if self.confidence is None:
                    raise ValueError("a heuristic confidence needs a number")
                if any(v is not None for v in stated):
                    raise ValueError(
                        "a heuristic confidence claims no provider raw value or scale"
                    )
        return self


class Signal(_Entity):
    """One piece of evidence, carrying its own Signal Strength."""

    label: NonBlank
    strength: Strength


class TechSignal(Signal):
    """Technographic evidence."""


class IntentSignal(Signal):
    """Intent evidence."""


def _reject_padded_email(value: object) -> object:
    # EmailStr strips surrounding whitespace; the design forbids silent coercion.
    if isinstance(value, str) and value != value.strip():
        raise ValueError("email must not have surrounding whitespace")
    return value


StrictEmail = Annotated[EmailStr, BeforeValidator(_reject_padded_email)]


def _check_domain(value: str) -> str:
    if not value or any(ch.isspace() for ch in value):
        raise ValueError("domain must be non-empty and contain no whitespace")
    return value


class ProviderCompanyId(_Entity):
    """One provider's own identifier for a company, kept for traceability only."""

    source: NonBlank
    id: NonBlank


class CompanySignal(_Entity):
    """Organization-level information; carries no person identity.

    ``company_id`` is the one canonical identifier, assigned by this layer and never
    taken from a provider. Each provider's native id is normalized onto it and kept
    in ``provider_ids``, so every provider's view of one company shares one id.
    """

    company_id: NonBlank
    provider_ids: tuple[ProviderCompanyId, ...] = ()
    name: NonBlank | None = None
    domains: tuple[Annotated[str, AfterValidator(_check_domain)], ...] = ()
    tech_signals: tuple[TechSignal, ...] = ()
    intent_signals: tuple[IntentSignal, ...] = ()

    @field_validator("domains")
    @classmethod
    def _domains_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("domain listed twice")
        return value

    @field_validator("provider_ids")
    @classmethod
    def _one_native_id_per_source(
        cls, value: tuple[ProviderCompanyId, ...]
    ) -> tuple[ProviderCompanyId, ...]:
        if len({p.source for p in value}) != len(value):
            raise ValueError("provider_ids lists one source twice")
        return value

    @model_validator(mode="after")
    def _has_organization_identity(self) -> Self:
        if self.name is None and not self.domains:
            raise ValueError("a Company Signal needs an organization name or a domain")
        return self


class Employment(_Entity):
    """A Lead's relationship to one Company Signal, current or historical.

    ``is_current`` is ``None`` when the provider did not say; unknown is not past.
    """

    company: CompanySignal
    title: NonBlank | None = None
    is_current: bool | None = None


class CanonicalLead(_Entity):
    """The only lead type a downstream stage may consume: one identifiable human."""

    email: StrictEmail | None = None
    email_status: EmailStatus = EmailStatus.UNKNOWN
    linkedin_url: HttpUrl | None = None
    full_name: NonBlank | None = None
    employments: tuple[Employment, ...] = ()
    tech_signals: tuple[TechSignal, ...] = ()
    intent_signals: tuple[IntentSignal, ...] = ()
    opt_out: bool = False
    suppressed: bool = False

    @property
    def current_employments(self) -> tuple[Employment, ...]:
        """Employments a provider reported as current; unknown is not current."""
        return tuple(e for e in self.employments if e.is_current is True)

    @property
    def has_multiple_current_employments(self) -> bool:
        """Flag for lead scoring: providers disagree or the person holds two jobs."""
        return len(self.current_employments) > 1

    @model_validator(mode="after")
    def _has_person_identity(self) -> Self:
        if self.email is None and self.linkedin_url is None and self.full_name is None:
            raise ValueError(
                "a Lead needs person identity: email, linkedin_url, or full_name"
            )
        return self

    @model_validator(mode="after")
    def _email_status_needs_email(self) -> Self:
        if self.email is None and self.email_status is not EmailStatus.UNKNOWN:
            raise ValueError(
                f"email_status {self.email_status.value!r} given without an email"
            )
        return self

    @model_validator(mode="after")
    def _employments_are_consistent(self) -> Self:
        if len(set(self.employments)) != len(self.employments):
            raise ValueError("duplicate Employment")
        seen: dict[str, CompanySignal] = {}
        for e in self.employments:
            known = seen.setdefault(e.company.company_id, e.company)
            if known != e.company:
                raise ValueError(
                    f"company_id {e.company.company_id!r} names two different "
                    "Company Signals within one Lead"
                )
        return self


def share_company_signals(leads: Iterable[CanonicalLead]) -> tuple[CanonicalLead, ...]:
    """Return ``leads`` with one shared ``CompanySignal`` instance per ``company_id``.

    Equal copies collapse to the first-seen instance; two Company Signals with one
    ``company_id`` but different content raise ``ConflictingCompanySignalError``.
    Input leads are not modified; leads that already share are returned as-is.
    """
    batch = tuple(leads)
    shared: dict[str, CompanySignal] = {}
    for ld in batch:
        for e in ld.employments:
            known = shared.setdefault(e.company.company_id, e.company)
            if known != e.company:
                raise ConflictingCompanySignalError(e.company.company_id)

    def _share(ld: CanonicalLead) -> CanonicalLead:
        if all(shared[e.company.company_id] is e.company for e in ld.employments):
            return ld
        employments = tuple(
            e.model_copy(update={"company": shared[e.company.company_id]})
            for e in ld.employments
        )
        return ld.model_copy(update={"employments": employments})

    return tuple(_share(ld) for ld in batch)
