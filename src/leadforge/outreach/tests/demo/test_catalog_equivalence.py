"""Shipped catalog equals target_profile.yaml + company_terms.yaml (Req 2.1, 2.7).

Lives under ``tests/demo``: it names vendors, which the neutrality guard exempts there.
"""

from pathlib import Path

from leadforge.lead_ingestion.catalog import load_catalog, load_roles
from leadforge.lead_ingestion.target_profile import load_target_profile
from leadforge.outreach.company_terms import load_company_terms

CONFIG = Path(__file__).resolve().parents[5] / "config"
SOURCES = {"uid_source": "apollo", "alias_source": "google_search"}


def _old():
    return load_target_profile(CONFIG / "target_profile.yaml")


def _new():
    return load_catalog().to_profile(
        "datastax", competitors=("mongodb", "couchbase"), **SOURCES
    )


# Verifies: specs/user-recognition/requirements.md#2.1
def test_to_profile_has_the_same_terms_as_the_current_profile() -> None:
    assert set(_new().terms()) == set(_old().terms())
    assert set(_new().technologies) == set(_old().technologies)
    assert set(_new().competitors) == set(_old().competitors)


# Verifies: specs/user-recognition/requirements.md#2.1
def test_to_profile_has_the_same_per_source_vocabulary() -> None:
    old, new = _old(), _new()
    assert set(new.providers()) == set(old.providers())
    for term in old.terms():
        for provider in old.providers():
            assert new.vocabulary(provider, term) == old.vocabulary(provider, term)


# Verifies: specs/user-recognition/requirements.md#2.1
def test_vendor_domains_and_company_terms_match_company_terms_yaml() -> None:
    terms = load_company_terms(CONFIG / "company_terms.yaml")
    catalog = load_catalog()
    for key, entry in terms.companies.items():
        vendor = catalog.vendor(key)
        assert tuple(vendor.domains) == entry.domains
        selected = load_catalog().to_profile(key, **SOURCES).terms()
        assert set(selected) == set(entry.terms)


# Verifies: specs/user-recognition/requirements.md#2.7
def test_every_shipped_technology_uid_is_a_nonempty_string() -> None:
    uids = load_catalog().technology_uids()
    assert {
        "datastax",
        "cassandra",
        "mongodb_atlas",
        "mongodb_realm",
        "couchbase",
    } <= set(uids)


# Verifies: specs/user-recognition/requirements.md#7.1
def test_shipped_roles_load_with_families_and_seniority() -> None:
    roles = load_roles()
    assert roles.families
    assert roles.seniority
    assert roles.core
    assert roles.irrelevant
