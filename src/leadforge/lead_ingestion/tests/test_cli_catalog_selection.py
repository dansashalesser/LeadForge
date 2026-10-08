"""``ingest --vendor/--product`` builds the profile from the catalog (Req 2.7)."""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from leadforge.lead_ingestion import cli
from leadforge.lead_ingestion.target_profile import TargetProfile

runner = CliRunner()

ACME = """\
vendor: {key: acme, name: Acme, domains: [acme.example], partner_domains: []}
products:
  - key: widget
    aliases: [{text: "Acme Widget"}]
    technology_uids: [acme_widget]
  - key: gadget
    aliases: [{text: "Acme Gadget"}]
"""
OTHER = """\
vendor: {key: other, name: Other, domains: [other.example], partner_domains: []}
products:
  - key: thing
    aliases: [{text: "Other Thing"}]
    technology_uids: [other_thing]
"""


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "acme.yaml").write_text(ACME, encoding="utf-8")
    (root / "other.yaml").write_text(OTHER, encoding="utf-8")
    monkeypatch.setenv("LEADFORGE_CATALOG_DIR", str(root))
    seen: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> Any:
        seen.update(kwargs)
        raise cli.ConfigurationError("stop", key_path="", detail="stub")

    monkeypatch.setattr(cli, "run_ingestion", fake)
    return seen


def _ingest(*args: str) -> Any:
    return runner.invoke(
        cli.app, ["ingest", "--uid-source", "uids", "--alias-source", "names", *args]
    )


# Verifies: specs/user-recognition/requirements.md#2.7
def test_vendor_and_product_build_the_profile_from_the_catalog(
    captured: dict[str, Any],
) -> None:
    _ingest("--vendor", "acme", "--product", "widget")

    profile = captured["target_profile"]
    assert isinstance(profile, TargetProfile)
    assert profile.technologies["widget"]["uids"] == ("acme_widget",)
    assert profile.technologies["widget"]["names"] == ("Acme Widget",)
    assert "gadget" not in profile.terms()
    assert "thing" in profile.competitors  # the other vendors are the competitors
    assert "target_profile_path" not in captured


# Verifies: specs/user-recognition/requirements.md#2.7
def test_a_vendor_without_products_takes_all_of_its_products(
    captured: dict[str, Any],
) -> None:
    _ingest("--vendor", "acme")

    assert {"widget", "gadget"} <= set(captured["target_profile"].technologies)


# Verifies: specs/user-recognition/requirements.md#2.7
def test_an_unknown_vendor_is_a_clean_configuration_error(
    captured: dict[str, Any],
) -> None:
    result = _ingest("--vendor", "nobody")

    assert result.exit_code == 2
    assert "nobody" in result.output
    assert captured == {}


# Verifies: specs/user-recognition/requirements.md#2.2
def test_a_product_without_a_vendor_is_refused(captured: dict[str, Any]) -> None:
    result = _ingest("--product", "widget")

    assert result.exit_code == 2
    assert captured == {}


# Verifies: specs/user-recognition/requirements.md#2.7
def test_with_no_vendor_no_profile_is_given(captured: dict[str, Any]) -> None:
    _ingest()

    assert "target_profile" not in captured
