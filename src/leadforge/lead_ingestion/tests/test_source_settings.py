"""Source settings read from ``config/sources.yaml`` (task 9.1; builds 7.1's map)."""

import itertools
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

from leadforge.lead_ingestion.base_source import LiveAccess
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import LOWEST_TRUST_RANK, SourceSettings
from leadforge.lead_ingestion.source_settings import (
    DEFAULT_SOURCES_PATH,
    load_source_settings,
)

SECRET = "sk_live_NOT_FOR_LOGS_123"

Write = Callable[[str], Path]


@pytest.fixture
def write(tmp_path: Path) -> Write:
    counter = itertools.count()

    def _write(text: str) -> Path:
        path = tmp_path / f"{next(counter)}_sources.yaml"
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return path

    return _write


# Verifies: specs/lead-source-adapters/requirements.md#3.3
def test_builds_settings_keyed_by_exact_source_name(write: Write) -> None:
    settings = load_source_settings(
        write(
            """
            sources:
              provider_one:
                enabled: false
                trust_rank: 7
                mode: synthetic
                live_access: gated
              provider_two:
                trust_rank: 2
              provider_three: {}
              provider_four:
            """
        )
    )

    assert settings["provider_one"] == SourceSettings(
        enabled=False,
        trust_rank=7,
        mode=DataMode.SYNTHETIC,
        live_access=LiveAccess.GATED,
    )
    assert settings["provider_two"] == SourceSettings(trust_rank=2)
    assert settings["provider_three"] == SourceSettings()
    assert settings["provider_four"] == SourceSettings()
    assert settings["provider_three"].trust_rank == LOWEST_TRUST_RANK
    assert set(settings) == {
        "provider_one",
        "provider_two",
        "provider_three",
        "provider_four",
    }
    with pytest.raises(TypeError):
        settings["provider_five"] = SourceSettings()  # type: ignore[index]


# Verifies: specs/lead-source-adapters/requirements.md#3.3
def test_missing_file_and_empty_documents_mean_no_configuration(
    write: Write, tmp_path: Path
) -> None:
    assert dict(load_source_settings(tmp_path / "absent.yaml")) == {}
    assert dict(load_source_settings(write(""))) == {}
    assert dict(load_source_settings(write("sources:\n"))) == {}
    assert dict(load_source_settings(write("sources: {}\n"))) == {}


def test_default_path_is_config_sources_relative_to_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert Path("config/sources.yaml") == DEFAULT_SOURCES_PATH
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text(
        "sources:\n  provider_one:\n    trust_rank: 3\n"
    )
    monkeypatch.chdir(tmp_path)

    assert load_source_settings()["provider_one"].trust_rank == 3


BAD: list[tuple[str, str, str]] = [
    ("not-a-mapping", "- a\n", ""),
    ("unknown-top-level", f"sourcez: {{}}\nk: {SECRET}\n", "sourcez"),
    ("sources-not-mapping", f"sources: [{SECRET}]\n", "sources"),
    (
        "entry-not-mapping",
        f"sources:\n  provider_one: {SECRET}\n",
        "sources.provider_one",
    ),
    (
        "unknown-field",
        f"sources:\n  provider_one:\n    enabeld: {SECRET}\n",
        "sources.provider_one.enabeld",
    ),
    (
        "enabled-not-bool",
        f"sources:\n  provider_one:\n    enabled: {SECRET}\n",
        "sources.provider_one.enabled",
    ),
    (
        "enabled-yes-string",
        "sources:\n  provider_one:\n    enabled: 'yes'\n",
        "sources.provider_one.enabled",
    ),
    (
        "rank-str",
        f"sources:\n  provider_one:\n    trust_rank: {SECRET}\n",
        "sources.provider_one.trust_rank",
    ),
    (
        "rank-bool",
        "sources:\n  provider_one:\n    trust_rank: true\n",
        "sources.provider_one.trust_rank",
    ),
    (
        "rank-negative",
        "sources:\n  provider_one:\n    trust_rank: -1\n",
        "sources.provider_one.trust_rank",
    ),
    (
        "rank-float",
        "sources:\n  provider_one:\n    trust_rank: 1.5\n",
        "sources.provider_one.trust_rank",
    ),
    (
        "mode-unknown",
        f"sources:\n  provider_one:\n    mode: {SECRET}\n",
        "sources.provider_one.mode",
    ),
    (
        "mode-not-str",
        f"sources:\n  provider_one:\n    mode: [{SECRET}]\n",
        "sources.provider_one.mode",
    ),
    (
        "live-access-unknown",
        f"sources:\n  provider_one:\n    live_access: {SECRET}\n",
        "sources.provider_one.live_access",
    ),
    ("non-str-name", f"sources:\n  1:\n    enabled: {SECRET}\n", "sources"),
    ("blank-name", f"sources:\n  ' ':\n    enabled: {SECRET}\n", "sources"),
    (
        "duplicate-source",
        "sources:\n  provider_one:\n    enabled: true\n"
        f"  provider_one:\n    k: {SECRET}\n",
        "sources.provider_one",
    ),
    (
        "duplicate-field",
        "sources:\n  provider_one:\n    enabled: true\n    enabled: false\n",
        "sources.provider_one.enabled",
    ),
]


# Verifies: specs/lead-source-adapters/requirements.md#3.3
@pytest.mark.parametrize(
    ("text", "key_path"), [pytest.param(t, k, id=i) for i, t, k in BAD]
)
def test_invalid_settings_raise_a_named_error_naming_file_and_key_never_the_value(
    write: Write, text: str, key_path: str
) -> None:
    path = write(text)

    with pytest.raises(ConfigurationError) as caught:
        load_source_settings(path)

    err = caught.value
    assert err.path == str(path)
    if key_path:
        assert err.key_path == key_path
    assert str(path) in str(err)
    assert SECRET not in str(err)
    assert SECRET not in repr(err)
    assert err.__cause__ is None
    assert err.__suppress_context__ or err.__context__ is None
