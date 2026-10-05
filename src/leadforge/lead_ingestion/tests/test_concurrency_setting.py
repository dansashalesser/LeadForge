"""``max_concurrent_sources`` in ``config/sources.yaml`` (task 11.1, 6.7)."""

from collections.abc import Callable
from pathlib import Path

import pytest

from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.registry import SourceSettings
from leadforge.lead_ingestion.source_settings import (
    DEFAULT_MAX_CONCURRENT_SOURCES,
    load_max_concurrent_sources,
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


# Verifies: specs/lead-source-adapters/requirements.md#6.7
def test_default_is_four_when_the_file_or_key_is_absent(
    write: Write, tmp_path: Path
) -> None:
    assert DEFAULT_MAX_CONCURRENT_SOURCES == 4
    assert load_max_concurrent_sources(tmp_path / "absent.yaml") == 4
    assert load_max_concurrent_sources(write("")) == 4
    assert load_max_concurrent_sources(write("sources: {}\n")) == 4


# Verifies: specs/lead-source-adapters/requirements.md#6.7
def test_configured_value_is_read(write: Write) -> None:
    assert load_max_concurrent_sources(write("max_concurrent_sources: 7\n")) == 7


# Verifies: specs/lead-source-adapters/requirements.md#6.7
def test_the_key_does_not_disturb_source_settings(write: Write) -> None:
    path = write(
        "max_concurrent_sources: 2\nsources:\n  provider_one:\n    trust_rank: 3\n"
    )
    assert load_source_settings(path) == {"provider_one": SourceSettings(trust_rank=3)}
    assert load_max_concurrent_sources(path) == 2


# Verifies: specs/lead-source-adapters/requirements.md#6.7
@pytest.mark.parametrize("bad", ["0", "-1", "true", "2.5", "'4'", "null", "[4]"])
def test_a_non_positive_or_non_integer_value_is_rejected_without_echo(
    write: Write, bad: str
) -> None:
    path = write(f"max_concurrent_sources: {bad}\n")
    with pytest.raises(ConfigurationError) as caught:
        load_max_concurrent_sources(path)
    assert caught.value.key_path == "max_concurrent_sources"
    assert caught.value.path == str(path)
    assert bad.strip("'") not in caught.value.detail


# Verifies: specs/lead-source-adapters/requirements.md#6.7
def test_other_documents_still_fail_as_before(write: Write) -> None:
    with pytest.raises(ConfigurationError):
        load_max_concurrent_sources(write("- a\n"))
    with pytest.raises(ConfigurationError):
        load_max_concurrent_sources(write("sourcez: {}\n"))
