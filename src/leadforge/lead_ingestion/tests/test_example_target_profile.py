"""The shipped example Target Profile works on a clean clone (task 9.2, req 23.5).

These tests load ``config/target_profile.yaml`` and assert structure only. They never
name a vendor in a literal: the file under test is the one place those names live.
"""

import shutil
from pathlib import Path

import pytest

from leadforge.lead_ingestion import adapters
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.target_profile import (
    DEFAULT_TARGET_PROFILE_PATH,
    TargetProfile,
    check_against_registry,
    load_target_profile,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
SHIPPED = REPO_ROOT / "config" / "target_profile.yaml"


def _clean_clone(tmp_path: Path) -> Path:
    clone = tmp_path / "clone"
    shutil.copytree(REPO_ROOT / "config", clone / "config")
    return clone


# Verifies: specs/lead-source-adapters/requirements.md#23.5
def test_shipped_profile_exists_and_loads_with_no_edits() -> None:
    profile = load_target_profile(SHIPPED)

    assert isinstance(profile, TargetProfile)


# Verifies: specs/lead-source-adapters/requirements.md#23.5
def test_default_path_loads_from_the_repository_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO_ROOT)

    assert load_target_profile() == load_target_profile(SHIPPED)


# Verifies: specs/lead-source-adapters/requirements.md#23.5
def test_clean_checkout_copy_loads_through_the_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(_clean_clone(tmp_path))

    assert load_target_profile() == load_target_profile(SHIPPED)


# Verifies: specs/lead-source-adapters/requirements.md#23.5
def test_default_path_from_another_directory_fails_naming_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigurationError) as caught:
        load_target_profile()

    assert str(DEFAULT_TARGET_PROFILE_PATH) in str(caught.value)
    assert "file not found" in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#23.5
def test_profile_is_complete_enough_to_run_the_demo() -> None:
    profile = load_target_profile(SHIPPED)

    assert len(profile.technologies) >= 1
    assert len(profile.competitors) >= 1
    assert len(profile.keyword_templates) >= 1
    assert len(profile.providers()) >= 2


# Verifies: specs/lead-source-adapters/requirements.md#23.2
def test_a_term_carries_vocabularies_for_more_than_one_provider() -> None:
    profile = load_target_profile(SHIPPED)

    widest = max(
        sum(1 for p in profile.providers() if profile.vocabulary(p, t) is not None)
        for t in profile.terms()
    )

    assert widest >= 2


# Verifies: specs/lead-source-adapters/requirements.md#3.3
def test_every_term_is_expressible_by_at_least_one_provider() -> None:
    profile = load_target_profile(SHIPPED)

    silent = [
        t
        for t in profile.terms()
        if all(profile.vocabulary(p, t) is None for p in profile.providers())
    ]

    assert silent == []


# Verifies: specs/lead-source-adapters/requirements.md#23.5
def test_keywords_render_deterministically_for_every_term() -> None:
    first = load_target_profile(SHIPPED)
    second = load_target_profile(SHIPPED)

    for term in first.terms():
        rendered = first.render_keywords(term)
        assert rendered == second.render_keywords(term)
        assert len(rendered) == len(first.keyword_templates)
        assert all(term in text and "{term}" not in text for text in rendered)


# Verifies: specs/lead-source-adapters/requirements.md#23.5
def test_against_the_empty_adapter_package_columns_are_warnings_not_errors() -> None:
    # Tasks 12-15 register the providers; each warning then becomes a real check
    # against that source's declared surfaces (config overrides adapter defaults).
    profile = load_target_profile(SHIPPED)
    registry = SourceRegistry.discover(adapters)
    assert registry.names() == ()

    unregistered = check_against_registry(profile, registry, path=SHIPPED)

    assert unregistered == profile.providers()
    assert len(unregistered) >= 1


# Verifies: specs/lead-source-adapters/requirements.md#23.5
def test_the_shipped_file_documents_that_its_identifiers_are_examples() -> None:
    header = SHIPPED.read_text(encoding="utf-8").splitlines()[:25]

    assert any("example" in line.lower() and line.startswith("#") for line in header)
