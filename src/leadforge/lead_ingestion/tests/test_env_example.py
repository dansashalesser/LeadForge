"""Generated credential example file (task 8.3, requirements 10.1 to 10.4).

The committed ``.env.example`` is generated from the registry, so an adapter that reads
an undocumented variable fails the lockfile-style test below. Test adapters live in
throwaway packages under ``tmp_path``, as in the registry tests.
"""

import itertools
import re
import sys
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from leadforge.lead_ingestion import env_example
from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    ChargeUnit,
    CostClass,
    RateBucket,
    RateWindow,
)
from leadforge.lead_ingestion.env_example import (
    ManifestError,
    main,
    render_env_example,
    write_env_example,
)
from leadforge.lead_ingestion.registry import SourceRegistry

REPO_ROOT = Path(__file__).resolve().parents[4]
COMMITTED = REPO_ROOT / ".env.example"
REGENERATE = "uv run python -m leadforge.lead_ingestion.env_example"

# Google Custom Search variables dropped by design correction N3 (no code path may call
# that API); they must never come back through the manifest.
RETIRED = ("GOOGLE_CSE_API_KEY", "GOOGLE_CSE_CX")

SECRET = "sk-live-SUPERSECRET-0123456789"

HEADER = """
from collections.abc import Mapping
from typing import ClassVar

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource, Capability, ChargeUnit, CostClass, Endpoint, RateBucket,
    RateWindow,
)


class _Declared(BaseLeadSource):
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


def adapter(cls_name: str, name: str, env: tuple[str, ...], body: str = "") -> str:
    return f"""
class {cls_name}(_Declared):
    name = {name!r}
    required_env = {env!r}
{textwrap.indent(textwrap.dedent(body), "    ")}
    async def fetch_raw(self, request):
        raise NotImplementedError

    def normalize(self, raw):
        return []
"""


_counter = itertools.count()
MakePackage = Callable[[dict[str, str]], str]


@pytest.fixture
def make_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MakePackage]:
    created: list[str] = []
    monkeypatch.syspath_prepend(str(tmp_path))

    def build(files: dict[str, str]) -> str:
        pkg = f"_env_example_pkg_{next(_counter)}"
        created.append(pkg)
        root = tmp_path / pkg
        root.mkdir()
        (root / "__init__.py").write_text("")
        for rel, source in files.items():
            (root / rel).write_text(HEADER + source)
        return pkg

    yield build
    for key in [k for k in sys.modules if k.split(".")[0] in created]:
        del sys.modules[key]


def _cls(name: str, env: tuple[str, ...], **attrs: object) -> type[BaseLeadSource]:
    ns: dict[str, object] = {
        "name": name,
        "required_env": env,
        "capabilities": frozenset(),
        "rate_limit": {},
        "answerable_surfaces": {},
        "cost_class": CostClass.FREE,
        "charge_unit": ChargeUnit.PER_CALL,
        "yields_suppression": False,
        "target_vocabulary": {},
        "endpoints": {},
        "fetch_raw": lambda self, request: None,
        "normalize": lambda self, raw: [],
        **attrs,
    }
    return type(f"{name.title()}Source", (BaseLeadSource,), ns)


def _entries(text: str) -> dict[str, str]:
    """``{VARIABLE: value}`` for every assignment line."""
    return dict(
        line.split("=", 1)
        for line in text.splitlines()
        if re.match(r"^[A-Z_0-9]+=", line)
    )


def _block(text: str, variable: str) -> list[str]:
    """The comment lines directly above ``variable``'s assignment."""
    lines = text.splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith(f"{variable}="))
    out: list[str] = []
    for ln in reversed(lines[:at]):
        if not ln.startswith("#"):
            break
        out.append(ln)
    return list(reversed(out))


# --- built-in settings -------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#10.1
def test_empty_registry_lists_database_and_llm_settings_only() -> None:
    text = render_env_example(SourceRegistry())
    assert list(_entries(text)) == ["DATABASE_URL", "LLM_PROVIDER", "LLM_MODEL"]


# Verifies: specs/lead-source-adapters/requirements.md#10.2
def test_every_entry_has_empty_value_and_documentation_url_comment() -> None:
    text = render_env_example(SourceRegistry())
    for variable, value in _entries(text).items():
        assert value == ""
        assert any(re.search(r"https://\S+", ln) for ln in _block(text, variable)), (
            variable
        )


# --- adapter variables -------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#10.1
def test_union_of_adapter_variables_is_listed_sorted_after_builtins() -> None:
    registry = SourceRegistry(
        [
            _cls("zeta", ("ZETA_API_KEY",), docs_url="https://docs.example.invalid/z"),
            _cls(
                "alpha", ("ALPHA_API_KEY",), docs_url="https://docs.example.invalid/a"
            ),
        ]
    )
    names = list(_entries(render_env_example(registry)))
    assert names == [
        "DATABASE_URL",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "ALPHA_API_KEY",
        "ZETA_API_KEY",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#10.3
def test_multi_credential_provider_lists_each_variable_on_its_own_line() -> None:
    registry = SourceRegistry(
        [
            _cls(
                "provider_four",
                ("PROVIDER_FOUR_CLIENT_ID", "PROVIDER_FOUR_CLIENT_SECRET"),
                docs_url="https://docs.example.invalid/zi",
            )
        ]
    )
    text = render_env_example(registry)
    entries = _entries(text)
    assert entries["PROVIDER_FOUR_CLIENT_ID"] == ""
    assert entries["PROVIDER_FOUR_CLIENT_SECRET"] == ""
    for variable in ("PROVIDER_FOUR_CLIENT_ID", "PROVIDER_FOUR_CLIENT_SECRET"):
        assert "https://docs.example.invalid/zi" in "\n".join(_block(text, variable))


# Verifies: specs/lead-source-adapters/requirements.md#10.2
def test_documentation_url_falls_back_to_first_rate_bucket_url() -> None:
    def bucket(url: str) -> RateBucket:
        return RateBucket("b", (RateWindow(1, 1.0),), True, url)

    registry = SourceRegistry(
        [
            _cls(
                "provider_one",
                ("PROVIDER_ONE_API_KEY",),
                rate_limit={
                    "z": bucket("https://docs.example.invalid/z-limit"),
                    "a": bucket("https://docs.example.invalid/a-limit"),
                },
            )
        ]
    )
    block = "\n".join(_block(render_env_example(registry), "PROVIDER_ONE_API_KEY"))
    assert "https://docs.example.invalid/a-limit" in block


def test_explicit_docs_url_beats_rate_bucket_url() -> None:
    bucket = RateBucket("b", (RateWindow(1, 1.0),), True, "https://x.invalid/limit")
    registry = SourceRegistry(
        [
            _cls(
                "provider_one",
                ("PROVIDER_ONE_API_KEY",),
                docs_url="https://x.invalid/docs",
                rate_limit={"b": bucket},
            )
        ]
    )
    block = "\n".join(_block(render_env_example(registry), "PROVIDER_ONE_API_KEY"))
    assert "https://x.invalid/docs" in block
    assert "https://x.invalid/limit" not in block


def test_adapter_with_variables_but_no_documentation_url_fails_loudly() -> None:
    registry = SourceRegistry([_cls("provider_one", ("PROVIDER_ONE_API_KEY",))])
    with pytest.raises(ManifestError, match="provider_one"):
        render_env_example(registry)


def test_keyless_adapter_needs_no_documentation_url_and_adds_no_entry() -> None:
    registry = SourceRegistry([_cls("local", ())])
    assert list(_entries(render_env_example(registry))) == [
        "DATABASE_URL",
        "LLM_PROVIDER",
        "LLM_MODEL",
    ]


def test_variable_shared_by_two_adapters_appears_once_naming_both() -> None:
    registry = SourceRegistry(
        [
            _cls("one", ("SHARED_KEY",), docs_url="https://one.invalid/docs"),
            _cls("two", ("SHARED_KEY",), docs_url="https://two.invalid/docs"),
        ]
    )
    text = render_env_example(registry)
    assert text.count("\nSHARED_KEY=") == 1
    block = "\n".join(_block(text, "SHARED_KEY"))
    assert "one docs: https://one.invalid/docs" in block
    assert "two docs: https://two.invalid/docs" in block


def test_adapter_redeclaring_a_builtin_setting_is_merged_not_duplicated() -> None:
    registry = SourceRegistry(
        [_cls("one", ("DATABASE_URL",), docs_url="https://one.invalid/docs")]
    )
    text = render_env_example(registry)
    assert text.count("\nDATABASE_URL=") == 1
    assert "one" in "\n".join(_block(text, "DATABASE_URL"))


# --- retired Google Custom Search variables ----------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#14.2
@pytest.mark.parametrize("name", RETIRED)
def test_retired_search_variables_never_appear_in_output_or_committed_file(
    name: str,
) -> None:
    assert name not in render_env_example(SourceRegistry.discover())
    assert name not in COMMITTED.read_text()


def test_adapter_declaring_a_retired_variable_is_rejected() -> None:
    registry = SourceRegistry(
        [_cls("g", (RETIRED[0],), docs_url="https://g.invalid/docs")]
    )
    with pytest.raises(ManifestError, match=RETIRED[0]):
        render_env_example(registry)


# --- determinism, bytes, secrets, injection ----------------------------------------


def test_output_is_deterministic_and_independent_of_registration_order() -> None:
    a = _cls("a", ("A_KEY",), docs_url="https://a.invalid/d")
    b = _cls("b", ("B_KEY",), docs_url="https://b.invalid/d")
    assert render_env_example(SourceRegistry([a, b])) == render_env_example(
        SourceRegistry([b, a])
    )


def test_output_uses_lf_only_and_ends_with_exactly_one_newline() -> None:
    text = render_env_example(SourceRegistry())
    assert "\r" not in text
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_process_environment_values_never_reach_the_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("DATABASE_URL", "LLM_PROVIDER", "LLM_MODEL", "A_KEY"):
        monkeypatch.setenv(name, SECRET)
    registry = SourceRegistry([_cls("a", ("A_KEY",), docs_url="https://a.invalid/d")])
    assert SECRET not in render_env_example(registry)


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://a.invalid/d\nINJECTED=1",
        "https://a.invalid/d x",
        "javascript:alert(1)",
        "ftp://a.invalid/d",
        "",
        "https://a.invalid/\u2028INJECTED=1",
    ],
)
def test_malformed_documentation_url_is_rejected(bad_url: object) -> None:
    registry = SourceRegistry([_cls("a", ("A_KEY",), docs_url=bad_url)])
    with pytest.raises(ManifestError):
        render_env_example(registry)


@pytest.mark.parametrize("bad_name", ["a\nINJECTED=1", "a\x07b", "a\u2028b"])
def test_source_name_with_control_characters_is_rejected(bad_name: str) -> None:
    registry = SourceRegistry(
        [_cls(bad_name, ("A_KEY",), docs_url="https://a.invalid/d")]
    )
    with pytest.raises(ManifestError):
        render_env_example(registry)


@pytest.mark.parametrize("bad_var", ["A#B", "A\x07B", "A\x1bB", "a\u200bb"])
def test_odd_variable_name_is_rejected(bad_var: str) -> None:
    registry = SourceRegistry([_cls("a", (bad_var,), docs_url="https://a.invalid/d")])
    with pytest.raises(ManifestError):
        render_env_example(registry)


# --- generation from a discovered package ------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#10.1
def test_discovered_package_adapters_are_included(make_package: MakePackage) -> None:
    pkg = make_package(
        {
            "one.py": adapter(
                "One",
                "one",
                ("ONE_CLIENT_ID", "ONE_CLIENT_SECRET"),
                'docs_url = "https://docs.example.invalid/one"',
            ),
            "two.py": adapter(
                "Two",
                "two",
                ("TWO_API_KEY",),
                'docs_url = "https://docs.example.invalid/two"',
            ),
        }
    )
    text = render_env_example(SourceRegistry.discover(pkg))
    assert list(_entries(text))[3:] == [
        "ONE_CLIENT_ID",
        "ONE_CLIENT_SECRET",
        "TWO_API_KEY",
    ]


# --- the committed file (lockfile-style check) -------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#10.4
def test_committed_example_file_equals_generated_output() -> None:
    generated = render_env_example(SourceRegistry.discover()).encode()
    assert COMMITTED.is_file(), f"missing .env.example; regenerate with: {REGENERATE}"
    assert COMMITTED.read_bytes() == generated, (
        ".env.example is out of date with the adapter registry (an adapter reads or "
        f"no longer reads a variable it does not document). Regenerate with: "
        f"{REGENERATE}"
    )


# Verifies: specs/lead-source-adapters/requirements.md#10.4
def test_every_registered_required_env_appears_in_committed_file() -> None:
    registry = SourceRegistry.discover()
    entries = _entries(COMMITTED.read_text())
    for name in registry.names():
        for variable in registry.source_class(name).required_env:
            assert variable in entries, f"{name} reads undocumented {variable}"


def test_committed_file_holds_no_real_looking_values() -> None:
    assert all(value == "" for value in _entries(COMMITTED.read_text()).values())


# --- regenerate path ---------------------------------------------------------------


def test_write_env_example_writes_the_rendered_text_and_reports_change(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".env.example"
    registry = SourceRegistry()
    assert write_env_example(registry, target) is True
    assert target.read_bytes() == render_env_example(registry).encode()
    assert write_env_example(registry, target) is False


def test_main_writes_the_file_and_check_mode_detects_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / ".env.example"
    assert main(["--path", str(target), "--check"]) == 1
    assert not target.exists()
    assert REGENERATE in capsys.readouterr().err
    assert main(["--path", str(target)]) == 0
    assert main(["--path", str(target), "--check"]) == 0


def test_module_exposes_a_regenerate_command_string() -> None:
    assert env_example.REGENERATE_COMMAND == REGENERATE
