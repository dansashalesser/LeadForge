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

from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.target_profile import (
    DEFAULT_TARGET_PROFILE_PATH,
    TargetProfile,
    check_against_registry,
    load_target_profile,
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

SECRET = "sk_live_NOT_FOR_LOGS_123"

Write = Callable[[str], Path]


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


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_default_path_is_config_target_profile_relative_to_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert Path("config/target_profile.yaml") == DEFAULT_TARGET_PROFILE_PATH
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "target_profile.yaml").write_text(PROFILE)
    monkeypatch.chdir(tmp_path)

    assert load_target_profile().terms()[0] == "tech_alpha"


BAD_PROFILES: list[tuple[str, str, str]] = [
    # (id, yaml text, key path the error must name)
    ("not-a-mapping", "- a\n- b\n", ""),
    ("empty-document", "", ""),
    ("comment-only", "# nothing\n", ""),
    ("unknown-top-level", f"technologes:\n  t: {{}}\nx: {SECRET}\n", "technologes"),
    ("technologies-not-mapping", f"technologies: {SECRET}\n", "technologies"),
    ("no-terms", "technologies: {}\ncompetitors: {}\n", ""),
    ("only-templates", 'keyword_templates: ["{term}"]\n', ""),
    (
        "term-not-mapping",
        f"technologies:\n  tech_alpha: [{SECRET}]\n",
        "technologies.tech_alpha",
    ),
    ("numeric-term-name", f"technologies:\n  1:\n    p: {SECRET}\n", "technologies"),
    ("null-term-name", f"technologies:\n  ~:\n    p: {SECRET}\n", "technologies"),
    ("blank-term-name", f"technologies:\n  ' ':\n    p: {SECRET}\n", "technologies"),
    (
        "padded-term-name",
        f"technologies:\n  ' tech_alpha':\n    p: {SECRET}\n",
        "technologies",
    ),
    (
        "bool-provider-name",
        f"technologies:\n  tech_alpha:\n    yes: {SECRET}\n",
        "technologies.tech_alpha",
    ),
    (
        "blank-provider-name",
        f"technologies:\n  tech_alpha:\n    '': {SECRET}\n",
        "technologies.tech_alpha",
    ),
    (
        "duplicate-term-across-sections",
        f"technologies:\n  t: {{p: {SECRET}}}\ncompetitors:\n  t: {{p: x}}\n",
        "competitors.t",
    ),
    (
        "duplicate-key",
        f"technologies:\n  t:\n    p: {SECRET}\n  t:\n    p: y\n",
        "technologies.t",
    ),
    (
        "duplicate-provider-key",
        f"technologies:\n  t:\n    p: {SECRET}\n    p: y\n",
        "technologies.t.p",
    ),
    (
        "duplicate-top-level",
        f"technologies:\n  t: {{}}\ntechnologies:\n  u: {{p: {SECRET}}}\n",
        "technologies",
    ),
    (
        "templates-not-list",
        f"technologies:\n  t: {{}}\nkeyword_templates: {SECRET}\n",
        "keyword_templates",
    ),
    (
        "template-not-str",
        f"technologies:\n  t: {{}}\nkeyword_templates: [{{k: {SECRET}}}]\n",
        "keyword_templates[0]",
    ),
    (
        "template-blank",
        "technologies:\n  t: {}\nkeyword_templates: ['ok {term}', '  ']\n",
        "keyword_templates[1]",
    ),
    (
        "unsafe-tag",
        "technologies:\n  t:\n    p: !!python/object/apply:os.getcwd []\n",
        "",
    ),
    ("syntax-error", f"technologies: [unclosed {SECRET}\n", ""),
]


# Verifies: specs/lead-source-adapters/requirements.md#23.1
@pytest.mark.parametrize(
    ("text", "key_path"),
    [pytest.param(t, k, id=i) for i, t, k in BAD_PROFILES],
)
def test_invalid_profile_raises_a_named_error_naming_file_and_key_never_the_value(
    write: Write, text: str, key_path: str
) -> None:
    path = write(text)

    with pytest.raises(ConfigurationError) as caught:
        load_target_profile(path)

    err = caught.value
    assert err.path == str(path)
    assert str(path) in str(err)
    if key_path:
        assert err.key_path == key_path
        assert key_path in str(err)
    assert SECRET not in str(err)
    assert SECRET not in repr(err)
    assert SECRET not in "".join(map(str, err.args))
    assert err.__cause__ is None or SECRET not in str(err.__cause__)
    assert err.__suppress_context__ or err.__context__ is None


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_missing_file_and_directory_and_bad_encoding_are_named_errors(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError) as missing:
        load_target_profile(tmp_path / "absent.yaml")
    assert str(tmp_path / "absent.yaml") in str(missing.value)

    with pytest.raises(ConfigurationError):
        load_target_profile(tmp_path)

    bad = tmp_path / "bad.yaml"
    bad.write_bytes(b"technologies:\n  t:\n    p: \xff\xfe\n")
    with pytest.raises(ConfigurationError) as encoding:
        load_target_profile(bad)
    assert str(bad) in str(encoding.value)


def test_configuration_error_round_trips_through_pickle_and_copy() -> None:
    error = ConfigurationError("config/x.yaml", key_path="a.b", detail="bad")

    for clone in (pickle.loads(pickle.dumps(error)), copy.copy(error)):
        assert isinstance(clone, ConfigurationError)
        assert str(clone) == str(error)
        assert clone.key_path == "a.b"
    assert "key=<document>" in str(ConfigurationError("p", key_path="", detail="d"))


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_yaml_tags_cannot_execute_code(write: Write, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    text = (
        "technologies:\n  t:\n    p: !!python/object/apply:pathlib.Path.touch "
        f"[!!python/object/apply:pathlib.Path [{marker}]]\n"
    )

    with pytest.raises(ConfigurationError):
        load_target_profile(write(text))

    assert not marker.exists()


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
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {{}}
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


def source_module(cls: str, name: str, vocab: dict[str, object] | None = None) -> str:
    return HEADER.format(cls=cls, name=name, vocab=vocab or {})


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
def test_a_column_contradicting_a_declared_not_applicable_term_is_an_error(
    write: Write, make_package: MakePackage
) -> None:
    path = write(PROFILE)
    # The adapter declares tech_alpha as Not Applicable, yet the column supplies it.
    pkg = make_package(
        {"one.py": source_module("One", "provider_one", {"tech_alpha": ""})}
    )
    registry = SourceRegistry.discover(pkg)

    with pytest.raises(ConfigurationError) as caught:
        check_against_registry(load_target_profile(path), registry, path=path)

    assert caught.value.key_path == "technologies.tech_alpha.provider_one"
    assert str(path) in str(caught.value)
    assert "uid_1" not in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_an_empty_column_for_a_declared_not_applicable_term_is_consistent(
    write: Write, make_package: MakePackage
) -> None:
    path = write("technologies:\n  tech_alpha:\n    provider_one: []\n")
    pkg = make_package(
        {"one.py": source_module("One", "provider_one", {"tech_alpha": ""})}
    )

    assert (
        check_against_registry(
            load_target_profile(path), SourceRegistry.discover(pkg), path=path
        )
        == ()
    )


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


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_recursive_alias_is_a_named_error(write: Write) -> None:
    path = write("technologies:\n  tech_alpha:\n    provider_one: &a [*a]\n")

    with pytest.raises(ConfigurationError) as caught:
        load_target_profile(path)

    assert caught.value.key_path == "technologies.tech_alpha.provider_one"
    assert str(path) in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#23.1
def test_absurdly_deep_nesting_is_a_named_error_not_a_crash(write: Write) -> None:
    depth = 5000
    path = write(
        "technologies:\n  tech_alpha:\n    provider_one: "
        + "[" * depth
        + "]" * depth
        + "\n"
    )

    with pytest.raises(ConfigurationError) as caught:
        load_target_profile(path)

    assert str(path) in str(caught.value)
