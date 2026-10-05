"""Adapter contract, capability flags, and absence validation (task 3.1)."""

from collections.abc import Mapping
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    LeadContribution,
    RateBucket,
    RateWindow,
    RawBatch,
    SourceRequest,
    enrichment_order,
    enrichment_sort_key,
)
from leadforge.lead_ingestion.errors import InvalidAbsenceError, SourceError
from leadforge.lead_ingestion.models import AbsenceKind, DataMode, SourceAbsence

BUCKET = RateBucket(
    name="default",
    windows=(RateWindow(requests=60, per_seconds=60.0),),
    documented=True,
    doc_url="https://example.com/rate-limits",
)


class _Stub(BaseLeadSource):
    """Complete concrete adapter; subclasses override declarations only."""

    name: ClassVar[str] = "stub"
    capabilities: ClassVar[frozenset[Capability]] = frozenset()
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {"default": BUCKET}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {
        "email": frozenset({"person.email"})
    }
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        return RawBatch(source_name=self.name, payload={"kind": request.kind})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return [LeadContribution(source_name=self.name)]


class SearchOnly(_Stub):
    name: ClassVar[str] = "search_only"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})


class EnrichOnly(_Stub):
    name: ClassVar[str] = "enrich_only"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.ENRICH})


class Both(_Stub):
    name: ClassVar[str] = "both"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.SEARCH, Capability.ENRICH}
    )


class Neither(_Stub):
    name: ClassVar[str] = "neither"


class NoEmailSurface(_Stub):
    """Technographics-only API: it has no surface for any person field."""

    name: ClassVar[str] = "tech_only"
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {
        "company.tech": frozenset({"technologies"})
    }


CONCRETE: list[type[BaseLeadSource]] = [SearchOnly, EnrichOnly, Both, Neither]


def negative(**kw: Any) -> SourceAbsence:
    base: dict[str, Any] = {
        "canonical_path": "email",
        "source_name": "stub",
        "kind": AbsenceKind.NEGATIVE_EVIDENCE,
        "raw_field_path": "person.email",
    }
    base.update(kw)
    return SourceAbsence(**base)


def not_applicable(**kw: Any) -> SourceAbsence:
    base: dict[str, Any] = {
        "canonical_path": "linkedin_url",
        "source_name": "stub",
        "kind": AbsenceKind.NOT_APPLICABLE,
    }
    base.update(kw)
    return SourceAbsence(**base)


# --- surface of the contract ------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#2.1
@pytest.mark.parametrize("cls", CONCRETE, ids=lambda c: c.__name__)
def test_adapter_exposes_the_complete_declared_surface(
    cls: type[BaseLeadSource],
) -> None:
    src = cls(DataMode.SYNTHETIC)
    assert src.name == cls.name
    assert isinstance(src.capabilities, frozenset)
    assert src.rate_limit["default"].windows[0].requests == 60
    assert src.data_mode is DataMode.SYNTHETIC
    assert cls(DataMode.LIVE).data_mode is DataMode.LIVE


# Verifies: specs/lead-source-adapters/requirements.md#2.1
async def test_fetch_raw_returns_a_raw_batch() -> None:
    batch = await Both(DataMode.SYNTHETIC).fetch_raw(SourceRequest(kind="search"))
    assert isinstance(batch, RawBatch)
    assert batch.source_name == "both"


# Verifies: specs/lead-source-adapters/requirements.md#2.1
def test_normalize_returns_contributions_not_a_canonical_lead() -> None:
    src = Both(DataMode.SYNTHETIC)
    out = src.normalize(RawBatch(source_name="both", payload={}))
    assert out
    assert all(isinstance(c, LeadContribution) for c in out)
    assert not hasattr(out[0], "email")


# Verifies: specs/lead-source-adapters/requirements.md#2.1
@pytest.mark.parametrize("missing", ["fetch_raw", "normalize"])
def test_incomplete_subclass_fails_at_construction(missing: str) -> None:
    attrs: dict[str, Any] = {
        "fetch_raw": _Stub.fetch_raw,
        "normalize": _Stub.normalize,
        "name": "incomplete",
        "capabilities": frozenset[Capability](),
        "rate_limit": {"default": BUCKET},
        "answerable_surfaces": {"email": frozenset({"person.email"})},
        "cost_class": CostClass.FREE,
        "charge_unit": ChargeUnit.PER_CALL,
        "yields_suppression": False,
    }
    del attrs[missing]
    incomplete = type("Incomplete", (BaseLeadSource,), attrs)
    with pytest.raises(TypeError, match=missing):
        incomplete(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#2.1
@pytest.mark.parametrize(
    "missing",
    [
        "name",
        "capabilities",
        "rate_limit",
        "answerable_surfaces",
        "cost_class",
        "charge_unit",
        "yields_suppression",
    ],
)
def test_subclass_without_a_declaration_fails_at_construction(missing: str) -> None:
    attrs: dict[str, Any] = {
        "fetch_raw": _Stub.fetch_raw,
        "normalize": _Stub.normalize,
        "name": "undeclared",
        "capabilities": frozenset[Capability](),
        "rate_limit": {"default": BUCKET},
        "answerable_surfaces": {"email": frozenset({"person.email"})},
        "cost_class": CostClass.FREE,
        "charge_unit": ChargeUnit.PER_CALL,
        "yields_suppression": False,
    }
    del attrs[missing]
    cls = type("Undeclared", (BaseLeadSource,), attrs)
    with pytest.raises(TypeError, match=f"Undeclared.*{missing}"):
        cls(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#2.1
def test_blank_name_and_non_capability_flags_are_rejected() -> None:
    class Blank(_Stub):
        name: ClassVar[str] = "  "

    class BadFlags(_Stub):
        capabilities: ClassVar[frozenset[Capability]] = frozenset({"search"})  # type: ignore[arg-type]

    class NotASet(_Stub):
        capabilities: ClassVar[frozenset[Capability]] = [Capability.SEARCH]  # type: ignore[assignment]

    for cls in (Blank, BadFlags, NotASet):
        with pytest.raises(TypeError, match=cls.__name__):
            cls(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#2.1
def test_surface_declaration_with_an_empty_surface_set_is_rejected() -> None:
    class EmptySurface(_Stub):
        answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {
            "email": frozenset()
        }

    with pytest.raises(TypeError, match=r"EmptySurface.*email"):
        EmptySurface(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_malformed_declarations_are_rejected_at_construction() -> None:
    class StrSurface(_Stub):  # a bare str would turn `in` into a substring test
        answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {
            "email": "person.email"  # type: ignore[dict-item]
        }

    class NoneName(_Stub):
        name: ClassVar[str] = None  # type: ignore[assignment]

    class BadRate(_Stub):
        rate_limit: ClassVar[Mapping[str, RateBucket]] = None  # type: ignore[assignment]

    for cls in (StrSurface, NoneName, BadRate):
        with pytest.raises(TypeError, match=cls.__name__):
            cls(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#2.1
def test_contract_models_are_frozen_and_closed() -> None:
    with pytest.raises(ValidationError):
        RawBatch(source_name=" ", payload=None)
    with pytest.raises(ValidationError):
        SourceRequest(kind="search", extra=1)  # type: ignore[call-arg]
    contribution = LeadContribution(source_name="stub")
    assert contribution.absences == ()
    with pytest.raises(ValidationError):
        contribution.source_name = "other"
    window = RateWindow(requests=1, per_seconds=1.0)
    with pytest.raises(AttributeError):
        window.requests = 2  # type: ignore[misc]


# --- capability flags (2.2, 2.3) --------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#2.2
# Verifies: specs/lead-source-adapters/requirements.md#2.3
@pytest.mark.parametrize(
    ("cls", "search", "enrich"),
    [
        (SearchOnly, True, False),
        (EnrichOnly, False, True),
        (Both, True, True),
        (Neither, False, False),
    ],
    ids=lambda v: getattr(v, "__name__", str(v)),
)
def test_search_and_enrich_flags_are_independent(
    cls: type[BaseLeadSource], search: bool, enrich: bool
) -> None:
    src = cls(DataMode.SYNTHETIC)
    assert (Capability.SEARCH in src.capabilities) is search
    assert (Capability.ENRICH in src.capabilities) is enrich


# Verifies: specs/lead-source-adapters/requirements.md#2.2
def test_capability_values_are_search_and_enrich() -> None:
    assert {c.value for c in Capability} == {"search", "enrich"}


# --- absence validation at the adapter boundary (1.9) -----------------------


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_negative_evidence_accepted_for_declared_path_and_surface() -> None:
    src = Both(DataMode.SYNTHETIC)
    absence = negative(source_name="both")
    assert src.validate_absence(absence) is absence


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_not_applicable_accepted_for_an_undeclared_path() -> None:
    src = Both(DataMode.SYNTHETIC)
    absence = not_applicable(source_name="both")
    assert src.validate_absence(absence) is absence


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_adapter_with_no_surface_cannot_emit_negative_evidence() -> None:
    src = NoEmailSurface(DataMode.SYNTHETIC)
    with pytest.raises(InvalidAbsenceError) as exc:
        src.validate_absence(negative(source_name="tech_only"))
    assert exc.value.canonical_path == "email"
    assert exc.value.raw_field_path == "person.email"
    assert "tech_only" in str(exc.value)
    # ...but it may honestly say the path is not applicable to it.
    na = not_applicable(source_name="tech_only", canonical_path="email")
    assert src.validate_absence(na) is na


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_negative_evidence_on_an_undeclared_surface_is_rejected() -> None:
    src = Both(DataMode.SYNTHETIC)
    with pytest.raises(InvalidAbsenceError, match=r"person\.work_email"):
        src.validate_absence(
            negative(source_name="both", raw_field_path="person.work_email")
        )


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_not_applicable_for_a_declared_path_is_rejected() -> None:
    src = Both(DataMode.SYNTHETIC)
    with pytest.raises(InvalidAbsenceError, match="email"):
        src.validate_absence(not_applicable(source_name="both", canonical_path="email"))


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_absence_attributed_to_another_source_is_rejected() -> None:
    src = Both(DataMode.SYNTHETIC)
    with pytest.raises(InvalidAbsenceError, match="stub"):
        src.validate_absence(negative(source_name="stub"))


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_invalid_absence_is_a_source_error() -> None:
    src = NoEmailSurface(DataMode.SYNTHETIC)
    with pytest.raises(SourceError):
        src.validate_absence(negative(source_name="tech_only"))


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_normalize_checked_validates_every_absence_of_every_contribution() -> None:
    class Emitting(_Stub):
        name: ClassVar[str] = "emitting"
        emit: ClassVar[list[SourceAbsence]] = []

        def normalize(self, raw: RawBatch) -> list[LeadContribution]:
            return [
                LeadContribution(source_name=self.name, absences=(a,))
                for a in self.emit
            ]

    raw = RawBatch(source_name="emitting", payload={})
    src = Emitting(DataMode.SYNTHETIC)

    Emitting.emit = [
        negative(source_name="emitting"),
        not_applicable(source_name="emitting"),
    ]
    assert len(src.normalize_checked(raw)) == 2

    Emitting.emit = [
        negative(source_name="emitting"),
        negative(source_name="emitting", canonical_path="full_name"),
    ]
    with pytest.raises(InvalidAbsenceError, match="full_name"):
        src.normalize_checked(raw)


# --- cost declarations and derived enrichment order (task 3.2) ---------------


def _src(
    name: str, cost: CostClass, unit: ChargeUnit, suppress: bool
) -> BaseLeadSource:
    cls: type[BaseLeadSource] = type(
        name,
        (_Stub,),
        {
            "name": name,
            "cost_class": cost,
            "charge_unit": unit,
            "yields_suppression": suppress,
        },
    )
    return cls(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#2.7
def test_cost_enums_have_the_spec_values() -> None:
    assert {c.value for c in CostClass} == {"free", "paid"}
    assert {u.value for u in ChargeUnit} == {"per_lead", "per_company", "per_call"}


# Verifies: specs/lead-source-adapters/requirements.md#2.7
def test_cost_declarations_are_exposed() -> None:
    src = _src("x", CostClass.PAID, ChargeUnit.PER_LEAD, True)
    assert src.cost_class is CostClass.PAID
    assert src.charge_unit is ChargeUnit.PER_LEAD
    assert src.yields_suppression is True


# Verifies: specs/lead-source-adapters/requirements.md#2.7
def test_malformed_cost_declarations_are_rejected_at_construction() -> None:
    class BadCost(_Stub):
        cost_class: ClassVar[CostClass] = "free"  # type: ignore[assignment]

    class BadUnit(_Stub):
        charge_unit: ClassVar[ChargeUnit] = "per_call"  # type: ignore[assignment]

    class BadSuppress(_Stub):
        yields_suppression: ClassVar[bool] = "yes"  # type: ignore[assignment]

    for cls in (BadCost, BadUnit, BadSuppress):
        with pytest.raises(TypeError, match=cls.__name__):
            cls(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#2.7
def test_free_suppression_sources_precede_every_credit_bearing_source() -> None:
    paid_supp = _src("paid_supp", CostClass.PAID, ChargeUnit.PER_COMPANY, True)
    paid = _src("paid", CostClass.PAID, ChargeUnit.PER_CALL, False)
    free_plain = _src("free_plain", CostClass.FREE, ChargeUnit.PER_CALL, False)
    free_supp = _src("free_supp", CostClass.FREE, ChargeUnit.PER_CALL, True)
    ordered = enrichment_order([paid, paid_supp, free_plain, free_supp])
    assert ordered[0] is free_supp
    assert [s.name for s in ordered[:2]] == ["free_supp", "free_plain"]
    assert {s.name for s in ordered[2:]} == {"paid_supp", "paid"}
    assert ordered[2] is paid_supp  # suppression first within the credit tier


# Verifies: specs/lead-source-adapters/requirements.md#2.7
def test_order_is_independent_of_input_order_and_deterministic() -> None:
    srcs = [
        _src("a", CostClass.PAID, ChargeUnit.PER_LEAD, False),
        _src("b", CostClass.PAID, ChargeUnit.PER_COMPANY, False),
        _src("c", CostClass.PAID, ChargeUnit.PER_CALL, False),
        _src("d", CostClass.PAID, ChargeUnit.PER_CALL, False),
        _src("e", CostClass.FREE, ChargeUnit.PER_LEAD, True),
    ]
    expected = [s.name for s in enrichment_order(srcs)]
    assert expected == ["e", "b", "c", "d", "a"]
    assert [s.name for s in enrichment_order(reversed(srcs))] == expected


# Verifies: specs/lead-source-adapters/requirements.md#2.7
def test_sort_key_is_a_pure_function_of_declarations_on_class_or_instance() -> None:
    src = _src("k", CostClass.FREE, ChargeUnit.PER_CALL, True)
    assert enrichment_sort_key(src) == enrichment_sort_key(type(src))


# Verifies: specs/lead-source-adapters/requirements.md#2.7
def test_a_new_source_is_placed_without_editing_the_ordering_code() -> None:
    new = _src("brand_new", CostClass.FREE, ChargeUnit.PER_LEAD, True)
    old = [_src("old", CostClass.PAID, ChargeUnit.PER_CALL, True)]
    assert enrichment_order([*old, new])[0] is new
