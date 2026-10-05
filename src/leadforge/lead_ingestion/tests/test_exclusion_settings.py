"""Loading Identity Exclusions from ``config/`` (task 16.6, Requirement 8.13)."""

from pathlib import Path

import pytest

from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.exclusion_settings import (
    DEFAULT_EXCLUSIONS_PATH,
    load_identity_exclusions,
)
from leadforge.lead_ingestion.match_keys import IdentityExclusions

SECRET = "secret.person@private.example"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "identity_exclusions.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_default_path_is_under_config() -> None:
    assert Path("config/identity_exclusions.yaml") == DEFAULT_EXCLUSIONS_PATH


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_absent_or_empty_file_means_no_exclusions(tmp_path: Path) -> None:
    assert load_identity_exclusions(tmp_path / "nope.yaml") == IdentityExclusions()
    assert load_identity_exclusions(write(tmp_path, "")) == IdentityExclusions()
    assert load_identity_exclusions(write(tmp_path, "emails:\n")) == (
        IdentityExclusions()
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_values_are_read_and_normalised_like_key_extraction(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "emails:\n  - ' Info@X.com '\nlinkedin_urls:\n"
        "  - https://LinkedIn.com/in/Ann/?x=1\n",
    )
    assert load_identity_exclusions(path) == IdentityExclusions.from_values(
        emails=["info@x.com"], linkedin_urls=["linkedin.com/in/ann"]
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.13
@pytest.mark.parametrize(
    ("text", "key_path"),
    [
        ("emails:\n  - ok@x.com\n  - secret.person.private.example\n", "emails[1]"),
        ("emails:\n  - ''\n", "emails[0]"),
        ("emails:\n  - 5\n", "emails[0]"),
        ("emails:\n  -\n", "emails[0]"),
        (f"linkedin_urls:\n  - 'https://  {SECRET}'\n", "linkedin_urls[0]"),
        (f"linkedin_urls: {SECRET}\n", "linkedin_urls"),
        (f"emails: {{a: {SECRET}}}\n", "emails"),
    ],
)
def test_a_bad_entry_names_file_and_key_path_but_never_the_value(
    tmp_path: Path, text: str, key_path: str
) -> None:
    path = write(tmp_path, text)
    with pytest.raises(ConfigurationError) as err:
        load_identity_exclusions(path)
    assert err.value.path == str(path)
    assert err.value.key_path == key_path
    assert "secret" not in str(err.value)
    assert "private.example" not in str(err.value)


# Verifies: specs/lead-source-adapters/requirements.md#8.13
@pytest.mark.parametrize(
    "text", ["- a@x.com\n", f"{SECRET}: []\n", f"emails: []\n{SECRET}: 1\n"]
)
def test_structure_errors_are_named_without_echoing_keys(
    tmp_path: Path, text: str
) -> None:
    with pytest.raises(ConfigurationError) as err:
        load_identity_exclusions(write(tmp_path, text))
    assert "secret" not in str(err.value)


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_a_duplicate_key_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="duplicate key"):
        load_identity_exclusions(write(tmp_path, "emails: []\nemails: []\n"))


# Verifies: specs/lead-source-adapters/requirements.md#8.13
@pytest.mark.parametrize("bad", [5, None, b"a@x.com", ["a@x.com"]])
def test_from_values_rejects_non_text_with_a_value_error_that_names_no_value(
    bad: object,
) -> None:
    with pytest.raises(ValueError, match="usable key value") as err:
        IdentityExclusions.from_values(emails=[bad])  # type: ignore[list-item]
    assert "a@x.com" not in str(err.value)
    with pytest.raises(ValueError, match="usable key value"):
        IdentityExclusions.from_values(linkedin_urls=[bad])  # type: ignore[list-item]
