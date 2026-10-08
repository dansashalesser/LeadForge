"""Usage budget (user-recognition requirement 4.5)."""

import pytest

from leadforge.outreach.usage.budget import BUDGET_EXHAUSTED, UsageBudget


# Verifies: specs/user-recognition/requirements.md#4.5
def test_spend_counts_each_kind_separately() -> None:
    b = UsageBudget(searches=1, fetches=2, llm_calls=0)
    assert b.spend("searches") is True
    assert b.spend("searches") is False
    assert b.spend("fetches")
    assert b.spend("fetches")
    assert b.spend("fetches") is False
    assert b.spend("llm_calls") is False
    assert (b.used("searches"), b.used("fetches"), b.used("llm_calls")) == (1, 2, 0)


# Verifies: specs/user-recognition/requirements.md#4.5
def test_search_budget_of_five_allows_at_most_five() -> None:
    b = UsageBudget(searches=5, fetches=9, llm_calls=9)
    assert sum(b.spend("searches") for _ in range(12)) == 5
    assert b.exhausted("searches")
    assert not b.exhausted("fetches")
    assert BUDGET_EXHAUSTED == "budget_exhausted"


# Verifies: specs/user-recognition/requirements.md#4.5
def test_unknown_kind_and_negative_limits_fail_fast() -> None:
    with pytest.raises(ValueError, match="unknown budget kind"):
        UsageBudget(searches=1, fetches=1, llm_calls=1).spend("emails")
    with pytest.raises(ValueError, match="budget"):
        UsageBudget(searches=-1, fetches=1, llm_calls=1)
