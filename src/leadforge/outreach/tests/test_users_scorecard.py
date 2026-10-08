"""Users-mode precision, recall and F1 on hand-built decision sets."""

import pytest

from leadforge.outreach.scorecard import (
    UserVerdict,
    render_users,
    score_users,
)


def _v(scenario: str, is_user: bool, status: str | None) -> UserVerdict:
    return UserVerdict(scenario=scenario, is_user=is_user, status=status)


PERFECT = [
    _v("verified_user", True, "selected"),
    _v("verified_user", True, "selected"),
    _v("vendor_staff", False, "rejected"),
    _v("vendor_staff", False, "rejected"),
]


# Verifies: specs/user-recognition/requirements.md#1.3
def test_a_perfect_run_scores_one_everywhere() -> None:
    card = score_users(PERFECT)
    assert (card.precision, card.recall, card.f1) == (1.0, 1.0, 1.0)
    assert card.scenarios["vendor_staff"].true_negative == 2
    assert card.scenarios["verified_user"].true_positive == 2


# Verifies: specs/user-recognition/requirements.md#1.3
def test_selecting_everyone_keeps_recall_and_loses_precision() -> None:
    card = score_users([_v(v.scenario, v.is_user, "selected") for v in PERFECT])
    assert card.precision == 0.5
    assert card.recall == 1.0
    assert card.f1 == pytest.approx(2 / 3)
    assert card.scenarios["vendor_staff"].false_positive == 2


# Verifies: specs/user-recognition/requirements.md#1.3
def test_manual_review_counts_as_not_selected() -> None:
    rows = [_v("verified_user", True, "manual_review"), *PERFECT[:1]]
    card = score_users(rows)
    assert card.recall == 0.5
    assert card.scenarios["verified_user"].false_negative == 1
    wrong = [_v("vendor_staff", False, "manual_review")]
    assert score_users(wrong).scenarios["vendor_staff"].true_negative == 1


# Verifies: specs/user-recognition/requirements.md#1.3
def test_a_person_the_search_did_not_gather_is_not_selected() -> None:
    card = score_users([_v("verified_user", True, None)])
    assert card.scenarios["verified_user"].false_negative == 1
    assert card.recall == 0.0


# Verifies: specs/user-recognition/requirements.md#1.3
def test_empty_denominators_give_none_not_a_division_error() -> None:
    nothing_selected = score_users([_v("vendor_staff", False, "rejected")])
    assert nothing_selected.precision is None  # nothing selected
    assert nothing_selected.recall is None  # no true users in the key
    assert nothing_selected.f1 is None
    none_right = score_users([_v("verified_user", True, "rejected")])
    assert none_right.precision is None
    assert none_right.recall == 0.0
    assert none_right.f1 is None
    assert score_users([]).precision is None


# Verifies: specs/user-recognition/requirements.md#1.3
def test_the_report_prints_all_three_metrics_and_a_row_per_scenario() -> None:
    text = render_users(score_users(PERFECT))
    for word in ("precision", "recall", "f1", "verified_user", "vendor_staff"):
        assert word in text
    assert "n/a" in render_users(score_users([]))
