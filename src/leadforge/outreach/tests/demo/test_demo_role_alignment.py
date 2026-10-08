"""The demo answer key's `person_fit` is what the shared roles.yaml grades (7.1)."""

import pytest

from leadforge.lead_ingestion.catalog import load_roles
from leadforge.lead_ingestion.demo import generator as g
from leadforge.lead_ingestion.demo import usage_data
from leadforge.outreach.usage.person import RoleVocabulary, grade_person_fit

_TITLES = (
    *((t, s, d) for t, s, d in g._TITLES),
    usage_data._SALES,
    usage_data._MARKETING,
)


# Verifies: specs/user-recognition/requirements.md#7.1
@pytest.mark.parametrize(("title", "seniority", "department"), _TITLES)
def test_the_key_grades_each_demo_title_as_roles_yaml_does(
    title: str, seniority: str, department: str
) -> None:
    roles = load_roles()
    vocab = RoleVocabulary(roles.core, roles.adjacent, roles.irrelevant)

    fit = grade_person_fit([title, department, seniority], vocab)

    assert fit.grade == g._TITLE_FIT[title]
