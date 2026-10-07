"""Task 8.1: live versus synthetic mode with a stated reason (Requirement 4)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import ClassVar

import pytest
from structlog.testing import capture_logs

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    LiveAccess,
    RateBucket,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.mode_resolution import (
    ModeResolution,
    make_mode_resolver,
    resolve_data_mode,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings

SECRET = "sk-live-SUPERSECRET-0123456789"


class _Base(BaseLeadSource):
    capabilities: ClassVar[frozenset[Capability]] = frozenset()
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        raise NotImplementedError

    def normalize(self, raw: RawBatch) -> list:  # type: ignore[type-arg]
        return []


def _make(name: str = "a", **attrs: object) -> type[_Base]:
    return type(f"{name.title()}Source", (_Base,), {"name": name, **attrs})


CRED = _make("cred", required_env=("A_KEY",))
TWO = _make("two", required_env=("ID_VAR", "SECRET_VAR"))
KEYLESS = _make("keyless")
DEAD = _make("dead", required_env=("D_KEY",), live_access=LiveAccess.UNAVAILABLE)
NONE = SourceSettings()


# --- precedence ----------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#4.2
def test_all_credentials_present_resolves_live_with_reason() -> None:
    got = resolve_data_mode(CRED, NONE, {"A_KEY": SECRET})
    assert got.mode is DataMode.LIVE
    assert got.reason == "all declared credentials present"


# Verifies: specs/lead-source-adapters/requirements.md#4.1
def test_missing_credentials_resolve_synthetic_naming_every_missing_variable() -> None:
    got = resolve_data_mode(TWO, NONE, {})
    assert got.mode is DataMode.SYNTHETIC
    assert got.reason == "missing credentials: ID_VAR, SECRET_VAR"


def test_only_the_absent_variable_is_named() -> None:
    got = resolve_data_mode(TWO, NONE, {"ID_VAR": SECRET})
    assert got.mode is DataMode.SYNTHETIC
    assert got.reason == "missing credentials: SECRET_VAR"


# Verifies: specs/lead-source-adapters/requirements.md#4.3
def test_per_source_override_wins_over_present_credential() -> None:
    settings = SourceSettings(mode=DataMode.SYNTHETIC)
    got = resolve_data_mode(CRED, settings, {"A_KEY": SECRET})
    assert got.mode is DataMode.SYNTHETIC
    assert got.reason == "per-source override: synthetic"


def test_per_source_live_override_wins_over_missing_credential() -> None:
    got = resolve_data_mode(CRED, SourceSettings(mode=DataMode.LIVE), {})
    assert got.mode is DataMode.LIVE
    assert got.reason == "per-source override: live"


def test_per_source_override_beats_global_override() -> None:
    got = resolve_data_mode(
        CRED, SourceSettings(mode=DataMode.LIVE), {}, global_override=DataMode.SYNTHETIC
    )
    assert got.mode is DataMode.LIVE
    assert got.reason == "per-source override: live"


def test_global_override_beats_credentials_and_classification() -> None:
    got = resolve_data_mode(
        CRED, NONE, {"A_KEY": SECRET}, global_override=DataMode.SYNTHETIC
    )
    assert got.mode is DataMode.SYNTHETIC
    assert got.reason == "global override: synthetic"


def test_global_live_override_forces_live_even_without_credentials() -> None:
    got = resolve_data_mode(CRED, NONE, {}, global_override=DataMode.LIVE)
    assert got.mode is DataMode.LIVE
    assert got.reason == "global override: live"


def test_global_override_beats_unavailable_classification() -> None:
    # Spec order puts the global override above classification (design, resolver).
    got = resolve_data_mode(DEAD, NONE, {}, global_override=DataMode.LIVE)
    assert got.mode is DataMode.LIVE


# Verifies: specs/lead-source-adapters/requirements.md#4.1
def test_unavailable_classification_is_synthetic_even_with_credentials() -> None:
    got = resolve_data_mode(DEAD, NONE, {"D_KEY": SECRET})
    assert got.mode is DataMode.SYNTHETIC
    assert got.reason == "live access unavailable"


def test_per_source_override_can_force_live_for_unavailable_source() -> None:
    got = resolve_data_mode(DEAD, SourceSettings(mode=DataMode.LIVE), {})
    assert got.mode is DataMode.LIVE


def test_configured_live_access_override_replaces_declared_classification() -> None:
    settings = SourceSettings(live_access=LiveAccess.AVAILABLE)
    assert resolve_data_mode(DEAD, settings, {"D_KEY": SECRET}).mode is DataMode.LIVE
    settings = SourceSettings(live_access=LiveAccess.UNAVAILABLE)
    got = resolve_data_mode(CRED, settings, {"A_KEY": SECRET})
    assert got.mode is DataMode.SYNTHETIC
    assert got.reason == "live access unavailable"


def test_gated_source_with_credentials_resolves_live() -> None:
    gated = _make("gated", required_env=("G",), live_access=LiveAccess.GATED)
    assert resolve_data_mode(gated, NONE, {"G": SECRET}).mode is DataMode.LIVE


def test_source_declaring_no_credentials_resolves_live() -> None:
    got = resolve_data_mode(KEYLESS, NONE, {})
    assert got.mode is DataMode.LIVE
    assert got.reason == "no credentials required"


# --- empty credential counts as absent ----------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#4.1
@pytest.mark.parametrize("blank", ["", " ", "\t\n  "])
def test_blank_credential_value_counts_as_absent(blank: str) -> None:
    got = resolve_data_mode(CRED, NONE, {"A_KEY": blank})
    assert got.mode is DataMode.SYNTHETIC
    assert got.reason == "missing credentials: A_KEY"


def test_environ_none_falls_back_to_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("A_KEY", SECRET)
    assert resolve_data_mode(CRED, NONE).mode is DataMode.LIVE
    monkeypatch.delenv("A_KEY")
    assert resolve_data_mode(CRED, NONE).mode is DataMode.SYNTHETIC


# --- global override input ----------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("live", DataMode.LIVE),
        (" Synthetic ", DataMode.SYNTHETIC),
        ("LIVE", DataMode.LIVE),
    ],
)
def test_global_override_accepts_padded_and_mixed_case_strings(
    raw: str, expected: DataMode
) -> None:
    assert resolve_data_mode(CRED, NONE, {}, global_override=raw).mode is expected


@pytest.mark.parametrize("unset", [None, "", "   "])
def test_blank_or_none_global_override_means_no_override(unset: str | None) -> None:
    got = resolve_data_mode(CRED, NONE, {"A_KEY": SECRET}, global_override=unset)
    assert got.reason == "all declared credentials present"


@pytest.mark.parametrize("bad", ["demo", "livee", "true"])
def test_invalid_global_override_string_is_rejected_loudly(bad: str) -> None:
    with pytest.raises(ValueError, match="global mode override"):
        resolve_data_mode(CRED, NONE, {}, global_override=bad)


def test_invalid_global_override_type_is_rejected() -> None:
    with pytest.raises(TypeError):
        resolve_data_mode(CRED, NONE, {}, global_override=1)  # type: ignore[arg-type]


# --- result type ----------------------------------


def test_result_is_frozen_and_equal_by_value() -> None:
    r = ModeResolution(DataMode.LIVE, "why")
    assert r == ModeResolution(DataMode.LIVE, "why")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.mode = DataMode.SYNTHETIC  # type: ignore[misc]


# --- logging (4.4) ----------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#4.4
def test_one_log_line_names_source_mode_and_reason() -> None:
    with capture_logs() as logs:
        got = resolve_data_mode(TWO, NONE, {})
    assert len(logs) == 1
    assert logs[0]["event"] == "data_mode_resolved"
    assert logs[0]["log_level"] == "info"
    assert logs[0]["source"] == "two"
    assert logs[0]["mode"] == "synthetic"
    assert logs[0]["reason"] == got.reason


def test_logged_for_every_precedence_branch_exactly_once() -> None:
    calls = [
        (CRED, SourceSettings(mode=DataMode.LIVE), None),
        (CRED, NONE, "synthetic"),
        (DEAD, NONE, None),
        (CRED, NONE, None),
        (KEYLESS, NONE, None),
    ]
    for cls, settings, glob in calls:
        with capture_logs() as logs:
            resolve_data_mode(cls, settings, {"A_KEY": SECRET}, global_override=glob)
        assert len(logs) == 1, (cls.name, logs)


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_planted_secret_never_reaches_logs_reasons_or_reprs() -> None:
    env = {"ID_VAR": SECRET, "SECRET_VAR": SECRET, "A_KEY": SECRET, "D_KEY": SECRET}
    for cls in (CRED, TWO, DEAD, KEYLESS):
        for glob in (None, "live"):
            with capture_logs() as logs:
                got = resolve_data_mode(cls, NONE, env, global_override=glob)
            assert SECRET not in repr(logs)
            assert SECRET not in repr(got)
            assert SECRET not in got.reason
    partial = {"ID_VAR": SECRET}
    with capture_logs() as logs:
        got = resolve_data_mode(TWO, NONE, partial)
    assert SECRET not in repr(logs) + repr(got)
    assert "SECRET_VAR" in got.reason  # the NAME, not a value


# --- registry seam ----------------------------------


def test_factory_resolver_plugs_into_describe_and_logs_each_source_once() -> None:
    registry = SourceRegistry(
        [CRED, TWO, DEAD, KEYLESS],
        {"cred": SourceSettings(mode=DataMode.SYNTHETIC)},
    )
    resolver = make_mode_resolver({"A_KEY": SECRET, "D_KEY": SECRET})
    with capture_logs() as logs:
        got = {
            d.name: d.resolved_mode
            for d in registry.describe({}, resolve_mode=resolver)
        }
    assert got == {
        "cred": DataMode.SYNTHETIC,  # per-source override beats the present credential
        "two": DataMode.SYNTHETIC,
        "dead": DataMode.SYNTHETIC,
        "keyless": DataMode.LIVE,
    }
    assert sorted(entry["source"] for entry in logs) == [
        "cred",
        "dead",
        "keyless",
        "two",
    ]
    by_source = {entry["source"]: entry for entry in logs}
    assert by_source["cred"]["reason"] == "per-source override: synthetic"


def test_factory_resolver_applies_global_override() -> None:
    registry = SourceRegistry([CRED])
    resolver = make_mode_resolver({}, global_override="live")
    assert (
        registry.describe({}, resolve_mode=resolver)[0].resolved_mode is DataMode.LIVE
    )


def test_factory_validates_global_override_eagerly() -> None:
    with pytest.raises(ValueError, match="global mode override"):
        make_mode_resolver({}, global_override="bogus")
