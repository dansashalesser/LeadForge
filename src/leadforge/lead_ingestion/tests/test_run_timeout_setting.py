"""``run_timeout_s`` in ``config/sources.yaml`` (task 11.4, 6.6)."""

from collections.abc import Callable
from pathlib import Path

import pytest

from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.registry import SourceSettings
from leadforge.lead_ingestion.source_settings import (
    DEFAULT_RUN_TIMEOUT_S,
    load_run_timeout_s,
    load_source_settings,
)

Write = Callable[[str], Path]


@pytest.fixture
def write(tmp_path: Path) -> Write:
    def go(text: str) -> Path:
        path = tmp_path / "sources.yaml"
        path.write_text(text)
        return path

    return go


# Verifies: specs/lead-source-adapters/requirements.md#6.6
def test_default_applies_when_the_file_or_key_is_absent(
    write: Write, tmp_path: Path
) -> None:
    assert DEFAULT_RUN_TIMEOUT_S == 600
    assert load_run_timeout_s(tmp_path / "absent.yaml") == DEFAULT_RUN_TIMEOUT_S
    assert load_run_timeout_s(write("")) == DEFAULT_RUN_TIMEOUT_S
    assert load_run_timeout_s(write("sources: {}\n")) == DEFAULT_RUN_TIMEOUT_S


# Verifies: specs/lead-source-adapters/requirements.md#6.6
def test_configured_value_is_read_as_seconds(write: Write) -> None:
    assert load_run_timeout_s(write("run_timeout_s: 90\n")) == 90
    assert load_run_timeout_s(write("run_timeout_s: 0.5\n")) == 0.5


# Verifies: specs/lead-source-adapters/requirements.md#6.6
def test_the_key_does_not_disturb_source_settings(write: Write) -> None:
    path = write("run_timeout_s: 30\nsources:\n  provider_one:\n    trust_rank: 3\n")
    assert load_source_settings(path) == {"provider_one": SourceSettings(trust_rank=3)}
    assert load_run_timeout_s(path) == 30


# Verifies: specs/lead-source-adapters/requirements.md#6.6
@pytest.mark.parametrize(
    "bad", ["0", "-1", "0.0", "true", "'60'", "null", "[60]", ".inf", ".nan"]
)
def test_a_non_positive_non_finite_or_non_number_is_rejected_without_echo(
    write: Write, bad: str
) -> None:
    path = write(f"run_timeout_s: {bad}\n")
    with pytest.raises(ConfigurationError) as caught:
        load_run_timeout_s(path)
    assert caught.value.key_path == "run_timeout_s"
    assert caught.value.path == str(path)
    assert bad.strip("'") not in caught.value.detail


# Verifies: specs/lead-source-adapters/requirements.md#6.6
def test_an_integer_too_large_for_a_float_is_a_configuration_error(
    write: Write,
) -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_run_timeout_s(write(f"run_timeout_s: {10**400}\n"))
    assert caught.value.key_path == "run_timeout_s"
