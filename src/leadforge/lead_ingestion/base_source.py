"""The adapter contract: what every lead source declares and implements (task 3.1).

Normalization returns ``LeadContribution`` records, never a ``CanonicalLead``; only the
Merge Engine builds that projection. Each adapter declares the canonical paths its API
can answer for (``answerable_surfaces``), so "could answer" versus "never able to
answer" is data, and every ``SourceAbsence`` is checked against it at the boundary.
"""

import os
from abc import ABC, abstractmethod
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Literal, Self

from pydantic import Field, model_validator

from leadforge.lead_ingestion.errors import InvalidAbsenceError, MissingCredentialError
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    DataMode,
    FieldProvenance,
    NonBlank,
    SourceAbsence,
    UntrustedText,
    _Entity,
)

__all__ = [
    "TARGET_TERM_PATH_PREFIX",
    "BaseLeadSource",
    "Capability",
    "ChargeUnit",
    "CostClass",
    "Endpoint",
    "LeadContribution",
    "LiveAccess",
    "RateBucket",
    "RateWindow",
    "RawBatch",
    "SourceRequest",
    "enrichment_order",
    "enrichment_sort_key",
    "resolve_credentials",
]


class Capability(StrEnum):
    SEARCH = "search"  # Discovery
    ENRICH = "enrich"  # Enrichment


class CostClass(StrEnum):
    FREE = "free"
    PAID = "paid"  # Credit-bearing


class LiveAccess(StrEnum):
    """Whether a provider can run live for a demo operator (3.6)."""

    AVAILABLE = "available"
    GATED = "gated"  # live is possible only behind an approval or plan we may lack
    UNAVAILABLE = "unavailable"  # decided synthetic-only


class ChargeUnit(StrEnum):
    PER_LEAD = "per_lead"
    PER_COMPANY = "per_company"
    PER_CALL = "per_call"


# Fewest billable events first: a per-company source is called once per company, a
# per-call source once per request, a per-lead source once for every lead.
_CHARGE_UNIT_RANK = {
    ChargeUnit.PER_COMPANY: 0,
    ChargeUnit.PER_CALL: 1,
    ChargeUnit.PER_LEAD: 2,
}


@dataclass(frozen=True)
class RateWindow:
    requests: int
    per_seconds: float


@dataclass(frozen=True)
class RateBucket:
    """One named limit; its windows are ANDed."""

    name: str
    windows: tuple[RateWindow, ...]
    documented: bool
    doc_url: str


def _is_provider_path(path: str) -> bool:
    """A path on the adapter's own host: '/a/b', no URL, query, '..' or '//'."""
    if not path.startswith("/") or path.startswith("//"):
        return False
    if any(c in path for c in "?#\\") or any(
        c.isspace() or not c.isprintable() for c in path
    ):
        return False
    return not any(seg in (".", "..") for seg in path.split("/")) and "//" not in path


@dataclass(frozen=True)
class Endpoint:
    """One provider path an adapter may reach; every call goes through a declared one.

    ``read_only`` is ``Literal[True]``, so a write endpoint is a type error, and it is
    also rejected at runtime for callers the type checker does not see (11.1, 11.2).
    POST is allowed because several read-only search APIs take their query in a body.
    """

    method: Literal["GET", "POST"]
    path: str
    bucket: str  # name of a RateBucket in the adapter's ``rate_limit``
    read_only: Literal[True] = True

    def __post_init__(self) -> None:
        if self.read_only is not True:
            raise ValueError("Endpoint.read_only must be True; no write endpoints")
        if self.method not in ("GET", "POST"):
            raise ValueError(
                f"Endpoint.method must be GET or POST, got {self.method!r}"
            )
        for field in ("path", "bucket"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Endpoint.{field} must be a non-blank str")
        if not _is_provider_path(self.path):
            raise ValueError(
                f"Endpoint.path must be a root-relative path on the provider host, "
                f"got {self.path!r}"
            )


class SourceRequest(_Entity):
    kind: NonBlank


class RawBatch(_Entity):
    """A provider payload kept verbatim; schema validation happens later."""

    source_name: NonBlank
    payload: Any


class LeadContribution(_Entity):
    """What one source says about a lead; merged into a ``CanonicalLead`` elsewhere."""

    source_name: NonBlank
    absences: tuple[SourceAbsence, ...] = ()
    # Canonical path -> the value this source supplied; one provenance record each.
    values: Mapping[str, Any] = Field(default_factory=dict)
    provenance: tuple[FieldProvenance, ...] = ()

    @model_validator(mode="after")
    def _provenance_matches_values(self) -> Self:
        paths = [p.canonical_path for p in self.provenance]
        if len(set(paths)) != len(paths) or set(paths) != set(self.values):
            raise ValueError(
                "values and provenance must name the same canonical paths, "
                "one provenance record each"
            )
        for record in self.provenance:
            if record.source_name != self.source_name:
                raise ValueError("provenance attributed to a different source")
            wrapped = isinstance(self.values[record.canonical_path], UntrustedText)
            if wrapped != record.untrusted:
                raise ValueError(
                    f"untrusted marking of {record.canonical_path!r} must match "
                    "whether its value is UntrustedText"
                )
        return self


TARGET_TERM_PATH_PREFIX = "target_profile."


def _is_empty_vocabulary(value: object) -> bool:
    """A vocabulary is empty if it is None, a blank str, or an empty collection.

    Any other value, including 0 and False, is a real provider identifier.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Collection):
        return len(value) == 0
    return False


# Mapping declarations are copied into read-only views when a subclass is defined.
_FROZEN_MAPPINGS = (
    "rate_limit",
    "answerable_surfaces",
    "target_vocabulary",
    "endpoints",
)

_DECLARATIONS = (
    "name",
    "capabilities",
    "rate_limit",
    "answerable_surfaces",
    "cost_class",
    "charge_unit",
    "yields_suppression",
    "target_vocabulary",
    "endpoints",
    "required_env",
)


def _is_env_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and "=" not in name
        and "\0" not in name
        and not any(c.isspace() for c in name)
    )


class BaseLeadSource(ABC):
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]]  # may be empty
    rate_limit: ClassVar[Mapping[str, RateBucket]]
    # Canonical path the API can answer for -> raw field paths it can be asked on.
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]]
    # Enrichment ordering is derived from these, never hand-listed (2.7).
    cost_class: ClassVar[CostClass]
    charge_unit: ClassVar[ChargeUnit]
    yields_suppression: ClassVar[bool]
    # Whether the provider can run live for a demo operator (3.6). Defaulted, so a new
    # adapter stays one class; a configuration override may replace it per source.
    live_access: ClassVar[LiveAccess] = LiveAccess.AVAILABLE
    # Canonical Target Profile term -> this provider's opaque vocabulary for it.
    # An empty value (or an absent term) is Not Applicable, never "no match" (2.8).
    target_vocabulary: ClassVar[Mapping[str, object]]
    # Every provider path this adapter may reach, keyed by a local label (11.1).
    endpoints: ClassVar[Mapping[str, Endpoint]]
    # Names of the environment variables holding credentials; values are never declared
    # here, only resolved from the process environment (2.5, 10.4).
    required_env: ClassVar[tuple[str, ...]]
    # Provider documentation page named in the generated credential example file (10.2).
    # Defaulted, so a keyless adapter declares nothing; when blank, the generator falls
    # back to the first rate bucket's doc_url and fails if an adapter with credentials
    # has neither.
    docs_url: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for declaration in _FROZEN_MAPPINGS:
            # getattr, not __dict__: an inherited mixin dict must be frozen too.
            declared = getattr(cls, declaration, None)
            if isinstance(declared, Mapping):
                # Always copy, so neither the author's dict nor a proxy over one
                # can be used to mutate the declaration.
                setattr(cls, declaration, MappingProxyType(dict(declared)))

    def __init__(self, mode: DataMode) -> None:
        cls = type(self).__name__
        missing = [d for d in _DECLARATIONS if not hasattr(type(self), d)]
        if missing:
            raise TypeError(f"{cls} does not declare: {', '.join(missing)}")
        if not isinstance(self.name, str) or not self.name.strip():
            raise TypeError(f"{cls}.name must be a non-blank str")
        if not isinstance(self.capabilities, frozenset) or not all(
            isinstance(c, Capability) for c in self.capabilities
        ):
            raise TypeError(f"{cls}.capabilities must be a frozenset of Capability")
        if not isinstance(self.rate_limit, Mapping):
            raise TypeError(f"{cls}.rate_limit must be a Mapping")
        if not isinstance(self.answerable_surfaces, Mapping):
            raise TypeError(f"{cls}.answerable_surfaces must be a Mapping")
        if not isinstance(self.cost_class, CostClass):
            raise TypeError(f"{cls}.cost_class must be a CostClass")
        if not isinstance(self.charge_unit, ChargeUnit):
            raise TypeError(f"{cls}.charge_unit must be a ChargeUnit")
        if not isinstance(self.yields_suppression, bool):
            raise TypeError(f"{cls}.yields_suppression must be a bool")
        if not isinstance(self.live_access, LiveAccess):
            raise TypeError(f"{cls}.live_access must be a LiveAccess")
        for path, surfaces in self.answerable_surfaces.items():
            # A bare str would make `in` a substring test, so demand a real set.
            if not isinstance(surfaces, frozenset) or not all(
                isinstance(s, str) and s.strip() for s in surfaces
            ):
                raise TypeError(
                    f"{cls}.answerable_surfaces[{path!r}] must be a frozenset of "
                    "non-blank str"
                )
            if not surfaces:
                raise TypeError(
                    f"{cls}.answerable_surfaces[{path!r}] has no surface; "
                    "leave an unanswerable path out instead"
                )
        if not isinstance(self.target_vocabulary, Mapping):
            raise TypeError(f"{cls}.target_vocabulary must be a Mapping")
        for term in self.target_vocabulary:
            if not isinstance(term, str) or not term.strip():
                raise TypeError(f"{cls}.target_vocabulary keys must be non-blank str")
        answerable = {
            path.removeprefix(TARGET_TERM_PATH_PREFIX)
            for path in self.answerable_surfaces
            if path.startswith(TARGET_TERM_PATH_PREFIX)
        }
        expressible = {
            term
            for term, vocab in self.target_vocabulary.items()
            if not _is_empty_vocabulary(vocab)
        }
        if answerable != expressible:
            raise TypeError(
                f"{cls} target terms must match: answerable without vocabulary "
                f"{sorted(answerable - expressible)}, vocabulary without answerable "
                f"surface {sorted(expressible - answerable)}"
            )
        self._validate_endpoints(cls)
        self._validate_required_env(cls)
        self._mode = mode

    def _validate_endpoints(self, cls: str) -> None:
        if not isinstance(self.endpoints, Mapping):
            raise TypeError(f"{cls}.endpoints must be a Mapping")
        for label, endpoint in self.endpoints.items():
            if not isinstance(label, str) or not label.strip():
                raise TypeError(f"{cls}.endpoints keys must be non-blank str")
            # Exact type: a subclass could override the checks in __post_init__.
            if type(endpoint) is not Endpoint or endpoint.read_only is not True:
                raise TypeError(
                    f"{cls}.endpoints[{label!r}] must be a read-only Endpoint"
                )
            if endpoint.bucket not in self.rate_limit:
                raise TypeError(
                    f"{cls}.endpoints[{label!r}] uses undeclared rate bucket "
                    f"{endpoint.bucket!r}"
                )

    def _validate_required_env(self, cls: str) -> None:
        names = self.required_env
        if not isinstance(names, tuple) or not all(_is_env_name(n) for n in names):
            raise TypeError(
                f"{cls}.required_env must be a tuple of environment variable names"
            )
        if len(set(names)) != len(names):
            raise TypeError(f"{cls}.required_env must not repeat a name")

    @property
    def data_mode(self) -> DataMode:
        return self._mode

    @abstractmethod
    async def fetch_raw(self, request: SourceRequest) -> RawBatch: ...

    @abstractmethod
    def normalize(self, raw: RawBatch) -> list[LeadContribution]: ...

    def target_term_absence(self, term: str) -> SourceAbsence | None:
        """``None`` if this source can express ``term``, else a Not Applicable record.

        A source with no vocabulary for a term was never asked, so it cannot have
        found "no match" (2.8).
        """
        if not isinstance(term, str) or not term.strip():
            raise ValueError("target term must be non-blank")
        if not _is_empty_vocabulary(self.target_vocabulary.get(term)):
            return None
        return SourceAbsence(
            canonical_path=f"{TARGET_TERM_PATH_PREFIX}{term}",
            source_name=self.name,
            kind=AbsenceKind.NOT_APPLICABLE,
        )

    def validate_absence(self, absence: SourceAbsence) -> SourceAbsence:
        """Return ``absence`` if this source's declarations allow it, else raise."""

        def reject(reason: str) -> InvalidAbsenceError:
            return InvalidAbsenceError(
                self.name,
                canonical_path=absence.canonical_path,
                raw_field_path=absence.raw_field_path,
                reason=reason,
            )

        if absence.source_name != self.name:
            raise reject(f"attributed to {absence.source_name!r}, not {self.name!r}")
        surfaces = self.answerable_surfaces.get(absence.canonical_path)
        if absence.kind is AbsenceKind.NOT_APPLICABLE:
            if surfaces is not None:
                raise reject("source declares this path answerable; not applicable")
        elif surfaces is None:
            raise reject(
                "source declares no surface for this path; no negative evidence"
            )
        elif absence.raw_field_path not in surfaces:
            raise reject("raw field path is not a declared surface for this path")
        return absence

    def normalize_checked(self, raw: RawBatch) -> list[LeadContribution]:
        """``normalize`` plus absence validation; the orchestrator calls this."""
        contributions = self.normalize(raw)
        for contribution in contributions:
            for absence in contribution.absences:
                self.validate_absence(absence)
        return contributions


def enrichment_sort_key(
    source: "BaseLeadSource | type[BaseLeadSource]",
) -> tuple[bool, bool, int, str]:
    """Pure ordering key over a source's declarations; smaller runs earlier.

    Free before Credit-bearing, so Suppression and dedupe cost nothing; within a tier,
    Suppression-bearing first so suppressed leads leave the work list before later
    sources are called; then fewest billable events; name breaks ties deterministically.
    """
    return (
        source.cost_class is CostClass.PAID,
        not source.yields_suppression,
        _CHARGE_UNIT_RANK[source.charge_unit],
        source.name,
    )


def enrichment_order[S: BaseLeadSource](sources: Iterable[S]) -> list[S]:
    """Sources in Enrichment order, derived from declarations alone (2.7)."""
    return sorted(sources, key=enrichment_sort_key)


def resolve_credentials(
    source: "BaseLeadSource | type[BaseLeadSource]",
    environ: Mapping[str, str] | None = None,
) -> Mapping[str, str]:
    """Values of the source's ``required_env`` names, from the process environment.

    The environment is the only source: no config file, ``.env`` parse or default is
    consulted here (2.5). An unset or blank variable is missing; every missing name is
    reported together, and the error carries names only, never values (10.5).
    """
    env = os.environ if environ is None else environ
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for name in source.required_env:
        value = env.get(name, "")
        if value.strip():
            resolved[name] = value
        else:
            missing.append(name)
    if missing:
        raise MissingCredentialError(source.name, missing=tuple(missing))
    return MappingProxyType(resolved)
