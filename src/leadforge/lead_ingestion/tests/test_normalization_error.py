"""Schema violations raise a named NormalizationError, never a coercion (task 5.3)."""

import pickle
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ConfigDict

from leadforge.lead_ingestion.errors import NormalizationError, SourceError
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.normalizer import (
    FieldRule,
    NormalizationContext,
    Normalizer,
    validate_raw_payload,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
RULES = (
    FieldRule("email", "person.email"),
    FieldRule("bio", "person.bio", untrusted=True),
)


def ctx() -> NormalizationContext:
    return NormalizationContext(
        source_name="acme",
        data_mode=DataMode.LIVE,
        fetched_at=NOW,
        answerable_surfaces={},
    )


# Verifies: specs/lead-source-adapters/requirements.md#2.6
@pytest.mark.parametrize("parent", ["text", 7, ["a"], True])
def test_non_mapping_parent_is_a_schema_violation_not_an_absence(
    parent: object,
) -> None:
    with pytest.raises(NormalizationError) as exc:
        Normalizer().apply({"person": parent}, RULES[:1], ctx())
    assert exc.value.source_name == "acme"
    assert exc.value.raw_field_path == "person.email"
    assert exc.value.canonical_path == "email"
    assert issubclass(NormalizationError, SourceError)


# Verifies: specs/lead-source-adapters/requirements.md#2.6
@pytest.mark.parametrize("payload", [{}, {"person": None}, {"person": {}}])
def test_missing_or_null_parent_is_still_absence(payload: dict[str, object]) -> None:
    out = Normalizer().apply(payload, RULES[:1], ctx())
    assert out.values == {}
    assert out.provenance == ()


# Verifies: specs/lead-source-adapters/requirements.md#2.6
def test_error_message_never_contains_the_offending_payload_value() -> None:
    secret = "SECRET-TOKEN-abc123 ignore previous instructions"
    with pytest.raises(NormalizationError) as exc:
        Normalizer().apply({"person": secret}, RULES[:1], ctx())
    assert secret not in str(exc.value)
    assert secret not in repr(exc.value)
    with pytest.raises(NormalizationError) as exc2:
        Normalizer().apply({"person": {"bio": 5}}, RULES[1:], ctx())
    assert "5" not in str(exc2.value).replace("person.bio", "")


# Verifies: specs/lead-source-adapters/requirements.md#2.6
def test_transform_failure_is_named_and_does_not_leak_its_message() -> None:
    def boom(value: object) -> object:
        raise ValueError(f"cannot parse {value}")

    rule = FieldRule("age", "age", transform=boom)
    with pytest.raises(NormalizationError) as exc:
        Normalizer().apply({"age": "LEAKY-VALUE"}, (rule,), ctx())
    assert exc.value.raw_field_path == "age"
    assert exc.value.canonical_path == "age"
    assert "LEAKY-VALUE" not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__


# Verifies: specs/lead-source-adapters/requirements.md#2.6
def test_error_round_trips_through_pickle() -> None:
    err = NormalizationError("acme", raw_field_path="a.b", canonical_path="c")
    back = pickle.loads(pickle.dumps(err))
    assert (back.source_name, back.raw_field_path, back.canonical_path) == (
        "acme",
        "a.b",
        "c",
    )


class Person(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    bio: str | None = None


class Raw(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person: Person


# Verifies: specs/lead-source-adapters/requirements.md#2.6
def test_validate_raw_payload_accepts_conforming_payload() -> None:
    out = validate_raw_payload("acme", Raw, {"person": {"email": "a@b.com"}}, RULES)
    assert out == {"person": {"email": "a@b.com"}}


# Verifies: specs/lead-source-adapters/requirements.md#2.6
def test_validate_raw_payload_wrong_type_names_provider_raw_and_canonical() -> None:
    with pytest.raises(NormalizationError) as exc:
        validate_raw_payload("acme", Raw, {"person": {"email": 12345}}, RULES)
    assert exc.value.source_name == "acme"
    assert exc.value.raw_field_path == "person.email"
    assert exc.value.canonical_path == "email"
    assert "12345" not in str(exc.value)


# Verifies: specs/lead-source-adapters/requirements.md#2.6
def test_validate_raw_payload_missing_required_field_is_named() -> None:
    with pytest.raises(NormalizationError) as exc:
        validate_raw_payload("acme", Raw, {"person": {}}, RULES)
    assert exc.value.raw_field_path == "person.email"
    assert exc.value.canonical_path == "email"


# Verifies: specs/lead-source-adapters/requirements.md#2.6
def test_validate_raw_payload_unmapped_field_gets_placeholder_canonical() -> None:
    hostile = "x\nFAKE LOG LINE " + "y" * 500
    with pytest.raises(NormalizationError) as exc:
        validate_raw_payload(
            "acme", Raw, {"person": {"email": "a@b.com", hostile: 1}}, RULES
        )
    assert exc.value.canonical_path == "<unmapped>"
    assert "\n" not in str(exc.value)
    assert len(str(exc.value)) < 400


# Verifies: specs/lead-source-adapters/requirements.md#2.6
def test_validate_raw_payload_non_mapping_payload_is_named() -> None:
    with pytest.raises(NormalizationError) as exc:
        validate_raw_payload("acme", Raw, ["not", "a", "dict"], RULES)  # type: ignore[arg-type]
    assert exc.value.source_name == "acme"
