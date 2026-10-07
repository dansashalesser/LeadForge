"""Named error taxonomy for every failure class in the ingestion slice."""

import copy
import pickle

import pytest

from leadforge.lead_ingestion.errors import (
    DuplicateSourceNameError,
    FixtureSchemaError,
    InvalidAbsenceError,
    NoAccessibleAccountError,
    NormalizationError,
    SourceComplianceRestricted,
    SourceDiscoveryError,
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
    SourceTimedOut,
    SourceTransient,
    SourceUnauthorized,
    UndeclaredEndpointError,
)

SOURCE_ERRORS: list[SourceError] = [
    SourceUnauthorized(
        "provider_one", endpoint="/people/match", scope_cause="no_scope"
    ),
    SourceRateLimited("provider_three", retry_after_s=2.0, cause="secondly"),
    SourceQuotaExhausted("provider_two"),
    SourceTransient("provider_one", status=503),
    SourceTimedOut("google_search"),
    SourceComplianceRestricted("provider_two", subject="jane@example.com"),
    NormalizationError(
        "provider_one", raw_field_path="person.email", canonical_path="Lead.email"
    ),
    NoAccessibleAccountError("provider_three"),
    InvalidAbsenceError(
        "provider_two",
        canonical_path="email",
        raw_field_path="data.email",
        reason="surface not declared",
    ),
]


# Verifies: specs/lead-source-adapters/requirements.md#6.2
@pytest.mark.parametrize("error", SOURCE_ERRORS, ids=lambda e: type(e).__name__)
def test_every_source_error_derives_from_root_and_names_its_provider(
    error: SourceError,
) -> None:
    assert isinstance(error, SourceError)
    assert isinstance(error, Exception)
    assert error.source_name in str(error)


# Verifies: specs/lead-source-adapters/requirements.md#6.3
def test_source_error_classes_are_distinct_named_types() -> None:
    types = {type(e) for e in SOURCE_ERRORS}
    assert len(types) == len(SOURCE_ERRORS)


# Verifies: specs/lead-source-adapters/requirements.md#6.2
def test_unauthorized_names_endpoint_and_scope_cause() -> None:
    err = SourceUnauthorized(
        "provider_one", endpoint="/people/match", scope_cause="no_scope"
    )
    assert err.endpoint == "/people/match"
    assert err.scope_cause == "no_scope"
    assert "/people/match" in str(err)
    assert "no_scope" in str(err)


# Verifies: specs/lead-source-adapters/requirements.md#6.2
def test_unauthorized_scope_cause_is_optional() -> None:
    assert SourceUnauthorized("provider_two", endpoint="/v2/x").scope_cause is None


# Verifies: specs/lead-source-adapters/requirements.md#6.3
def test_rate_limited_carries_retry_after_and_cause() -> None:
    err = SourceRateLimited("provider_three", retry_after_s=1.5, cause="daily")
    assert err.retry_after_s == 1.5
    assert err.cause == "daily"
    assert SourceRateLimited("provider_one", cause="x").retry_after_s is None


# Verifies: specs/lead-source-adapters/requirements.md#6.3
def test_transient_status_is_optional() -> None:
    assert SourceTransient("provider_one", status=502).status == 502
    assert SourceTransient("provider_one").status is None


# Verifies: specs/lead-source-adapters/requirements.md#6.2
def test_compliance_restricted_names_subject() -> None:
    assert (
        SourceComplianceRestricted("provider_two", subject="a@b.co").subject == "a@b.co"
    )


# Verifies: specs/lead-source-adapters/requirements.md#2.6
def test_normalization_error_names_provider_raw_and_canonical_path() -> None:
    err = NormalizationError(
        "provider_one", raw_field_path="person.email", canonical_path="Lead.email"
    )
    assert err.raw_field_path == "person.email"
    assert err.canonical_path == "Lead.email"
    text = str(err)
    assert "provider_one" in text
    assert "person.email" in text
    assert "Lead.email" in text


# Verifies: specs/lead-source-adapters/requirements.md#2.6
def test_non_source_errors_name_their_provider_and_offender() -> None:
    fixture = FixtureSchemaError("provider_two", field="data.emails[0].value")
    assert (fixture.provider, fixture.field) == ("provider_two", "data.emails[0].value")
    assert "provider_two" in str(fixture)
    assert "data.emails[0].value" in str(fixture)

    dup = DuplicateSourceNameError("provider_one")
    assert dup.name == "provider_one"
    assert "provider_one" in str(dup)

    undeclared = UndeclaredEndpointError("provider_one", path="/v1/secret")
    assert (undeclared.provider, undeclared.path) == ("provider_one", "/v1/secret")
    assert "provider_one" in str(undeclared)
    assert "/v1/secret" in str(undeclared)


# Verifies: specs/lead-source-adapters/requirements.md#6.3
def test_non_source_errors_are_not_source_errors() -> None:
    for err in (
        FixtureSchemaError("provider_two", field="f"),
        DuplicateSourceNameError("provider_one"),
        SourceDiscoveryError("pkg.mod", detail="d"),
        UndeclaredEndpointError("provider_one", path="/p"),
    ):
        assert not isinstance(err, SourceError)


# Verifies: specs/lead-source-adapters/requirements.md#6.3
def test_retry_dispatch_by_type_distinguishes_every_failure_class() -> None:
    retryable = (SourceTransient, SourceRateLimited)
    flags = {type(e).__name__: isinstance(e, retryable) for e in SOURCE_ERRORS}
    assert flags["SourceTransient"]
    assert flags["SourceRateLimited"]
    others = {
        k: v
        for k, v in flags.items()
        if k not in {"SourceTransient", "SourceRateLimited"}
    }
    assert not any(others.values())


# Verifies: specs/lead-source-adapters/requirements.md#6.3
@pytest.mark.parametrize(
    "error",
    [
        *SOURCE_ERRORS,
        FixtureSchemaError("provider_two", field="f"),
        DuplicateSourceNameError("provider_one"),
        SourceDiscoveryError("pkg.mod", detail="d"),
        UndeclaredEndpointError("provider_one", path="/p"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_errors_round_trip_through_pickle_and_copy(error: Exception) -> None:
    for clone in (pickle.loads(pickle.dumps(error)), copy.copy(error)):
        assert type(clone) is type(error)
        assert clone.args == error.args
        assert vars(clone) == vars(error)
        assert str(clone) == str(error)


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_invalid_absence_names_provider_both_paths_and_reason() -> None:
    err = InvalidAbsenceError(
        "provider_two",
        canonical_path="Lead.email",
        raw_field_path="data.email",
        reason="surface not declared",
    )
    assert isinstance(err, SourceError)
    assert (err.canonical_path, err.raw_field_path) == ("Lead.email", "data.email")
    assert err.reason == "surface not declared"
    text = str(err)
    for part in ("provider_two", "Lead.email", "data.email", "surface not declared"):
        assert part in text
    assert (
        InvalidAbsenceError(
            "provider_two", canonical_path="x", raw_field_path=None, reason="r"
        ).raw_field_path
        is None
    )
