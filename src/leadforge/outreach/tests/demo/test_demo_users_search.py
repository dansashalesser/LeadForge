"""The users search for DataStax, scored on `is_user` end to end (1.4, 8.3).

The executable spec of the feature: red until the usage stage and the qualify gate land
(task 9 of specs/user-recognition).
"""

# ruff: noqa: F811 - fixtures imported from the ingestion tests

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)
from leadforge.outreach.scorecard import render_users, score_users_search
from leadforge.outreach.tables import OutreachDecision
from leadforge.outreach.tests.demo.test_demo_run import KEY, _search

MIN_PRECISION = 0.98
MIN_RECALL = 0.85
MIN_CITED_CLASSES = 2


def _cited_classes(reasons: list[Any]) -> set[str]:
    """Evidence classes cited with a URL by one Decision's stored reasons.

    Reads `evidence_refs` (design: `{class, source, url, observed_on, quote,
    relationship}`) from the stored reason JSON. A Decision without any yields the
    empty set, so a selection with no citations fails the count, not a lookup.
    """
    classes: set[str] = set()
    for reason in reasons:
        for ref in reason.get("evidence_refs") or ():
            if ref.get("url") and ref.get("class"):
                classes.add(ref["class"])
    return classes


# Verifies: specs/user-recognition/requirements.md#1.4
# Verifies: specs/user-recognition/requirements.md#8.3
@pytest.mark.xfail(
    strict=True,
    reason="user-recognition: red until task 9 (usage stage + qualify gate)",
)
async def test_a_demo_users_search_for_datastax_is_precise_and_every_selection_is_cited(
    composed: Backend, tmp_path: Any
) -> None:
    summary = await _search(composed, tmp_path, "users", "DataStax")

    with Session(composed.engine) as session:
        card = score_users_search(session, summary.search_id, KEY)
        selected = [
            d.reasons
            for d in session.scalars(
                select(OutreachDecision).where(
                    OutreachDecision.search_id == summary.search_id,
                    OutreachDecision.status == "selected",
                )
            )
        ]
    text = render_users(card)
    adversarial_fp = {
        name: row.false_positive
        for name, row in card.scenarios.items()
        if name != "verified_user" and row.false_positive
    }
    uncited = sum(
        len(_cited_classes(reasons)) < MIN_CITED_CLASSES for reasons in selected
    )
    summary_line = (
        f"selected={len(selected)} uncited(<{MIN_CITED_CLASSES} classes with URL)="
        f"{uncited} adversarial_false_positives={adversarial_fp}\n{text}"
    )

    assert card.precision is not None, summary_line
    assert card.precision >= MIN_PRECISION, summary_line
    assert card.recall is not None, summary_line
    assert card.recall >= MIN_RECALL, summary_line
    assert not adversarial_fp, summary_line
    assert selected, summary_line
    assert uncited == 0, summary_line
