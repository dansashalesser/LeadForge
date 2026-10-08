import pytest

from leadforge.outreach.usage.person import PersonFit, RoleVocabulary, grade_person_fit

ROLES = RoleVocabulary(
    core=(
        "engineering",
        "engineer",
        "data platform",
        "infrastructure",
        "database",
        "storage",
        "site reliability",
    ),
    adjacent=("cto", "vp", "chief technology officer"),
    irrelevant=("sales", "marketing", "recruiter"),
)


# Verifies: specs/user-recognition/requirements.md#7.1
@pytest.mark.parametrize(
    ("title", "grade"),
    [
        ("VP Engineering", "core"),
        ("VP of Sales", "irrelevant"),
        ("VP Sales", "irrelevant"),
        ("Head Marketing", "irrelevant"),
        ("Head Data Platform", "core"),
        ("Director Infrastructure", "core"),
        ("Staff Database Engineer", "core"),
        ("Engineering Manager, Storage", "core"),
        ("Principal Site Reliability Engineer", "core"),
        ("CTO", "adjacent"),
        ("Sales Engineer", "irrelevant"),
        ("Salesware Architect", "irrelevant"),  # not "sales"; no vocabulary hit
        ("Salesware Database Engineer", "core"),  # "salesware" must not trip "sales"
        ("Gardener", "irrelevant"),  # "engineer" is not inside "gardener"
        ("", "irrelevant"),
    ],
)
def test_title_grades(title, grade):
    assert grade_person_fit([title], ROLES).grade == grade


def test_fields_beyond_title_count():
    assert grade_person_fit(["Manager", "Storage"], ROLES).grade == "core"


# Verifies: specs/user-recognition/requirements.md#7.2
@pytest.mark.parametrize(
    ("stated", "confirmed", "grade", "boosted"),
    [
        (True, True, "core", True),
        (True, False, "adjacent", False),
        (False, True, "adjacent", False),
    ],
)
def test_self_stated_boost_needs_confirmed_record(stated, confirmed, grade, boosted):
    got = grade_person_fit(
        ["CTO"], ROLES, self_stated=stated, self_stated_confirmed=confirmed
    )
    assert got == PersonFit(grade, boosted)


def test_boost_never_rescues_irrelevant():
    got = grade_person_fit(
        ["VP Sales"], ROLES, self_stated=True, self_stated_confirmed=True
    )
    assert got.grade == "irrelevant"
