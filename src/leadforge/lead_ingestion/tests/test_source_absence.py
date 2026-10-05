"""Negative Evidence vs Not Applicable vs plain absence (task 2.4)."""

from typing import Any

import pytest
from pydantic import ValidationError

from leadforge.lead_ingestion.models import AbsenceKind, CanonicalLead, SourceAbsence

absence_any: Any = SourceAbsence


def negative(**kw: Any) -> SourceAbsence:
    base: dict[str, Any] = {
        "canonical_path": "email",
        "source_name": "apollo",
        "kind": AbsenceKind.NEGATIVE_EVIDENCE,
        "raw_field_path": "person.email",
    }
    base.update(kw)
    return SourceAbsence(**base)


def not_applicable(**kw: Any) -> SourceAbsence:
    base: dict[str, Any] = {
        "canonical_path": "email",
        "source_name": "tech_only",
        "kind": AbsenceKind.NOT_APPLICABLE,
    }
    base.update(kw)
    return SourceAbsence(**base)


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_negative_evidence_names_the_surface_that_was_asked() -> None:
    n = negative()
    assert n.kind is AbsenceKind.NEGATIVE_EVIDENCE
    assert n.raw_field_path == "person.email"


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_not_applicable_has_no_surface() -> None:
    n = not_applicable()
    assert n.kind is AbsenceKind.NOT_APPLICABLE
    assert n.raw_field_path is None


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_the_two_kinds_are_distinguishable() -> None:
    assert negative().kind != not_applicable().kind
    assert negative() != not_applicable(source_name="apollo")


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_source_with_no_surface_cannot_contribute_negative_evidence() -> None:
    with pytest.raises(ValidationError):
        negative(raw_field_path=None)


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_not_applicable_cannot_claim_a_surface() -> None:
    with pytest.raises(ValidationError):
        not_applicable(raw_field_path="person.email")


# Verifies: specs/lead-source-adapters/requirements.md#1.9
@pytest.mark.parametrize("field", ["canonical_path", "source_name", "kind"])
def test_required_fields_cannot_be_omitted(field: str) -> None:
    kw: dict[str, Any] = {
        "canonical_path": "email",
        "source_name": "apollo",
        "kind": AbsenceKind.NOT_APPLICABLE,
    }
    del kw[field]
    with pytest.raises(ValidationError):
        absence_any(**kw)


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_blank_identifying_strings_and_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        negative(source_name=" ")
    with pytest.raises(ValidationError):
        negative(raw_field_path=" ")
    with pytest.raises(ValidationError):
        negative(extra=1)


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_plain_absence_stays_none_and_is_neither_kind() -> None:
    lead = CanonicalLead(full_name="Ada")
    assert lead.email is None
    assert not hasattr(lead, "absences")
    assert {k.value for k in AbsenceKind} == {"negative_evidence", "not_applicable"}


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_records_are_immutable() -> None:
    n = negative()
    with pytest.raises(ValidationError):
        n.kind = AbsenceKind.NOT_APPLICABLE
