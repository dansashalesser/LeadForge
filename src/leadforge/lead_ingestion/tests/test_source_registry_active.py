"""Active source list: disabled sources are never constructed (task 7.2, req 3.4)."""

import sys
import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

import pytest

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    RateBucket,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings


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


def _make(name: str) -> type[_Base]:
    return type(f"{name.title()}Source", (_Base,), {"name": name})


class _Tripwire(_Base):
    """Records every construction attempt on both construction hooks, then fails."""

    name: ClassVar[str] = "tripwire"
    new_calls: ClassVar[list[str]] = []
    init_calls: ClassVar[list[str]] = []

    def __new__(cls, *args: object, **kwargs: object) -> "_Tripwire":
        _Tripwire.new_calls.append("new")
        raise AssertionError("disabled adapter was constructed (__new__)")

    def __init__(self, mode: DataMode) -> None:
        _Tripwire.init_calls.append("init")
        raise AssertionError("disabled adapter was constructed (__init__)")


@pytest.fixture(autouse=True)
def _reset_tripwire() -> None:
    _Tripwire.new_calls.clear()
    _Tripwire.init_calls.clear()


def _factory(cls: type[BaseLeadSource]) -> BaseLeadSource:
    return cls(DataMode.SYNTHETIC)


DISABLED = SourceSettings(enabled=False)


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_disabled_source_is_never_constructed() -> None:
    registry = SourceRegistry([_Tripwire, _make("alpha")], {"tripwire": DISABLED})

    active = registry.active(_factory)

    assert [s.name for s in active] == ["alpha"]
    assert _Tripwire.new_calls == []
    assert _Tripwire.init_calls == []


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_tripwire_really_fires_when_the_source_is_enabled() -> None:
    # Guards the guard: if this did not raise, the test above would prove nothing.
    registry = SourceRegistry([_Tripwire])

    with pytest.raises(AssertionError, match="__new__"):
        registry.active(_factory)
    assert _Tripwire.new_calls == ["new"]


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_factory_is_only_called_for_enabled_classes() -> None:
    seen: list[str] = []

    def factory(cls: type[BaseLeadSource]) -> BaseLeadSource:
        seen.append(cls.name)
        return _factory(cls)

    registry = SourceRegistry([_make("a"), _make("b"), _make("c")], {"b": DISABLED})
    registry.active(factory)

    assert seen == ["a", "c"]


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_disabled_source_stays_listed_but_is_not_obtainable_live() -> None:
    registry = SourceRegistry([_make("a"), _make("b")], {"b": DISABLED})

    assert registry.names() == ("a", "b")  # 7.3 descriptors still see it
    assert registry.source_class("b").name == "b"
    assert registry.settings("b").enabled is False
    assert registry.enabled_names() == ("a",)
    assert [s.name for s in registry.active(_factory)] == ["a"]


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_absent_config_means_enabled() -> None:
    registry = SourceRegistry([_make("a")])

    assert [s.name for s in registry.active(_factory)] == ["a"]


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_active_order_is_trust_rank_descending_then_name() -> None:
    registry = SourceRegistry(
        [_make("zeta"), _make("alpha"), _make("mid"), _make("beta")],
        {
            "zeta": SourceSettings(trust_rank=5),
            "mid": SourceSettings(trust_rank=9),
            "alpha": SourceSettings(trust_rank=5),
        },
    )

    assert registry.enabled_names() == ("mid", "alpha", "zeta", "beta")
    assert [s.name for s in registry.active(_factory)] == [
        "mid",
        "alpha",
        "zeta",
        "beta",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_active_order_ignores_registration_order() -> None:
    classes = [_make(n) for n in ("c", "a", "b")]

    forward = SourceRegistry(classes).enabled_names()
    backward = SourceRegistry(classes[::-1]).enabled_names()

    assert forward == backward == ("a", "b", "c")


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_all_disabled_gives_an_empty_list_not_an_error() -> None:
    registry = SourceRegistry(
        [_Tripwire, _make("a")], {"tripwire": DISABLED, "a": DISABLED}
    )

    assert registry.active(_factory) == ()
    assert registry.enabled_names() == ()


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_empty_registry_gives_an_empty_list() -> None:
    assert SourceRegistry().active(_factory) == ()


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_config_naming_an_unregistered_source_does_not_affect_the_list() -> None:
    registry = SourceRegistry([_make("a")], {"ghost": DISABLED})

    assert [s.name for s in registry.active(_factory)] == ["a"]
    assert registry.unknown_config_names == ("ghost",)


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_enabled_adapter_construction_failure_propagates_unwrapped() -> None:
    class Broken(_Base):
        name: ClassVar[str] = "broken"

    def factory(cls: type[BaseLeadSource]) -> BaseLeadSource:
        if cls.name == "broken":
            raise RuntimeError("boom")
        return _factory(cls)

    registry = SourceRegistry([_make("a"), Broken])

    with pytest.raises(RuntimeError, match="boom"):
        registry.active(factory)


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_factory_returning_the_wrong_kind_of_object_is_rejected() -> None:
    registry = SourceRegistry([_make("a"), _make("b")])
    other = _make("b")

    def factory(cls: type[BaseLeadSource]) -> BaseLeadSource:
        return _factory(other if cls.name == "a" else cls)

    with pytest.raises(TypeError, match="'a'"):
        registry.active(factory)


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_active_returns_a_fresh_tuple_of_new_instances_each_call() -> None:
    registry = SourceRegistry([_make("a")])

    first, second = registry.active(_factory), registry.active(_factory)

    assert isinstance(first, tuple)
    assert first[0] is not second[0]


# Verifies: specs/lead-source-adapters/requirements.md#3.4
def test_discovery_and_import_construct_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = "_adapters_active_probe"
    root = tmp_path / pkg
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "probe.py").write_text(
        textwrap.dedent(
            """
            from leadforge.lead_ingestion.tests.test_source_registry_active import (
                _Base,
            )

            CALLS = []

            class ProbeSource(_Base):
                name = "probe"

                def __new__(cls, *a, **k):
                    CALLS.append("new")
                    return super().__new__(cls)

                def __init__(self, mode):
                    CALLS.append("init")
                    super().__init__(mode)
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        registry = SourceRegistry.discover(pkg, config={"probe": DISABLED})
        probe = sys.modules[f"{pkg}.probe"]
        assert registry.names() == ("probe",)
        assert registry.active(_factory) == ()
        assert probe.CALLS == []
        enabled = SourceRegistry.discover(pkg)
        assert [s.name for s in enabled.active(_factory)] == ["probe"]
        assert probe.CALLS == ["new", "init"]
    finally:
        for key in [k for k in sys.modules if k.split(".")[0] == pkg]:
            del sys.modules[key]
