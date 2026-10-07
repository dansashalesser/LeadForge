"""Source Registry: package-scan discovery and duplicate rejection (task 7.1).

Test adapters live in throwaway packages under ``tmp_path``, never in the real
adapter package, so the suite stays independent of which providers exist.
"""

import itertools
import sys
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import ClassVar

import pytest

import leadforge.lead_ingestion.adapters as real_adapters
from leadforge.lead_ingestion.base_source import BaseLeadSource
from leadforge.lead_ingestion.errors import (
    DuplicateSourceNameError,
    SourceDiscoveryError,
)
from leadforge.lead_ingestion.registry import (
    ADAPTER_PACKAGE,
    LOWEST_TRUST_RANK,
    SourceRegistry,
    SourceSettings,
)

HEADER = """
from collections.abc import Mapping
from typing import ClassVar

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource, Capability, ChargeUnit, CostClass, Endpoint, RateBucket,
)


class _Declared(BaseLeadSource):
    # Every declaration but no name and no fetch_raw/normalize: an abstract base.
    capabilities: ClassVar[frozenset[Capability]] = frozenset()
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {}
    required_env: ClassVar[tuple[str, ...]] = ()
"""


def adapter(cls_name: str, name: object, base: str = "_Declared") -> str:
    """Source text of a concrete adapter class declaring ``name``."""
    return f"""
class {cls_name}({base}):
    name = {name!r}

    async def fetch_raw(self, request):
        raise NotImplementedError

    def normalize(self, raw):
        return []
"""


def module(*classes: str) -> str:
    return HEADER + "".join(classes)


_counter = itertools.count()
MakePackage = Callable[[dict[str, str]], str]


@pytest.fixture
def make_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MakePackage]:
    """Write ``{relative path: source}`` into a uniquely named throwaway package."""
    created: list[str] = []
    monkeypatch.syspath_prepend(str(tmp_path))

    def build(files: dict[str, str]) -> str:
        pkg = f"_adapters_under_test_{next(_counter)}"
        created.append(pkg)
        root = tmp_path / pkg
        root.mkdir()
        (root / "__init__.py").write_text("")
        for rel, source in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            for parent in path.relative_to(root).parents:
                init = root / parent / "__init__.py"
                if not init.exists():
                    init.write_text("")
            path.write_text(textwrap.dedent(source))
        return pkg

    yield build
    for key in [k for k in sys.modules if k.split(".")[0] in created]:
        del sys.modules[key]


class _StraySource(BaseLeadSource):
    """A subclass outside any scanned package; discovery must never see it."""

    name: ClassVar[str] = "stray"


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_discovers_every_adapter_module_without_any_listing(
    make_package: MakePackage,
) -> None:
    pkg = make_package(
        {
            "beta.py": module(adapter("BetaSource", "beta")),
            "alpha.py": module(adapter("AlphaSource", "alpha")),
        }
    )

    registry = SourceRegistry.discover(pkg)

    assert registry.names() == ("alpha", "beta")
    assert registry.source_class("alpha").__name__ == "AlphaSource"
    assert issubclass(registry.source_class("beta"), BaseLeadSource)
    assert "stray" not in registry.names()


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_adding_one_module_adds_exactly_one_source(make_package: MakePackage) -> None:
    base = {"alpha.py": module(adapter("AlphaSource", "alpha"))}
    before = SourceRegistry.discover(make_package(base))
    after = SourceRegistry.discover(
        make_package({**base, "gamma.py": module(adapter("GammaSource", "gamma"))})
    )

    assert after.names() == (*before.names(), "gamma")


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_discovers_adapters_in_nested_subpackages(make_package: MakePackage) -> None:
    pkg = make_package({"deep/inner/zeta.py": module(adapter("ZetaSource", "zeta"))})

    assert SourceRegistry.discover(pkg).names() == ("zeta",)


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_abstract_intermediate_bases_are_not_registered(
    make_package: MakePackage,
) -> None:
    pkg = make_package({"a.py": module(adapter("AlphaSource", "alpha"))})

    registry = SourceRegistry.discover(pkg)

    assert registry.names() == ("alpha",)  # _Declared and BaseLeadSource are absent


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_subclass_of_a_concrete_adapter_with_its_own_name_is_registered(
    make_package: MakePackage,
) -> None:
    pkg = make_package(
        {
            "a.py": module(
                adapter("AlphaSource", "alpha"),
                adapter("AlphaPlusSource", "alpha_plus", base="AlphaSource"),
            )
        }
    )

    assert SourceRegistry.discover(pkg).names() == ("alpha", "alpha_plus")


# Verifies: specs/lead-source-adapters/requirements.md#3.3
def test_subclass_inheriting_a_concrete_name_is_a_duplicate(
    make_package: MakePackage,
) -> None:
    inherits = """
class AlphaVariant(AlphaSource):
    pass
"""
    pkg = make_package({"a.py": module(adapter("AlphaSource", "alpha"), inherits)})

    with pytest.raises(DuplicateSourceNameError) as exc:
        SourceRegistry.discover(pkg)
    assert exc.value.name == "alpha"


# Verifies: specs/lead-source-adapters/requirements.md#3.3
def test_same_name_in_two_modules_fails_startup_with_a_named_error(
    make_package: MakePackage,
) -> None:
    pkg = make_package(
        {
            "one.py": module(adapter("OneSource", "provider_one")),
            "two.py": module(adapter("TwoSource", "provider_one")),
        }
    )

    with pytest.raises(DuplicateSourceNameError) as exc:
        SourceRegistry.discover(pkg)

    assert exc.value.name == "provider_one"
    assert "provider_one" in str(exc.value)


# Verifies: specs/lead-source-adapters/requirements.md#3.3
@pytest.mark.parametrize(
    "other", ["Provider_One", "PROVIDER_ONE", " provider_one ", "provider_one\t"]
)
def test_names_differing_only_in_case_or_padding_collide(
    make_package: MakePackage, other: str
) -> None:
    pkg = make_package(
        {
            "one.py": module(adapter("OneSource", "provider_one")),
            "two.py": module(adapter("TwoSource", other)),
        }
    )

    with pytest.raises(DuplicateSourceNameError):
        SourceRegistry.discover(pkg)


# Verifies: specs/lead-source-adapters/requirements.md#3.3
def test_constructor_rejects_duplicates_given_classes_directly(
    make_package: MakePackage,
) -> None:
    pkg = make_package(
        {
            "one.py": module(adapter("OneSource", "provider_one")),
            "two.py": module(adapter("TwoSource", "other")),
        }
    )
    registry = SourceRegistry.discover(pkg)
    one = registry.source_class("provider_one")

    with pytest.raises(DuplicateSourceNameError):
        SourceRegistry([one, _clone_named(one, "PROVIDER_ONE")])


def _clone_named(cls: type[BaseLeadSource], name: str) -> type[BaseLeadSource]:
    return type("Clone", (cls,), {"name": name})


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_a_class_reexported_into_another_module_is_registered_once(
    make_package: MakePackage,
) -> None:
    pkg = make_package(
        {
            "a.py": module(adapter("AlphaSource", "alpha")),
            "b.py": "from .a import AlphaSource\n",
        }
    )

    assert SourceRegistry.discover(pkg).names() == ("alpha",)


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_scanning_twice_is_repeatable_and_keeps_no_global_state(
    make_package: MakePackage,
) -> None:
    pkg = make_package({"a.py": module(adapter("AlphaSource", "alpha"))})

    first = SourceRegistry.discover(pkg)
    second = SourceRegistry.discover(pkg)
    other = SourceRegistry.discover(
        make_package({"z.py": module(adapter("ZSource", "z"))})
    )

    assert first.names() == second.names() == ("alpha",)
    assert first.source_class("alpha") is second.source_class("alpha")
    assert other.names() == ("z",)


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_order_does_not_depend_on_file_creation_or_definition_order(
    make_package: MakePackage,
) -> None:
    pkg = make_package(
        {
            "z.py": module(adapter("ZSource", "zulu"), adapter("MSource", "mike")),
            "a.py": module(adapter("ASource", "alpha")),
        }
    )

    assert SourceRegistry.discover(pkg).names() == ("alpha", "mike", "zulu")


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_discovery_never_instantiates_an_adapter(make_package: MakePackage) -> None:
    exploding = """
class BoomSource(_Declared):
    name = "boom"

    def __init__(self, *args, **kwargs):
        raise AssertionError("constructed during discovery")

    async def fetch_raw(self, request):
        raise NotImplementedError

    def normalize(self, raw):
        return []
"""
    pkg = make_package({"boom.py": module(exploding)})

    assert SourceRegistry.discover(pkg).names() == ("boom",)


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_an_import_error_in_one_module_fails_startup_naming_it(
    make_package: MakePackage,
) -> None:
    pkg = make_package(
        {
            "good.py": module(adapter("GoodSource", "good")),
            "bad.py": "import module_that_does_not_exist\n",
        }
    )

    with pytest.raises(SourceDiscoveryError) as exc:
        SourceRegistry.discover(pkg)

    assert exc.value.module == f"{pkg}.bad"
    assert isinstance(exc.value.__cause__, ModuleNotFoundError)


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_a_module_that_raises_at_import_time_fails_startup(
    make_package: MakePackage,
) -> None:
    pkg = make_package({"bad.py": "raise RuntimeError('side effect')\n"})

    with pytest.raises(SourceDiscoveryError) as exc:
        SourceRegistry.discover(pkg)

    assert isinstance(exc.value.__cause__, RuntimeError)


# Verifies: specs/lead-source-adapters/requirements.md#3.1
@pytest.mark.parametrize("bad_name", ["", "   ", 7, None])
def test_a_concrete_adapter_without_a_usable_name_fails_startup(
    make_package: MakePackage, bad_name: object
) -> None:
    pkg = make_package({"a.py": module(adapter("NamelessSource", bad_name))})

    with pytest.raises(SourceDiscoveryError) as exc:
        SourceRegistry.discover(pkg)

    assert exc.value.module == f"{pkg}.a"
    assert "NamelessSource" in str(exc.value)


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_a_concrete_adapter_that_never_declares_a_name_fails_startup(
    make_package: MakePackage,
) -> None:
    unnamed = """
class UnnamedSource(_Declared):
    async def fetch_raw(self, request):
        raise NotImplementedError

    def normalize(self, raw):
        return []
"""
    pkg = make_package({"a.py": module(unnamed)})

    with pytest.raises(SourceDiscoveryError, match="UnnamedSource"):
        SourceRegistry.discover(pkg)


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_an_incomplete_adapter_that_declares_a_name_is_not_silently_skipped(
    make_package: MakePackage,
) -> None:
    incomplete = """
class HalfSource(_Declared):
    name = "half"
"""
    pkg = make_package({"a.py": module(incomplete)})

    with pytest.raises(SourceDiscoveryError, match=r"HalfSource.*fetch_raw"):
        SourceRegistry.discover(pkg)


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_a_non_package_target_fails_startup_with_a_named_error(
    make_package: MakePackage,
) -> None:
    pkg = make_package({"plain.py": module(adapter("PlainSource", "plain"))})

    with pytest.raises(SourceDiscoveryError):
        SourceRegistry.discover(f"{pkg}.plain")
    with pytest.raises(SourceDiscoveryError):
        SourceRegistry.discover("no_such_adapter_package_anywhere")


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_a_module_object_may_be_passed_instead_of_a_name(
    make_package: MakePackage,
) -> None:
    pkg = make_package({"a.py": module(adapter("AlphaSource", "alpha"))})
    package_module: ModuleType = __import__(pkg)

    assert SourceRegistry.discover(package_module).names() == ("alpha",)


# Verifies: specs/lead-source-adapters/requirements.md#3.1
def test_the_real_adapter_package_exists_and_is_scannable() -> None:
    assert real_adapters.__name__ == ADAPTER_PACKAGE

    registry = SourceRegistry.discover()

    assert all(
        issubclass(registry.source_class(n), BaseLeadSource) for n in registry.names()
    )


# Verifies: specs/lead-source-adapters/requirements.md#3.3
def test_a_source_absent_from_configuration_is_enabled_at_lowest_trust_rank(
    make_package: MakePackage,
) -> None:
    pkg = make_package({"a.py": module(adapter("AlphaSource", "alpha"))})

    settings = SourceRegistry.discover(pkg, config={}).settings("alpha")

    assert settings == SourceSettings(enabled=True, trust_rank=LOWEST_TRUST_RANK)
    assert SourceRegistry.discover(pkg).settings("alpha") == settings


# Verifies: specs/lead-source-adapters/requirements.md#3.3
def test_configured_settings_apply_only_to_the_named_source(
    make_package: MakePackage,
) -> None:
    pkg = make_package(
        {
            "a.py": module(adapter("AlphaSource", "alpha")),
            "b.py": module(adapter("BetaSource", "beta")),
        }
    )
    config = {"alpha": SourceSettings(enabled=False, trust_rank=5)}

    registry = SourceRegistry.discover(pkg, config=config)

    assert registry.settings("alpha") == SourceSettings(False, 5)
    assert registry.settings("beta") == SourceSettings(True, LOWEST_TRUST_RANK)


# Verifies: specs/lead-source-adapters/requirements.md#3.3
def test_configuration_for_an_unregistered_source_is_reported_not_fatal(
    make_package: MakePackage,
) -> None:
    pkg = make_package({"a.py": module(adapter("AlphaSource", "alpha"))})
    config = {"alpha": SourceSettings(), "deferred": SourceSettings(enabled=False)}

    registry = SourceRegistry.discover(pkg, config=config)

    assert registry.unknown_config_names == ("deferred",)


# Verifies: specs/lead-source-adapters/requirements.md#3.3
def test_the_registry_does_not_alias_the_callers_config(
    make_package: MakePackage,
) -> None:
    pkg = make_package({"a.py": module(adapter("AlphaSource", "alpha"))})
    config = {"alpha": SourceSettings(trust_rank=3)}
    registry = SourceRegistry.discover(pkg, config=config)

    config["alpha"] = SourceSettings(trust_rank=9)

    assert registry.settings("alpha").trust_rank == 3


# Verifies: specs/lead-source-adapters/requirements.md#3.3
@pytest.mark.parametrize("rank", [-1, True, 1.5, "high", None])
def test_trust_rank_must_be_a_non_negative_int(rank: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        SourceSettings(trust_rank=rank)  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#3.3
@pytest.mark.parametrize("enabled", [0, 1, "yes", None])
def test_enabled_must_be_a_real_bool(enabled: object) -> None:
    with pytest.raises(TypeError):
        SourceSettings(enabled=enabled)  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#3.3
def test_looking_up_an_unregistered_source_raises_key_error(
    make_package: MakePackage,
) -> None:
    registry = SourceRegistry.discover(
        make_package({"a.py": module(adapter("AlphaSource", "alpha"))})
    )

    with pytest.raises(KeyError):
        registry.source_class("missing")
    with pytest.raises(KeyError):
        registry.settings("missing")
