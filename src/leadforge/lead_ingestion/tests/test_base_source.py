"""Adapter contract, capability flags, and absence validation (task 3.1)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from leadforge.lead_ingestion.base_source import (
    TARGET_TERM_PATH_PREFIX,
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    LeadContribution,
    RateBucket,
    RateWindow,
    RawBatch,
    SourceRequest,
    enrichment_order,
    enrichment_sort_key,
    enrichment_tiers,
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
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()

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
        "target_vocabulary": {},
        "endpoints": {},
        "required_env": (),
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
        "target_vocabulary",
        "endpoints",
        "required_env",
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
        "target_vocabulary": {},
        "endpoints": {},
        "required_env": (),
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


# Verifies: specs/lead-source-adapters/requirements.md#2.7
def test_every_charge_unit_is_rankable() -> None:
    # A unit added to the enum without a rank would KeyError at ordering time.
    for unit in ChargeUnit:
        src = _src(f"u_{unit.value}", CostClass.FREE, unit, False)
        assert enrichment_sort_key(src)[3] >= 0  # the charge-unit rank (ADR-0006)


# --- per-source Target Profile vocabulary (task 3.3) -------------------------


def _targeting(
    vocab: Mapping[str, object],
    surfaces: Mapping[str, frozenset[str]] | None = None,
    name: str = "alpha",
) -> BaseLeadSource:
    cls: type[BaseLeadSource] = type(
        "Targeting",
        (_Stub,),
        {
            "name": name,
            "target_vocabulary": vocab,
            "answerable_surfaces": {} if surfaces is None else surfaces,
        },
    )
    return cls(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_vocabulary_is_per_source_and_opaque() -> None:
    a = _targeting(
        {"python": {"tech_a": 7}}, {"target_profile.python": frozenset({"t"})}
    )
    b = _targeting(
        {"python": ["other-id"]}, {"target_profile.python": frozenset({"t"})}
    )
    assert a.target_vocabulary["python"] == {"tech_a": 7}
    assert b.target_vocabulary["python"] == ["other-id"]


# Verifies: specs/lead-source-adapters/requirements.md#2.8
def test_prefix_constant() -> None:
    assert TARGET_TERM_PATH_PREFIX == "target_profile."


# Verifies: specs/lead-source-adapters/requirements.md#2.8
@pytest.mark.parametrize("empty", [None, "", "  ", [], (), {}, frozenset()])
def test_empty_declaration_is_not_applicable(empty: object) -> None:
    src = _targeting({"python": empty})
    absence = src.target_term_absence("python")
    assert absence is not None
    assert absence.kind is AbsenceKind.NOT_APPLICABLE
    assert absence.canonical_path == "target_profile.python"
    assert absence.source_name == "alpha"
    assert absence.raw_field_path is None
    assert src.validate_absence(absence) is absence


# Verifies: specs/lead-source-adapters/requirements.md#2.8
def test_undeclared_term_is_not_applicable() -> None:
    absence = _targeting({}).target_term_absence("rust")
    assert absence is not None
    assert absence.kind is AbsenceKind.NOT_APPLICABLE


# Verifies: specs/lead-source-adapters/requirements.md#2.8
def test_expressible_term_has_no_absence() -> None:
    src = _targeting(
        {"python": {"tech_a": 1}}, {"target_profile.python": frozenset({"t"})}
    )
    assert src.target_term_absence("python") is None


# Verifies: specs/lead-source-adapters/requirements.md#2.8
@pytest.mark.parametrize("term", ["", "   "])
def test_blank_term_is_rejected(term: str) -> None:
    with pytest.raises(ValueError, match="non-blank"):
        _targeting({}).target_term_absence(term)


# Verifies: specs/lead-source-adapters/requirements.md#2.8
def test_source_without_targeting_surface_never_yields_negative_evidence() -> None:
    src = _targeting({})
    negative = SourceAbsence(
        canonical_path="target_profile.python",
        source_name="alpha",
        kind=AbsenceKind.NEGATIVE_EVIDENCE,
        raw_field_path="anything",
    )
    with pytest.raises(InvalidAbsenceError, match=r"target_profile\.python"):
        src.validate_absence(negative)


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_malformed_vocabulary_is_rejected_at_construction() -> None:
    class NotAMapping(_Stub):
        target_vocabulary: ClassVar[Mapping[str, object]] = ["python"]  # type: ignore[assignment]

    class BadKey(_Stub):
        target_vocabulary: ClassVar[Mapping[str, object]] = {1: "x"}  # type: ignore[dict-item]

    class BlankKey(_Stub):
        target_vocabulary: ClassVar[Mapping[str, object]] = {" ": "x"}

    for cls in (NotAMapping, BadKey, BlankKey):
        with pytest.raises(TypeError, match=cls.__name__):
            cls(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#2.8
def test_answerable_target_term_without_vocabulary_is_rejected() -> None:
    with pytest.raises(TypeError, match=r"Targeting.*python"):
        _targeting({"python": None}, {"target_profile.python": frozenset({"t"})})


# Verifies: specs/lead-source-adapters/requirements.md#2.8
def test_vocabulary_without_answerable_surface_is_rejected() -> None:
    with pytest.raises(TypeError, match=r"Targeting.*python"):
        _targeting({"python": {"tech_a": 1}})


# Verifies: specs/lead-source-adapters/requirements.md#2.8
@pytest.mark.parametrize("term", [None, 1, b"python"])
def test_non_str_term_is_rejected(term: object) -> None:
    with pytest.raises(ValueError, match="non-blank"):
        _targeting({}).target_term_absence(term)  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#2.8
def test_negative_evidence_for_a_target_term_is_rejected_by_normalize_checked() -> None:
    class Rogue(_Stub):
        name: ClassVar[str] = "alpha"
        target_vocabulary: ClassVar[Mapping[str, object]] = {"java": {"tech_a": 1}}
        answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {
            "target_profile.java": frozenset({"t"})
        }

        def normalize(self, raw: RawBatch) -> list[LeadContribution]:
            return [
                LeadContribution(
                    source_name="alpha",
                    absences=(
                        SourceAbsence(
                            canonical_path="target_profile.python",
                            source_name="alpha",
                            kind=AbsenceKind.NEGATIVE_EVIDENCE,
                            raw_field_path="t",
                        ),
                    ),
                )
            ]

    src = Rogue(DataMode.SYNTHETIC)
    assert src.target_term_absence("java") is None
    na = src.target_term_absence("python")
    assert na is not None
    assert src.validate_absence(na) is na
    with pytest.raises(InvalidAbsenceError, match=r"target_profile\.python"):
        src.normalize_checked(RawBatch(source_name="alpha", payload={}))


# Verifies: specs/lead-source-adapters/requirements.md#2.8
def test_not_applicable_for_an_expressible_term_is_rejected() -> None:
    src = _targeting(
        {"python": {"tech_a": 1}}, {"target_profile.python": frozenset({"t"})}
    )
    na = SourceAbsence(
        canonical_path="target_profile.python",
        source_name="alpha",
        kind=AbsenceKind.NOT_APPLICABLE,
    )
    with pytest.raises(InvalidAbsenceError):
        src.validate_absence(na)


# ------------------------------------------- declarations are immutable (3.3 audit)

_MAPPING_DECLARATIONS = (
    "rate_limit",
    "answerable_surfaces",
    "target_vocabulary",
    "endpoints",
)


# Verifies: specs/lead-source-adapters/requirements.md#2.1
@pytest.mark.parametrize("declaration", _MAPPING_DECLARATIONS)
def test_mapping_declarations_cannot_be_mutated_after_class_creation(
    declaration: str,
) -> None:
    declared: Any = getattr(_Stub, declaration)
    with pytest.raises(TypeError):
        declared["injected"] = object()
    with pytest.raises(TypeError):
        del declared[next(iter(declared), "absent")]


# Verifies: specs/lead-source-adapters/requirements.md#2.1
@pytest.mark.parametrize("declaration", _MAPPING_DECLARATIONS)
def test_instances_and_subclasses_see_frozen_declarations(declaration: str) -> None:
    class Child(_Stub):
        name: ClassVar[str] = "child"

    declared: Any = getattr(Child(DataMode.SYNTHETIC), declaration)
    with pytest.raises(TypeError):
        declared["injected"] = object()


# Verifies: specs/lead-source-adapters/requirements.md#2.1
def test_mutating_the_original_dict_after_class_creation_changes_nothing() -> None:
    surfaces = {"email": frozenset({"person.email"})}

    class Mutable(_Stub):
        name: ClassVar[str] = "mutable"
        answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = surfaces

    surfaces["phone"] = frozenset({"person.phone"})
    assert set(Mutable.answerable_surfaces) == {"email"}
    assert set(Mutable(DataMode.SYNTHETIC).answerable_surfaces) == {"email"}


# Verifies: specs/lead-source-adapters/requirements.md#2.1
def test_a_subclass_redeclaring_a_mapping_does_not_mutate_its_parent() -> None:
    class Wider(_Stub):
        name: ClassVar[str] = "wider"
        answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {
            "email": frozenset({"person.email"}),
            "phone": frozenset({"person.phone"}),
        }

    assert set(_Stub.answerable_surfaces) == {"email"}
    assert set(Wider.answerable_surfaces) == {"email", "phone"}


# Verifies: specs/lead-source-adapters/requirements.md#2.1
def test_a_non_mapping_declaration_is_still_rejected_at_construction() -> None:
    class Broken(_Stub):
        name: ClassVar[str] = "broken"
        rate_limit: ClassVar[Any] = None

    with pytest.raises(TypeError, match="rate_limit"):
        Broken(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#2.1
def test_a_proxy_over_a_mutable_dict_and_an_inherited_mixin_dict_are_frozen() -> None:
    backing = {"email": frozenset({"person.email"})}

    class Mixin:
        rate_limit: ClassVar[Any] = {}

    class Viaproxy(Mixin, _Stub):
        name: ClassVar[str] = "viaproxy"
        answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = MappingProxyType(
            backing
        )

    backing["phone"] = frozenset({"person.phone"})
    assert set(Viaproxy.answerable_surfaces) == {"email"}
    with pytest.raises(TypeError):
        Viaproxy.rate_limit["x"] = object()


# Verifies: specs/lead-source-adapters/requirements.md#6.10
def test_enrichment_tiers_group_equal_declarations_in_derived_order() -> None:
    srcs = [
        _src("a", CostClass.PAID, ChargeUnit.PER_LEAD, False),
        _src("b", CostClass.PAID, ChargeUnit.PER_LEAD, False),
        _src("c", CostClass.FREE, ChargeUnit.PER_CALL, True),
        _src("d", CostClass.PAID, ChargeUnit.PER_COMPANY, True),
    ]
    tiers = [[s.name for s in tier] for tier in enrichment_tiers(srcs)]
    assert tiers == [["c"], ["d"], ["a", "b"]]
    assert enrichment_tiers([]) == []
