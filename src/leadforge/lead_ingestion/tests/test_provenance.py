"""Provenance record with Field Confidence and Confidence Origin (task 2.2)."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
)

prov_any: Any = FieldProvenance
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def prov(**kw: Any) -> FieldProvenance:
    base: dict[str, Any] = {
        "canonical_path": "email",
        "source_name": "apollo",
        "data_mode": DataMode.SYNTHETIC,
        "fetched_at": NOW,
        "raw_field_path": "person.email",
        "confidence_origin": ConfidenceOrigin.NONE,
        "untrusted": False,
    }
    base.update(kw)
    return FieldProvenance(**base)


# Verifies: specs/lead-source-adapters/requirements.md#1.2
def test_carries_source_mode_timestamp_and_raw_path() -> None:
    p = prov(data_mode=DataMode.LIVE)
    assert (p.source_name, p.data_mode, p.fetched_at, p.raw_field_path) == (
        "apollo",
        DataMode.LIVE,
        NOW,
        "person.email",
    )


# Verifies: specs/lead-source-adapters/requirements.md#1.2
@pytest.mark.parametrize("field", ["canonical_path", "source_name", "raw_field_path"])
def test_blank_identifying_strings_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        prov(**{field: "  "})


# Verifies: specs/lead-source-adapters/requirements.md#1.2
def test_naive_fetch_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError):
        prov(fetched_at=datetime(2026, 10, 5, 12, 0))


# Verifies: specs/lead-source-adapters/requirements.md#1.2
@pytest.mark.parametrize("field", ["data_mode", "fetched_at", "confidence_origin"])
def test_required_fields_cannot_be_omitted(field: str) -> None:
    kw: dict[str, Any] = {
        "canonical_path": "email",
        "source_name": "apollo",
        "data_mode": "live",
        "fetched_at": NOW,
        "raw_field_path": "person.email",
        "confidence_origin": "none",
    }
    del kw[field]
    with pytest.raises(ValidationError):
        prov_any(**kw)


def test_frozen_and_rejects_undeclared_fields() -> None:
    p = prov()
    with pytest.raises(ValidationError):
        p.superseded = True
    with pytest.raises(ValidationError):
        prov(bogus=1)


def test_origin_vocabulary() -> None:
    assert {o.value for o in ConfidenceOrigin} == {
        "provider_stated",
        "heuristic",
        "none",
    }


# Verifies: specs/lead-source-adapters/requirements.md#1.8
def test_provider_stated_keeps_raw_value_and_scale_name() -> None:
    p = prov(
        confidence_origin=ConfidenceOrigin.PROVIDER_STATED,
        confidence=0.91,
        confidence_raw="91",
        confidence_scale="percent_0_100",
    )
    assert (p.confidence, p.confidence_raw, p.confidence_scale) == (
        0.91,
        "91",
        "percent_0_100",
    )


# Verifies: specs/lead-source-adapters/requirements.md#1.8
@pytest.mark.parametrize("missing", ["confidence_raw", "confidence_scale"])
def test_provider_stated_requires_raw_and_scale(missing: str) -> None:
    kw: dict[str, Any] = {
        "confidence_origin": ConfidenceOrigin.PROVIDER_STATED,
        "confidence": 0.5,
        "confidence_raw": "0.5",
        "confidence_scale": "unit",
    }
    kw[missing] = None
    with pytest.raises(ValidationError):
        prov(**kw)


# Verifies: specs/lead-source-adapters/requirements.md#1.8
def test_none_origin_has_no_number_raw_or_scale() -> None:
    p = prov()
    assert p.confidence is None
    assert p.confidence_raw is None
    assert p.confidence_scale is None


# Verifies: specs/lead-source-adapters/requirements.md#1.8
@pytest.mark.parametrize(
    "extra",
    [
        {"confidence": 0.0},
        {"confidence_raw": "high"},
        {"confidence_scale": "label"},
    ],
)
def test_none_origin_rejects_any_confidence_payload(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        prov(**extra)


# Verifies: specs/lead-source-adapters/requirements.md#1.8
def test_heuristic_needs_a_number_and_claims_no_provider_value() -> None:
    p = prov(confidence_origin=ConfidenceOrigin.HEURISTIC, confidence=0.4)
    assert p.confidence == 0.4
    with pytest.raises(ValidationError):
        prov(confidence_origin=ConfidenceOrigin.HEURISTIC)
    with pytest.raises(ValidationError):
        prov(
            confidence_origin=ConfidenceOrigin.HEURISTIC,
            confidence=0.4,
            confidence_raw="0.4",
        )


@pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan"), float("inf")])
def test_confidence_is_finite_and_within_unit_interval(bad: float) -> None:
    with pytest.raises(ValidationError):
        prov(confidence_origin=ConfidenceOrigin.HEURISTIC, confidence=bad)


# Verifies: specs/lead-source-adapters/requirements.md#1.8
def test_origin_is_not_coerced_from_arbitrary_text() -> None:
    with pytest.raises(ValidationError):
        prov_any(
            canonical_path="email",
            source_name="apollo",
            data_mode="live",
            fetched_at=NOW,
            raw_field_path="p",
            confidence_origin="guessed",
        )


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_superseded_defaults_false_and_losing_record_is_retained_as_copy() -> None:
    p = prov()
    assert p.superseded is False
    loser = p.model_copy(update={"superseded": True})
    assert loser.superseded is True
    assert p.superseded is False
    assert loser.raw_field_path == p.raw_field_path
