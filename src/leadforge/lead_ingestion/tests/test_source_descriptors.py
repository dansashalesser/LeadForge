"""Source descriptors and live-access classification (task 7.3, req 3.5, 3.6)."""

import dataclasses
import json
from collections.abc import Mapping
from typing import ClassVar

import pytest

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    LiveAccess,
    RateBucket,
    RateWindow,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import (
    SourceDescriptor,
    SourceRegistry,
    SourceSettings,
)

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


def _make(name: str, **attrs: object) -> type[_Base]:
    return type(f"{name.title()}Source", (_Base,), {"name": name, **attrs})


def _bucket(documented: bool, name: str = "default") -> RateBucket:
    return RateBucket(
        name=name,
        windows=(RateWindow(requests=10, per_seconds=1.0),),
        documented=documented,
        doc_url="https://example.invalid/docs",
    )


def _describe(
    registry: SourceRegistry, environ: Mapping[str, str] | None = None
) -> dict[str, SourceDescriptor]:
    return {d.name: d for d in registry.describe({} if environ is None else environ)}


# --- adapter contract: live_access declaration ---------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#3.6
def test_live_access_has_exactly_three_values() -> None:
    assert {m.value for m in LiveAccess} == {"available", "gated", "unavailable"}


# Verifies: specs/lead-source-adapters/requirements.md#3.6
def test_adapter_declares_live_access_with_explicit_default_available() -> None:
    assert _make("plain").live_access is LiveAccess.AVAILABLE


# Verifies: specs/lead-source-adapters/requirements.md#3.6
def test_adapter_can_declare_its_own_live_access() -> None:
    cls = _make("zi", live_access=LiveAccess.UNAVAILABLE)
    assert cls.live_access is LiveAccess.UNAVAILABLE


# Verifies: specs/lead-source-adapters/requirements.md#3.6
@pytest.mark.parametrize("bad", ["available", None, 1, True])
def test_adapter_rejects_non_enum_live_access_at_construction(bad: object) -> None:
    cls = _make("bad", live_access=bad)
    with pytest.raises(TypeError, match="live_access"):
        cls(DataMode.SYNTHETIC)


# --- descriptor content (3.5) --------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_descriptor_reports_name_capabilities_and_enabled_state() -> None:
    cls = _make("alpha", capabilities=frozenset({Capability.SEARCH, Capability.ENRICH}))
    d = _describe(SourceRegistry([cls]))["alpha"]
    assert d.name == "alpha"
    assert d.enabled is True
    assert d.capabilities == (Capability.ENRICH, Capability.SEARCH)


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_descriptor_with_no_capabilities_has_empty_tuple() -> None:
    assert _describe(SourceRegistry([_make("a")]))["a"].capabilities == ()


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_disabled_source_is_described_and_flagged_without_construction() -> None:
    class _Tripwire(_Base):
        name: ClassVar[str] = "off"

        def __new__(cls, *a: object, **k: object) -> "_Tripwire":
            raise AssertionError("describe constructed an adapter")

    registry = SourceRegistry(
        [_Tripwire, _make("on")], {"off": SourceSettings(enabled=False)}
    )
    got = _describe(registry)
    assert set(got) == {"off", "on"}
    assert got["off"].enabled is False
    assert got["on"].enabled is True


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_descriptors_are_ordered_by_name_regardless_of_registration() -> None:
    forward = SourceRegistry([_make("b"), _make("a"), _make("c")])
    backward = SourceRegistry([_make("c"), _make("a"), _make("b")])
    assert [d.name for d in forward.describe({})] == ["a", "b", "c"]
    assert forward.describe({}) == backward.describe({})


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_empty_registry_describes_nothing() -> None:
    assert SourceRegistry().describe({}) == ()


# --- credentials (3.5, 3.4 env-only) -------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_credentials_found_when_every_declared_variable_is_set() -> None:
    cls = _make("k", required_env=("K_ID", "K_SECRET"))
    d = _describe(SourceRegistry([cls]), {"K_ID": "x", "K_SECRET": "y"})["k"]
    assert d.credential_present is True
    assert d.missing_env == ()


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_missing_variables_are_named_in_declared_order() -> None:
    cls = _make("k", required_env=("K_ID", "K_SECRET", "K_TOKEN"))
    d = _describe(SourceRegistry([cls]), {"K_SECRET": "y"})["k"]
    assert d.credential_present is False
    assert d.missing_env == ("K_ID", "K_TOKEN")


# Verifies: specs/lead-source-adapters/requirements.md#3.5
@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
def test_blank_value_counts_as_missing(blank: str) -> None:
    cls = _make("k", required_env=("K_ID",))
    d = _describe(SourceRegistry([cls]), {"K_ID": blank})["k"]
    assert d.credential_present is False
    assert d.missing_env == ("K_ID",)


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_source_declaring_no_credentials_is_present_with_nothing_missing() -> None:
    d = _describe(SourceRegistry([_make("free")]), {"UNRELATED": "v"})["free"]
    assert d.credential_present is True
    assert d.missing_env == ()


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_describe_reads_the_injected_environ_not_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("K_ID", "from-process")
    cls = _make("k", required_env=("K_ID",))
    d = _describe(SourceRegistry([cls]), {})["k"]
    assert d.credential_present is False


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_describe_defaults_to_process_environment_when_none_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("K_ID", "from-process")
    cls = _make("k", required_env=("K_ID",))
    (d,) = SourceRegistry([cls]).describe()
    assert d.credential_present is True


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_credential_values_never_appear_in_any_descriptor_form() -> None:
    cls = _make("k", required_env=("K_ID", "K_SECRET"))
    (d,) = SourceRegistry([cls]).describe({"K_ID": SECRET, "K_SECRET": ""})
    for field in dataclasses.fields(d):
        assert SECRET not in repr(getattr(d, field.name))
    assert SECRET not in repr(d)
    assert SECRET not in str(d)
    assert SECRET not in json.dumps(d.to_dict())
    assert SECRET not in str(dataclasses.asdict(d))


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_secret_in_a_variable_name_position_is_not_echoed_from_environ() -> None:
    # Only declared names are looked up; an undeclared variable holding a secret
    # is not read into the descriptor at all.
    cls = _make("k", required_env=("K_ID",))
    (d,) = SourceRegistry([cls]).describe({"K_ID": "ok", "OTHER_KEY": SECRET})
    assert SECRET not in repr(d)


# --- mode seam (8.1 owns the real resolver) ------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_mode_defaults_to_synthetic_without_resolver_or_override() -> None:
    d = _describe(SourceRegistry([_make("a")]), {"A": "x"})["a"]
    assert d.resolved_mode is DataMode.SYNTHETIC


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_per_source_mode_override_is_the_resolved_mode() -> None:
    registry = SourceRegistry([_make("a")], {"a": SourceSettings(mode=DataMode.LIVE)})
    assert _describe(registry)["a"].resolved_mode is DataMode.LIVE


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_injected_resolver_supplies_the_mode_and_sees_class_and_settings() -> None:
    seen: list[tuple[str, SourceSettings]] = []

    def resolver(cls: type[BaseLeadSource], settings: SourceSettings) -> DataMode:
        seen.append((cls.name, settings))
        return DataMode.LIVE if cls.name == "a" else DataMode.SYNTHETIC

    registry = SourceRegistry([_make("b"), _make("a")])
    got = {d.name: d for d in registry.describe({}, resolve_mode=resolver)}
    assert got["a"].resolved_mode is DataMode.LIVE
    assert got["b"].resolved_mode is DataMode.SYNTHETIC
    assert sorted(n for n, _ in seen) == ["a", "b"]


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_resolver_returning_a_non_mode_is_rejected() -> None:
    registry = SourceRegistry([_make("a")])
    with pytest.raises(TypeError, match="DataMode"):
        registry.describe({}, resolve_mode=lambda c, s: "live")  # type: ignore[arg-type,return-value]


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_mode_override_wins_over_injected_resolver() -> None:
    registry = SourceRegistry(
        [_make("a")], {"a": SourceSettings(mode=DataMode.SYNTHETIC)}
    )
    got = registry.describe({}, resolve_mode=lambda c, s: DataMode.LIVE)
    assert got[0].resolved_mode is DataMode.SYNTHETIC


# --- rate-limit provenance (3.5) -----------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_rate_limit_documented_when_every_bucket_is_published() -> None:
    cls = _make("a", rate_limit={"x": _bucket(True, "x"), "y": _bucket(True, "y")})
    assert _describe(SourceRegistry([cls]))["a"].rate_limit_documented is True


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_rate_limit_self_imposed_when_any_bucket_is_self_imposed() -> None:
    cls = _make("a", rate_limit={"x": _bucket(True, "x"), "y": _bucket(False, "y")})
    assert _describe(SourceRegistry([cls]))["a"].rate_limit_documented is False


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_rate_limit_self_imposed_when_all_buckets_self_imposed() -> None:
    cls = _make("a", rate_limit={"x": _bucket(False, "x")})
    assert _describe(SourceRegistry([cls]))["a"].rate_limit_documented is False


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_no_declared_bucket_is_not_reported_as_published() -> None:
    assert _describe(SourceRegistry([_make("a")]))["a"].rate_limit_documented is False


# --- live-access classification (3.6) ------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#3.6
@pytest.mark.parametrize("declared", list(LiveAccess))
def test_descriptor_reports_declared_live_access(declared: LiveAccess) -> None:
    cls = _make("a", live_access=declared)
    assert _describe(SourceRegistry([cls]))["a"].live_access is declared


# Verifies: specs/lead-source-adapters/requirements.md#3.6
def test_config_override_replaces_declared_live_access() -> None:
    cls = _make("a", live_access=LiveAccess.GATED)
    registry = SourceRegistry(
        [cls], {"a": SourceSettings(live_access=LiveAccess.AVAILABLE)}
    )
    assert _describe(registry)["a"].live_access is LiveAccess.AVAILABLE


# Verifies: specs/lead-source-adapters/requirements.md#3.6
def test_absent_override_keeps_declared_live_access() -> None:
    cls = _make("a", live_access=LiveAccess.UNAVAILABLE)
    registry = SourceRegistry([cls], {"a": SourceSettings(enabled=True)})
    assert _describe(registry)["a"].live_access is LiveAccess.UNAVAILABLE


# Verifies: specs/lead-source-adapters/requirements.md#3.6
def test_override_applies_only_to_the_named_source() -> None:
    registry = SourceRegistry(
        [_make("a"), _make("b")],
        {"a": SourceSettings(live_access=LiveAccess.GATED)},
    )
    got = _describe(registry)
    assert got["a"].live_access is LiveAccess.GATED
    assert got["b"].live_access is LiveAccess.AVAILABLE


# --- SourceSettings override validation ----------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#3.6
def test_settings_override_defaults_are_none() -> None:
    s = SourceSettings()
    assert s.mode is None
    assert s.live_access is None


# Verifies: specs/lead-source-adapters/requirements.md#3.6
def test_settings_accept_valid_strings_for_overrides() -> None:
    s = SourceSettings(mode="live", live_access="gated")  # type: ignore[arg-type]
    assert s.mode is DataMode.LIVE
    assert s.live_access is LiveAccess.GATED


# Verifies: specs/lead-source-adapters/requirements.md#3.6
@pytest.mark.parametrize("bad", ["maybe", "", "AVAILABLE ", "unknown"])
def test_settings_reject_unknown_live_access_value(bad: str) -> None:
    with pytest.raises(ValueError, match="live_access"):
        SourceSettings(live_access=bad)  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#3.6
@pytest.mark.parametrize("bad", [1, True, 1.5, ["gated"]])
def test_settings_reject_non_str_live_access(bad: object) -> None:
    with pytest.raises(TypeError, match="live_access"):
        SourceSettings(live_access=bad)  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#3.5
@pytest.mark.parametrize("bad", ["hybrid", ""])
def test_settings_reject_unknown_mode_value(bad: str) -> None:
    with pytest.raises(ValueError, match="mode"):
        SourceSettings(mode=bad)  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#3.5
@pytest.mark.parametrize("bad", [1, True, 1.5])
def test_settings_reject_non_str_mode(bad: object) -> None:
    with pytest.raises(TypeError, match="mode"):
        SourceSettings(mode=bad)  # type: ignore[arg-type]


# --- serialisation and immutability --------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_descriptor_is_frozen() -> None:
    (d,) = SourceRegistry([_make("a")]).describe({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.name = "x"  # type: ignore[misc]


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_descriptor_serialises_to_plain_json_values() -> None:
    cls = _make(
        "a",
        capabilities=frozenset({Capability.SEARCH}),
        required_env=("A_KEY", "A_ORG"),
        live_access=LiveAccess.GATED,
        rate_limit={"default": _bucket(False)},
    )
    registry = SourceRegistry([cls], {"a": SourceSettings(enabled=False)})
    (d,) = registry.describe({"A_KEY": "v"})
    assert json.loads(json.dumps(d.to_dict())) == {
        "name": "a",
        "enabled": False,
        "capabilities": ["search"],
        "resolved_mode": "synthetic",
        "live_access": "gated",
        "credential_present": False,
        "missing_env": ["A_ORG"],
        "rate_limit_documented": False,
    }


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_descriptor_is_hashable_and_equal_by_value() -> None:
    registry = SourceRegistry([_make("a")])
    assert registry.describe({}) == registry.describe({})
    assert len({*registry.describe({}), *registry.describe({})}) == 1


# Verifies: specs/lead-source-adapters/requirements.md#3.5
def test_describe_does_not_mutate_registry_state() -> None:
    registry = SourceRegistry([_make("a")], {"a": SourceSettings(enabled=False)})
    before = (registry.names(), registry.enabled_names(), registry.settings("a"))
    registry.describe({})
    assert before == (
        registry.names(),
        registry.enabled_names(),
        registry.settings("a"),
    )
