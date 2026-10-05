"""Signals and Signal Strength on both entities (task 2.5, requirement 24.4).

Scope of the "no merge outcome" tests: the Merge Engine (tasks 16.x) does not exist
yet, so these prove only the structural half - Signal Strength is reachable from no
field a merge would resolve, and no ordering or hashing hook on any entity reads it.
Task 16.3 must extend this with a real merge run over leads differing only in strength.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from leadforge.lead_ingestion import models
from leadforge.lead_ingestion.models import (
    CanonicalLead,
    CompanySignal,
    Employment,
    FieldProvenance,
    IntentSignal,
    Signal,
    TechSignal,
)

SIGNAL_COLLECTIONS = ("tech_signals", "intent_signals")
signal_any: Any = Signal


def lead(**kw: Any) -> CanonicalLead:
    base: dict[str, Any] = {
        "full_name": "Ada Lovelace",
        "linkedin_url": "https://x.com/a",
    }
    base.update(kw)
    return CanonicalLead(**base)


def company(**kw: Any) -> CompanySignal:
    base: dict[str, Any] = {"company_id": "c1", "name": "Acme"}
    base.update(kw)
    return CompanySignal(**base)


def with_strength(strength: float) -> dict[str, Any]:
    return {
        "tech_signals": (TechSignal(label="cassandra", strength=strength),),
        "intent_signals": (IntentSignal(label="hiring", strength=strength),),
    }


# Verifies: specs/lead-source-adapters/requirements.md#24.4
@pytest.mark.parametrize("bad", [True, False, "0.5", None, [0.5], (0.5, 0.6)])
def test_signal_strength_is_exactly_one_real_number(bad: object) -> None:
    for kind in (TechSignal, IntentSignal):
        with pytest.raises(ValidationError, match="strength"):
            kind(label="x", strength=bad)  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_signal_has_a_single_strength_field_and_no_second_one() -> None:
    assert [f for f in Signal.model_fields if "strength" in f] == ["strength"]
    with pytest.raises(ValidationError):
        signal_any(label="x", strength=0.5, strength_raw=3)


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_signals_default_to_none_on_both_entities() -> None:
    for entity in (lead(), company()):
        assert entity.tech_signals == ()
        assert entity.intent_signals == ()


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_json_round_trip_keeps_strength_on_both_entities() -> None:
    ld = lead(**with_strength(0.25))
    cs = company(**with_strength(0.75))
    assert CanonicalLead.model_validate_json(ld.model_dump_json()) == ld
    assert CompanySignal.model_validate_json(cs.model_dump_json()) == cs


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_changing_strength_changes_only_signal_paths_of_either_entity() -> None:
    for build in (lead, company):
        low, high = build(**with_strength(0.1)), build(**with_strength(0.9))
        assert low != high  # strength is recorded, not discarded
        a, b = low.model_dump(), high.model_dump()
        resolvable = [k for k in a if k not in SIGNAL_COLLECTIONS]
        assert {k: a[k] for k in resolvable} == {k: b[k] for k in resolvable}


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_employment_and_company_identity_ignore_the_lead_signal_strength() -> None:
    emp = Employment(company=company(), title="CTO", is_current=True)
    low = lead(employments=(emp,), **with_strength(0.1))
    high = lead(employments=(emp,), **with_strength(0.9))
    assert low.employments == high.employments
    assert low.current_employments == high.current_employments
    assert low.has_multiple_current_employments == high.has_multiple_current_employments


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_no_conflict_resolution_surface_carries_signal_strength() -> None:
    # Provenance is what a merge orders by (trust rank, then Field Confidence).
    assert not [f for f in FieldProvenance.model_fields if "strength" in f]
    for entity in (CanonicalLead, CompanySignal):
        strength_fields = [f for f in entity.model_fields if "strength" in f]
        assert strength_fields == []  # reachable only through the Signal tuples


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_no_entity_defines_an_ordering_so_strength_cannot_rank_leads() -> None:
    entities = (CanonicalLead, CompanySignal, Employment, Signal, FieldProvenance)
    for cls in entities:
        for hook in ("__lt__", "__le__", "__gt__", "__ge__"):
            assert getattr(cls, hook, None) is getattr(object, hook, None), (cls, hook)


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_only_signals_expose_a_strength_among_public_model_types() -> None:
    owners = [
        name
        for name in models.__all__
        if isinstance(getattr(models, name), type)
        and "strength" in getattr(getattr(models, name), "model_fields", {})
    ]
    assert sorted(owners) == ["IntentSignal", "Signal", "TechSignal"]
