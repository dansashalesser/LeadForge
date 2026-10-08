"""Shipped catalog: frozen profile expectation, UIDs, roles (Req 2.1, 2.7, 7.1).

Lives under ``tests/demo``: it names vendors, which the neutrality guard exempts there.
"""

from leadforge.lead_ingestion.catalog import load_catalog, load_roles

SOURCES = {"uid_source": "apollo", "alias_source": "google_search"}

# Frozen expectation of the shipped catalog's profile (it replaced the old profile
# file): term -> source -> vocabulary.
EXPECTED_TECHNOLOGIES = {
    "datastax": {
        "apollo": ("datastax",),
        "google_search": ("DataStax", "DataStax Astra"),
    },
    "apache_cassandra": {
        "apollo": ("cassandra",),
        "google_search": ("Apache Cassandra", "Cassandra database"),
    },
}
EXPECTED_COMPETITORS = {
    "mongodb": {
        "apollo": ("mongodb_atlas", "mongodb_realm"),
        "google_search": ("MongoDB", "MongoDB Atlas"),
    },
    "couchbase": {"apollo": ("couchbase",), "google_search": ("Couchbase",)},
}


# Verifies: specs/user-recognition/requirements.md#2.1
def test_to_profile_has_the_expected_terms_and_per_source_vocabulary() -> None:
    profile = load_catalog().to_profile(
        "datastax", competitors=("mongodb", "couchbase"), **SOURCES
    )
    technologies = {t: dict(c) for t, c in profile.technologies.items()}
    competitors = {t: dict(c) for t, c in profile.competitors.items()}
    assert technologies == EXPECTED_TECHNOLOGIES
    assert competitors == EXPECTED_COMPETITORS


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
