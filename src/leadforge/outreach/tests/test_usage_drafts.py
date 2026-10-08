"""Catalog drafts for unknown vendors: LLM-drafted, held until approved (Req 2.5)."""

from pathlib import Path

import pytest
import yaml

from leadforge.lead_ingestion.catalog import (
    UnapprovedDraftError,
    UnknownCatalogKeyError,
    UnknownTechnologyUidError,
    load_catalog,
)
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.outreach.tests.support import ScriptedModel
from leadforge.outreach.usage.drafts import (
    DraftExistsError,
    DraftInvalidError,
    DraftNotFoundError,
    approve_draft,
    draft_vendor,
)

LISTED = {"uid_a": {}, "uid_b": {}}


def _doc(key: str = "newco", uids: list[str] | None = None) -> dict[str, object]:
    return {
        "vendor": {"key": key, "name": "NewCo", "domains": ["newco.example"]},
        "products": [
            {
                "key": "widget",
                "name": "Widget",
                "aliases": [{"text": "NewCo Widget"}],
                "technology_uids": ["uid_a"] if uids is None else uids,
            }
        ],
    }


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "other.yaml").write_text(
        yaml.safe_dump(
            {
                "vendor": {"key": "other", "name": "Other"},
                "products": [{"key": "p", "aliases": [{"text": "Other P"}]}],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _draft(model: ScriptedModel, root: Path, key: str = "newco") -> Path:
    return draft_vendor(
        model, key=key, name="NewCo", listed_technologies=LISTED, catalog_dir=root
    )


# Verifies: specs/user-recognition/requirements.md#2.5
def test_a_draft_lands_in_drafts_and_the_model_sees_the_uid_list(root: Path) -> None:
    model = ScriptedModel(_doc())

    path = _draft(model, root)

    assert path == root / "drafts" / "newco.yaml"
    assert yaml.safe_load(path.read_text())["vendor"]["key"] == "newco"
    asked = "\n".join(text for _, text in model.asked[0])
    assert all(token in asked for token in ("uid_a", "uid_b", "NewCo"))


# Verifies: specs/user-recognition/requirements.md#2.5
def test_a_search_naming_a_draft_is_refused_until_approved(root: Path) -> None:
    _draft(ScriptedModel(_doc()), root)

    catalog = load_catalog(root)
    assert catalog.vendor_keys() == ("other",)
    with pytest.raises(UnapprovedDraftError):
        catalog.vendor("newco")
    with pytest.raises(UnknownCatalogKeyError):  # still a catalog key error
        catalog.product("newco", "widget")

    approve_draft("newco", catalog_dir=root)

    assert not (root / "drafts" / "newco.yaml").exists()
    assert load_catalog(root).vendor("newco").name == "NewCo"


# Verifies: specs/user-recognition/requirements.md#2.5
def test_a_truly_unknown_vendor_is_not_reported_as_a_draft(root: Path) -> None:
    with pytest.raises(UnknownCatalogKeyError) as caught:
        load_catalog(root).vendor("nope")
    assert not isinstance(caught.value, UnapprovedDraftError)


# Verifies: specs/user-recognition/requirements.md#2.5
def test_a_uid_outside_the_supported_list_is_refused_and_nothing_written(
    root: Path,
) -> None:
    with pytest.raises(UnknownTechnologyUidError):
        _draft(ScriptedModel(_doc(uids=["uid_a", "invented"])), root)

    assert not (root / "drafts").exists()


# Verifies: specs/user-recognition/requirements.md#2.5
def test_schema_invalid_output_is_retried_then_refused(root: Path) -> None:
    bad = {"vendor": {"key": "newco"}, "bogus": 1}
    model = ScriptedModel(bad, _doc())
    assert _draft(model, root).exists()
    assert len(model.asked) == 2

    with pytest.raises(DraftInvalidError):
        _draft(ScriptedModel(bad), root, key="other2")


# Verifies: specs/user-recognition/requirements.md#2.5
def test_the_draft_key_must_match_the_requested_key(root: Path) -> None:
    with pytest.raises(DraftInvalidError):
        _draft(ScriptedModel(_doc(key="someone_else")), root)


# Verifies: specs/user-recognition/requirements.md#2.5
@pytest.mark.parametrize("key", ["../evil", "A B", "", "a/b"])
def test_an_unsafe_vendor_key_is_refused_before_any_model_call(
    root: Path, key: str
) -> None:
    model = ScriptedModel(_doc())
    with pytest.raises(DraftInvalidError):
        _draft(model, root, key=key)
    assert model.asked == []


# Verifies: specs/user-recognition/requirements.md#2.5
def test_an_approved_vendor_file_is_never_overwritten(root: Path) -> None:
    before = (root / "other.yaml").read_text()

    with pytest.raises(DraftExistsError):
        _draft(ScriptedModel(_doc(key="other")), root, key="other")

    assert (root / "other.yaml").read_text() == before


# Verifies: specs/user-recognition/requirements.md#2.5
def test_approving_refuses_a_missing_draft_and_an_existing_vendor(root: Path) -> None:
    with pytest.raises(DraftNotFoundError):
        approve_draft("newco", catalog_dir=root)

    _draft(ScriptedModel(_doc()), root)
    (root / "newco.yaml").write_text("keep", encoding="utf-8")
    with pytest.raises(DraftExistsError):
        approve_draft("newco", catalog_dir=root)
    assert (root / "newco.yaml").read_text() == "keep"


# Verifies: specs/user-recognition/requirements.md#2.5
def test_approving_revalidates_an_edited_draft(root: Path) -> None:
    path = _draft(ScriptedModel(_doc()), root)
    path.write_text("vendor: {key: newco}\nbogus: 1\n", encoding="utf-8")

    with pytest.raises(ConfigurationError):
        approve_draft("newco", catalog_dir=root)

    assert path.exists()
    assert not (root / "newco.yaml").exists()
