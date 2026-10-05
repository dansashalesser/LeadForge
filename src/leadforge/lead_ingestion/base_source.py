"""The adapter contract: what every lead source declares and implements (task 3.1).

Normalization returns ``LeadContribution`` records, never a ``CanonicalLead``; only the
Merge Engine builds that projection. Each adapter declares the canonical paths its API
can answer for (``answerable_surfaces``), so "could answer" versus "never able to
answer" is data, and every ``SourceAbsence`` is checked against it at the boundary.
"""

from abc import ABC, abstractmethod
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from leadforge.lead_ingestion.errors import InvalidAbsenceError
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    DataMode,
    NonBlank,
    SourceAbsence,
    _Entity,
)

__all__ = [
    "TARGET_TERM_PATH_PREFIX",
    "BaseLeadSource",
    "Capability",
    "ChargeUnit",
    "CostClass",
    "LeadContribution",
    "RateBucket",
    "RateWindow",
    "RawBatch",
    "SourceRequest",
    "enrichment_order",
    "enrichment_sort_key",
]


class Capability(StrEnum):
    SEARCH = "search"  # Discovery
    ENRICH = "enrich"  # Enrichment


class CostClass(StrEnum):
    FREE = "free"
    PAID = "paid"  # Credit-bearing


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


TARGET_TERM_PATH_PREFIX = "target_profile."


def _is_empty_vocabulary(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Collection):
        return len(value) == 0
    return False


_DECLARATIONS = (
    "name",
    "capabilities",
    "rate_limit",
    "answerable_surfaces",
    "cost_class",
    "charge_unit",
    "yields_suppression",
    "target_vocabulary",
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
    # Canonical Target Profile term -> this provider's opaque vocabulary for it.
    # An empty value (or an absent term) is Not Applicable, never "no match" (2.8).
    target_vocabulary: ClassVar[Mapping[str, object]]

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
        self._mode = mode

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
        if not term.strip():
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
