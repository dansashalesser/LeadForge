"""The demo builds its profile from the catalog, not from a profile file (Req 2.7)."""

from leadforge.lead_ingestion import catalog as catalog_module
from leadforge.lead_ingestion.demo import generator
from leadforge.lead_ingestion.demo.cli import demo_profile


# Verifies: specs/user-recognition/requirements.md#2.7
def test_the_demo_profile_is_the_catalog_with_datastax_as_target() -> None:
    profile = demo_profile()

    assert "datastax" in profile.technologies
    assert "datastax" not in profile.competitors
    assert set(profile.competitors) == set(
        catalog_module.load_catalog().vendor_keys()
    ) - {"datastax"}
    assert profile.keyword_templates


# Verifies: specs/user-recognition/requirements.md#2.7
def test_the_generator_technologies_are_the_catalog_uids() -> None:
    assert set(generator._TECHNOLOGIES) == set(
        catalog_module.load_catalog().technology_uids()
    )
