"""The adapter contract: what every lead source declares and implements (task 3.1).

Normalization returns ``LeadContribution`` records, never a ``CanonicalLead``; only the
Merge Engine builds that projection. Each adapter declares the canonical paths its API
can answer for (``answerable_surfaces``), so "could answer" versus "never able to
answer" is data, and every ``SourceAbsence`` is checked against it at the boundary.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
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
    "BaseLeadSource",
    "Capability",
    "LeadContribution",
    "RateBucket",
    "RateWindow",
    "RawBatch",
    "SourceRequest",
]


class Capability(StrEnum):
    SEARCH = "search"  # Discovery
    ENRICH = "enrich"  # Enrichment


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


_DECLARATIONS = ("name", "capabilities", "rate_limit", "answerable_surfaces")


class BaseLeadSource(ABC):
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]]  # may be empty
    rate_limit: ClassVar[Mapping[str, RateBucket]]
    # Canonical path the API can answer for -> raw field paths it can be asked on.
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]]

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
        self._mode = mode

    @property
    def data_mode(self) -> DataMode:
        return self._mode

    @abstractmethod
    async def fetch_raw(self, request: SourceRequest) -> RawBatch: ...

    @abstractmethod
    def normalize(self, raw: RawBatch) -> list[LeadContribution]: ...

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
