"""Catalog loader: one YAML per vendor, typed, unknown keys are errors (Req 2)."""

from pathlib import Path

import pytest

from leadforge.lead_ingestion import catalog as catalog_module
from leadforge.lead_ingestion.catalog import (
    Catalog,
    CatalogError,
    UnknownCatalogKeyError,
    UnknownTechnologyUidError,
    default_catalog_dir,
    load_catalog,
    unknown_technology_uids,
)
from leadforge.lead_ingestion.errors import ConfigurationError

ACME = """\
vendor: {key: acme, name: Acme, domains: [acme.example], partner_domains: [p.example]}
products:
  - key: widget
    name: Widget
    aliases: [{text: "Acme Widget"}, {text: "Widget", co_terms: [Acme, gizmo]}]
    technology_uids: [acme_widget]
    packages: ["com.acme.widget"]
    customer_paths: ["/customers"]
ecosystem:
  - key: gizmo
    aliases: [{text: "Gizmo"}]
    technology_uids: [gizmo]
"""


def _dir(tmp_path: Path, text: str = ACME) -> Path:
    (tmp_path / "acme.yaml").write_text(text)
    return tmp_path


# Verifies: specs/user-recognition/requirements.md#2.1
def test_loads_a_vendor_file_with_its_products_and_ecosystem(tmp_path: Path) -> None:
    catalog = load_catalog(_dir(tmp_path))
    vendor = catalog.vendor("acme")
    assert vendor.name == "Acme"
    assert vendor.partner_domains == ("p.example",)
    product = catalog.product("acme", "widget")
    assert product.packages == ("com.acme.widget",)
    assert product.customer_paths == ("/customers",)
    assert product.aliases[1].co_terms == ("Acme", "gizmo")
    assert product.aliases[0].co_terms == ()
    assert [e.key for e in vendor.ecosystem] == ["gizmo"]
    assert isinstance(catalog, Catalog)


# Verifies: specs/user-recognition/requirements.md#2.1
@pytest.mark.parametrize(
    "bad",
    [
        ACME.replace("name: Widget", "name: Widget, colour: red"),
        ACME.replace("domains: [acme.example],", "domains: [acme.example], x: 1,"),
        ACME + "surprise: 1\n",
    ],
)
def test_an_unknown_key_fails_load_with_a_named_error(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ConfigurationError) as info:
        load_catalog(_dir(tmp_path, bad))
    assert "acme.yaml" in str(info.value)


def test_roles_file_and_drafts_directory_are_not_vendor_entries(
    tmp_path: Path,
) -> None:
    _dir(tmp_path)
    (tmp_path / "roles.yaml").write_text("anything: goes\n")
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "d.yaml").write_text("nonsense: 1\n")
    assert load_catalog(tmp_path).vendor_keys() == ("acme",)


def test_duplicate_vendor_keys_are_refused(tmp_path: Path) -> None:
    _dir(tmp_path)
    (tmp_path / "other.yaml").write_text(ACME)
    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path)


def test_missing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path / "nope")


def test_unknown_keys_raise_the_named_error(tmp_path: Path) -> None:
    catalog = load_catalog(_dir(tmp_path))
    with pytest.raises(UnknownCatalogKeyError):
        catalog.vendor("nope")
    with pytest.raises(UnknownCatalogKeyError):
        catalog.product("acme", "nope")
    assert issubclass(UnknownCatalogKeyError, CatalogError)


# Verifies: specs/user-recognition/requirements.md#2.6
def test_an_unknown_technology_uid_fails_load(tmp_path: Path) -> None:
    listed = {"acme_widget": ("Cat", "Widget")}
    with pytest.raises(UnknownTechnologyUidError) as info:
        load_catalog(_dir(tmp_path), listed_technologies=listed)
    assert "gizmo" in str(info.value)
    listed["gizmo"] = ("Cat", "Gizmo")
    assert load_catalog(tmp_path, listed_technologies=listed).technology_uids() == (
        "acme_widget",
        "gizmo",
    )


def test_unknown_technology_uids_is_sorted_set_difference() -> None:
    assert unknown_technology_uids({"a": ("", "")}, frozenset({"b", "a", "c"})) == [
        "b",
        "c",
    ]


def test_env_var_overrides_the_default_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEADFORGE_CATALOG_DIR", str(tmp_path))
    assert default_catalog_dir() == tmp_path
    monkeypatch.delenv("LEADFORGE_CATALOG_DIR")
    path = default_catalog_dir()
    assert path.is_absolute()
    assert (
        path
        == Path(catalog_module.__file__).resolve().parents[3] / "config" / "catalog"
    )


# Verifies: specs/user-recognition/requirements.md#2.1
def test_to_profile_maps_selection_to_terms_and_source_vocabulary(
    tmp_path: Path,
) -> None:
    catalog = load_catalog(_dir(tmp_path))
    profile = catalog.to_profile("acme", uid_source="uids", alias_source="web")
    assert profile.terms() == ("widget", "gizmo")
    assert profile.vocabulary("uids", "widget") == ("acme_widget",)
    assert profile.vocabulary("web", "widget") == ("Acme Widget", "Widget")
    assert profile.vocabulary("web", "gizmo") == ("Gizmo",)
    assert profile.vocabulary("uids", "gizmo") == ("gizmo",)


# Verifies: specs/user-recognition/requirements.md#2.1
def test_to_profile_unknown_vendor_or_product_is_an_error(tmp_path: Path) -> None:
    catalog = load_catalog(_dir(tmp_path))
    with pytest.raises(UnknownCatalogKeyError):
        catalog.to_profile("nope", uid_source="u", alias_source="w")
    with pytest.raises(UnknownCatalogKeyError):
        catalog.to_profile(
            "acme", product_keys=("nope",), uid_source="u", alias_source="w"
        )


# Verifies: specs/user-recognition/requirements.md#7.1
def test_load_roles_validates_the_file(tmp_path: Path) -> None:
    from leadforge.lead_ingestion.catalog import load_roles

    (tmp_path / "roles.yaml").write_text(
        "families:\n"
        "  - {key: eng, core: [data engineer], adjacent: [analyst],"
        " irrelevant: [recruiter]}\n"
        "seniority: [director]\n"
    )
    roles = load_roles(tmp_path)
    assert roles.core == ("data engineer",)
    assert roles.seniority == ("director",)
    (tmp_path / "roles.yaml").write_text("families: []\nbogus: 1\n")
    with pytest.raises(ConfigurationError):
        load_roles(tmp_path)
