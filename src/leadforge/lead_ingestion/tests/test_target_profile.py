"""Target Profile configuration (task 9.1, Requirement 23.1-23.3).

Only synthetic placeholder names appear here: the profile is configuration, and no
test may assume a particular vendor or technology (23.1).
"""

import copy
import itertools
import pickle
import sys
import textwrap
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

import pytest
import yaml

from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.target_profile import (
    TargetProfile,
    check_against_registry,
    effective_vocabulary,
)

PROFILE = """
technologies:
  tech_alpha:
    provider_one: [uid_1, uid_2]
    provider_two: ["phrase one", "phrase two"]
  tech_beta:
    provider_one: uid_3
competitors:
  rival_one:
    provider_two: ["rival one"]
keyword_templates:
  - "{term} alternative"
  - "switching from {term}"
"""

Write = Callable[[str], Path]


def load_target_profile(path: Path) -> TargetProfile:
    """Test-only: a profile from YAML text (production builds them from the catalog)."""
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return TargetProfile(
        technologies=document.get("technologies") or {},
        competitors=document.get("competitors") or {},
        keyword_templates=tuple(document.get("keyword_templates") or ()),
    )


@pytest.fixture
def write(tmp_path: Path) -> Write:
    counter = itertools.count()

    def _write(text: str, name: str = "target_profile.yaml") -> Path:
        path = tmp_path / f"{next(counter)}_{name}"
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return path

    return _write


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_reads_technologies_competitors_and_keyword_templates(write: Write) -> None:
    profile = load_target_profile(write(PROFILE))

    assert isinstance(profile, TargetProfile)
    assert tuple(profile.technologies) == ("tech_alpha", "tech_beta")
    assert tuple(profile.competitors) == ("rival_one",)
    assert profile.terms() == ("tech_alpha", "tech_beta", "rival_one")
    assert profile.keyword_templates == ("{term} alternative", "switching from {term}")


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_one_term_carries_every_providers_vocabulary_in_one_entry(
    write: Write,
) -> None:
    profile = load_target_profile(write(PROFILE))

    assert profile.vocabulary("provider_one", "tech_alpha") == ("uid_1", "uid_2")
    assert profile.vocabulary("provider_two", "tech_alpha") == (
        "phrase one",
        "phrase two",
    )
    assert profile.vocabulary("provider_one", "tech_beta") == "uid_3"
    assert profile.providers() == ("provider_one", "provider_two")


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_vocabulary_for_a_provider_lists_only_terms_it_can_express(
    write: Write,
) -> None:
    profile = load_target_profile(write(PROFILE))

    assert dict(profile.vocabulary_for("provider_two")) == {
        "tech_alpha": ("phrase one", "phrase two"),
        "rival_one": ("rival one",),
    }
    # A term with no column for a source is absent (Not Applicable), not "no match".
    assert profile.vocabulary("provider_two", "tech_beta") is None
    assert profile.vocabulary("provider_unknown", "tech_alpha") is None
    assert dict(profile.vocabulary_for("provider_unknown")) == {}


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_empty_vocabularies_are_not_applicable_but_zero_and_false_are_identifiers(
    write: Write,
) -> None:
    profile = load_target_profile(
        write(
            """
            technologies:
              tech_alpha:
                provider_one: []
                provider_two: "   "
                provider_three: ~
                provider_four: 0
                provider_five: false
            """
        )
    )

    for empty in ("provider_one", "provider_two", "provider_three"):
        assert profile.vocabulary(empty, "tech_alpha") is None
        assert dict(profile.vocabulary_for(empty)) == {}
    assert profile.vocabulary("provider_four", "tech_alpha") == 0
    assert profile.vocabulary("provider_five", "tech_alpha") is False


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_loaded_profile_cannot_be_mutated(write: Write) -> None:
    profile = load_target_profile(write(PROFILE))

    with pytest.raises(TypeError):
        profile.technologies["tech_new"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        profile.technologies["tech_alpha"]["provider_one"] = "x"  # type: ignore[index]
    vocab = profile.vocabulary("provider_one", "tech_alpha")
    assert isinstance(vocab, tuple)  # lists are frozen to tuples
    with pytest.raises(TypeError):
        profile.vocabulary_for("provider_one")["tech_alpha"] = "x"  # type: ignore[index]


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_nested_mapping_vocabulary_is_frozen_too(write: Write) -> None:
    profile = load_target_profile(
        write(
            """
            technologies:
              tech_alpha:
                provider_one: {ids: [1, 2], mode: any}
            """
        )
    )

    vocab = profile.vocabulary("provider_one", "tech_alpha")
    assert isinstance(vocab, Mapping)
    assert not isinstance(vocab, dict)
    with pytest.raises(TypeError):
        vocab["mode"] = "all"  # type: ignore[index]
    assert vocab["ids"] == (1, 2)


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_rereading_the_file_after_it_changes_does_not_alter_a_loaded_profile(
    write: Write,
) -> None:
    path = write(PROFILE)
    profile = load_target_profile(path)
    path.write_text("technologies:\n  other_term:\n    provider_one: x\n")

    assert "other_term" not in profile.terms()
    assert load_target_profile(path).terms() == ("other_term",)


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_unicode_terms_and_phrases_round_trip(write: Write) -> None:
    profile = load_target_profile(
        write(
            """
            technologies:
              technologie_é:
                provider_one: ["café noir", "日本語"]
            """
        )
    )

    assert profile.vocabulary("provider_one", "technologie_é") == (
        "café noir",
        "日本語",
    )


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_keyword_templates_substitute_only_the_term_placeholder(write: Write) -> None:
    profile = load_target_profile(write(PROFILE))

    assert profile.render_keywords("tech_alpha") == (
        "tech_alpha alternative",
        "switching from tech_alpha",
    )
    assert profile.render_keywords("rival_one")[0] == "rival_one alternative"
    with pytest.raises(KeyError):
        profile.render_keywords("not_a_term")


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_keyword_templates_with_other_braces_are_left_untouched(write: Write) -> None:
    profile = load_target_profile(
        write(
            """
            technologies:
              tech_alpha: {}
            keyword_templates: ["{term} {other} {0} {{term}}"]
            """
        )
    )

    assert profile.render_keywords("tech_alpha") == (
        "tech_alpha {other} {0} {tech_alpha}",
    )


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_a_profile_without_templates_or_competitors_is_valid(write: Write) -> None:
    profile = load_target_profile(write("technologies:\n  tech_alpha: {}\n"))

    assert profile.competitors == {}
    assert profile.keyword_templates == ()
    assert profile.render_keywords("tech_alpha") == ()


def test_configuration_error_round_trips_through_pickle_and_copy() -> None:
    error = ConfigurationError("config/x.yaml", key_path="a.b", detail="bad")

    for clone in (pickle.loads(pickle.dumps(error)), copy.copy(error)):
        assert isinstance(clone, ConfigurationError)
        assert str(clone) == str(error)
        assert clone.key_path == "a.b"
    assert "key=<document>" in str(ConfigurationError("p", key_path="", detail="d"))


# ---- registry interplay: unregistered columns warn; contradictions fail ----------

HEADER = """
from collections.abc import Mapping
from typing import ClassVar

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource, Capability, ChargeUnit, CostClass, Endpoint, RateBucket,
)


class {cls}(BaseLeadSource):
    name = {name!r}
    capabilities: ClassVar[frozenset[Capability]] = frozenset()
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {{}}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {surfaces!r}
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {vocab!r}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {{}}
    required_env: ClassVar[tuple[str, ...]] = ()

    async def fetch_raw(self, request):
        raise NotImplementedError

    def normalize(self, raw):
        return []
"""

_counter = itertools.count()
MakePackage = Callable[[dict[str, str]], str]


def source_module(
    cls: str,
    name: str,
    vocab: dict[str, object] | None = None,
    surfaces: dict[str, frozenset[str]] | None = None,
) -> str:
    return HEADER.format(cls=cls, name=name, vocab=vocab or {}, surfaces=surfaces or {})


@pytest.fixture
def make_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MakePackage]:
    created: list[str] = []
    monkeypatch.syspath_prepend(str(tmp_path))

    def build(files: dict[str, str]) -> str:
        pkg = f"_profile_adapters_under_test_{next(_counter)}"
        created.append(pkg)
        root = tmp_path / pkg
        root.mkdir()
        (root / "__init__.py").write_text("")
        for rel, source in files.items():
            (root / rel).write_text(source)
        return pkg

    yield build
    for key in [k for k in sys.modules if k.split(".")[0] in created]:
        del sys.modules[key]


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_columns_for_unregistered_sources_are_returned_as_warnings_not_errors(
    write: Write, make_package: MakePackage
) -> None:
    path = write(PROFILE)
    pkg = make_package({"one.py": source_module("One", "provider_one")})
    registry = SourceRegistry.discover(pkg)

    unregistered = check_against_registry(
        load_target_profile(path), registry, path=path
    )

    assert unregistered == ("provider_two",)
    assert load_target_profile(path).unregistered_providers(["provider_one"]) == (
        "provider_two",
    )


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_a_column_may_make_a_term_expressible_that_the_adapter_never_declared(
    write: Write, make_package: MakePackage
) -> None:
    path = write(PROFILE)
    pkg = make_package({"one.py": source_module("One", "provider_one")})
    registry = SourceRegistry.discover(pkg)
    profile = load_target_profile(path)

    assert check_against_registry(profile, registry, path=path) == ("provider_two",)
    effective = effective_vocabulary(profile, registry.source_class("provider_one"))
    assert dict(effective) == {
        "tech_alpha": ("uid_1", "uid_2"),
        "tech_beta": "uid_3",
    }


# Verifies: specs/user-recognition/requirements.md#2.7
def test_the_adapter_default_no_longer_fills_a_term_the_configuration_does_not_cover(
    write: Write, make_package: MakePackage
) -> None:
    path = write(PROFILE)
    pkg = make_package(
        {
            "one.py": source_module(
                "One",
                "provider_two",
                {"rival_one": ["default rival"], "tech_beta": "default_beta"},
                {
                    "target_profile.rival_one": frozenset({"raw.a"}),
                    "target_profile.tech_beta": frozenset({"raw.b"}),
                },
            )
        }
    )
    registry = SourceRegistry.discover(pkg)
    profile = load_target_profile(path)

    effective = effective_vocabulary(profile, registry.source_class("provider_two"))

    # tech_beta has no provider_two column: the profile is the only vocabulary (2.7),
    # so the adapter default is inert and the term is Not Applicable.
    assert "tech_beta" not in effective
    assert effective["rival_one"] == ("rival one",)
    assert effective["tech_alpha"] == ("phrase one", "phrase two")
    assert check_against_registry(profile, registry, path=path) == ("provider_one",)


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_configuration_replaces_a_default_mapping_without_deep_merging(
    write: Write, make_package: MakePackage
) -> None:
    path = write("technologies:\n  tech_alpha:\n    provider_one: {b: 2}\n")
    pkg = make_package(
        {
            "one.py": source_module(
                "One",
                "provider_one",
                {"tech_alpha": {"a": 1, "b": 1}},
                {"target_profile.tech_alpha": frozenset({"raw.a"})},
            )
        }
    )
    registry = SourceRegistry.discover(pkg)

    effective = effective_vocabulary(
        load_target_profile(path), registry.source_class("provider_one")
    )

    assert dict(effective["tech_alpha"]) == {"b": 2}  # type: ignore[call-overload]


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_an_adapter_default_for_a_term_outside_the_profile_is_inert(
    write: Write, make_package: MakePackage
) -> None:
    path = write("technologies:\n  tech_alpha:\n    provider_one: [uid_1]\n")
    pkg = make_package(
        {
            "one.py": source_module(
                "One",
                "provider_one",
                {"stale_term": ["old"]},
                {"target_profile.stale_term": frozenset({"raw.a"})},
            )
        }
    )
    registry = SourceRegistry.discover(pkg)
    profile = load_target_profile(path)

    effective = effective_vocabulary(profile, registry.source_class("provider_one"))

    assert dict(effective) == {"tech_alpha": ("uid_1",)}
    assert check_against_registry(profile, registry, path=path) == ()


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_an_explicit_empty_column_blanks_a_default_that_has_no_surface_dependency(
    write: Write, make_package: MakePackage
) -> None:
    # An empty column is the explicit "no surface" of the configuration. A source whose
    # adapter default is a vocabulary must declare the matching surface (3.3), so a
    # blank-out contradicts it: that is a named startup error, never a silent N/A.
    path = write("technologies:\n  tech_alpha:\n    provider_one: []\n")
    pkg = make_package(
        {
            "one.py": source_module(
                "One",
                "provider_one",
                {"tech_alpha": ["secret_default_id"]},
                {"target_profile.tech_alpha": frozenset({"raw.a"})},
            )
        }
    )
    registry = SourceRegistry.discover(pkg)
    profile = load_target_profile(path)

    assert "tech_alpha" not in effective_vocabulary(
        profile, registry.source_class("provider_one")
    )
    with pytest.raises(ConfigurationError) as caught:
        check_against_registry(profile, registry, path=path)

    assert caught.value.key_path == "technologies.tech_alpha.provider_one"
    assert str(path) in str(caught.value)
    assert "secret_default_id" not in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_an_empty_column_for_a_term_the_adapter_never_declared_is_consistent(
    write: Write, make_package: MakePackage
) -> None:
    path = write("technologies:\n  tech_alpha:\n    provider_one: []\n")
    pkg = make_package({"one.py": source_module("One", "provider_one")})
    registry = SourceRegistry.discover(pkg)

    assert check_against_registry(load_target_profile(path), registry, path=path) == ()
    assert (
        dict(
            effective_vocabulary(
                load_target_profile(path), registry.source_class("provider_one")
            )
        )
        == {}
    )


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_target_term_absence_follows_the_effective_vocabulary(
    write: Write, make_package: MakePackage
) -> None:
    path = write(PROFILE)
    pkg = make_package({"one.py": source_module("One", "provider_one")})
    registry = SourceRegistry.discover(pkg)
    cls = registry.source_class("provider_one")
    source = cls(DataMode.SYNTHETIC)
    effective = effective_vocabulary(load_target_profile(path), cls)

    # The adapter alone declares nothing, so without the merge it is Not Applicable.
    assert source.target_term_absence("tech_alpha") is not None
    assert source.target_term_absence("tech_alpha", vocabulary=effective) is None
    assert source.target_term_absence("rival_one", vocabulary=effective) is not None


# ---- plug-and-play: one file per term, one module plus one column per source -----


# Verifies: specs/lead-source-adapters/requirements.md#23.3
def test_adding_a_term_edits_one_file_and_retargets_every_source(
    tmp_path: Path, make_package: MakePackage
) -> None:
    pkg = make_package(
        {
            "one.py": source_module("One", "provider_one"),
            "two.py": source_module("Two", "provider_two"),
        }
    )
    pkg_dir = tmp_path / pkg
    code_before = {p.name: p.read_text() for p in pkg_dir.glob("*.py")}
    config = tmp_path / "target_profile.yaml"
    config.write_text(PROFILE)
    before = load_target_profile(config)

    # The only edit: one more term in the one configuration file.
    config.write_text(
        PROFILE.replace(
            "competitors:",
            "  tech_gamma:\n    provider_one: uid_9\n    provider_two: [gamma]\n"
            "competitors:",
        )
    )
    after = load_target_profile(config)

    assert {p.name: p.read_text() for p in pkg_dir.glob("*.py")} == code_before
    assert "tech_gamma" not in before.terms()
    assert "tech_gamma" in after.terms()
    assert after.vocabulary("provider_one", "tech_gamma") == "uid_9"
    assert after.vocabulary("provider_two", "tech_gamma") == ("gamma",)
    registry = SourceRegistry.discover(pkg)
    assert check_against_registry(after, registry, path=config) == ()


# Verifies: specs/lead-source-adapters/requirements.md#23.3
def test_adding_a_source_is_one_module_plus_one_column_in_the_same_file(
    tmp_path: Path, make_package: MakePackage
) -> None:
    config = tmp_path / "target_profile.yaml"
    config.write_text(PROFILE)
    profile_before = config.read_text()
    pkg = make_package({"one.py": source_module("One", "provider_one")})
    assert load_target_profile(config).unregistered_providers(
        SourceRegistry.discover(pkg).names()
    ) == ("provider_two",)

    # The only edits: one new adapter module, and its column in the same file.
    (tmp_path / pkg / "two.py").write_text(source_module("Two", "provider_two"))
    registry = SourceRegistry.discover(pkg)
    profile = load_target_profile(config)

    assert config.read_text() == profile_before  # no config edit was needed here
    assert registry.names() == ("provider_one", "provider_two")
    assert profile.unregistered_providers(registry.names()) == ()
    assert check_against_registry(profile, registry, path=config) == ()

    # Third source: a module plus a column; no existing line of the file changes.
    (tmp_path / pkg / "three.py").write_text(source_module("Three", "provider_three"))
    config.write_text(
        PROFILE.replace(
            "    provider_one: uid_3\n",
            "    provider_one: uid_3\n    provider_three: [p3_tech_beta]\n",
        )
    )
    registry = SourceRegistry.discover(pkg)
    profile = load_target_profile(config)
    assert registry.names() == ("provider_one", "provider_three", "provider_two")
    assert profile.vocabulary("provider_three", "tech_beta") == ("p3_tech_beta",)
    assert profile.unregistered_providers(registry.names()) == ()


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_alias_bomb_is_frozen_in_linear_time(write: Write) -> None:
    levels = ["l0: &l0 [x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 12):
        previous = f"*l{i - 1}"
        levels.append(f"l{i}: &l{i} [{', '.join([previous] * 9)}]")
    body = "\n".join(f"      {line}" for line in levels)
    text = f"technologies:\n  tech_alpha:\n    provider_one:\n      ids:\n{body}\n"
    # Re-nest under one mapping so every level is reachable from the vocabulary.
    text = text.replace("      ids:\n", "")

    profile = load_target_profile(write(text))

    assert profile.vocabulary("provider_one", "tech_alpha") is not None
