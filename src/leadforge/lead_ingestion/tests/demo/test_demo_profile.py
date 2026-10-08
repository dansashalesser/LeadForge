"""The demo builds its profile from the catalog, not from a profile file (Req 2.7)."""

from leadforge.lead_ingestion import catalog as catalog_module
from leadforge.lead_ingestion.demo import generator
from leadforge.lead_ingestion.demo.cli import PROFILE, demo_profile
from leadforge.lead_ingestion.target_profile import load_target_profile


# Verifies: specs/user-recognition/requirements.md#2.7
def test_the_demo_profile_equals_the_profile_file_it_replaces() -> None:
    old = load_target_profile(PROFILE)
    new = demo_profile()

    assert set(new.technologies) == set(old.technologies)
    assert set(new.competitors) == set(old.competitors)
    assert new.keyword_templates == old.keyword_templates
    for term in old.terms():
        for provider in old.providers():
            assert new.vocabulary(provider, term) == old.vocabulary(provider, term)


# Verifies: specs/user-recognition/requirements.md#2.7
def test_the_generator_technologies_are_the_catalog_uids() -> None:
    assert set(generator._TECHNOLOGIES) == set(
        catalog_module.load_catalog().technology_uids()
    )
