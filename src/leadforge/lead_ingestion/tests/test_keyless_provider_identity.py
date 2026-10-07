"""A keyless record keeps its Lead across a differing re-fetch (follow-up 2026-10-06).

Requirement 8.12. A record with no Match Key used to be a singleton cluster keyed by
its whole content, so a re-fetch that differed in any field became a second Lead. A
record that carries its provider's own record id (``person.provider_id``) is now
identified by source + that id: a keyless record joins the one cluster holding the
same source's record of that id, or the other keyless records of it. Keyed clusters
are never joined through it (Match Keys stay the authority), and an id held by two
keyed clusters is ambiguous: the keyless record then stays on its own.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

from datetime import timedelta
from typing import Any

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.remerge import merge_contributions
from leadforge.lead_ingestion.tests.store_run_support import active_leads
from leadforge.lead_ingestion.tests.test_lead_remerge import (
    NONE,
    NOW,
    RANKS,
    contribution,
    remerge,
)
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)


def _keyless(
    source: str, record: str | None, title: str, minutes: int = 0
) -> LeadContribution:
    values: dict[str, Any] = {
        "person__full_name": "Nameless Person",
        "person__title": title,
        "company__name": "Acme Ltd",
    }
    if record is not None:
        values["person__provider_id"] = record
    return contribution(source, at=NOW + timedelta(minutes=minutes), **values)


def _sizes(contributions: list[LeadContribution]) -> list[int]:
    merged = merge_contributions(contributions, exclusions=NONE, trust_ranks=RANKS)
    return sorted(len(c.contributions) for c, _ in merged)


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_keyless_refetch_with_the_same_provider_id_is_one_cluster() -> None:
    old = _keyless("alpha", "rec-1", "Engineer")
    new = _keyless("alpha", "rec-1", "Staff Engineer", minutes=5)
    assert _sizes([old, new]) == [2]


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_the_provider_id_is_scoped_to_its_source_and_never_joins_keyed_clusters() -> (
    None
):
    other_source = _keyless("beta", "rec-1", "Engineer")
    assert _sizes([_keyless("alpha", "rec-1", "Engineer"), other_source]) == [1, 1]

    keyed = [
        contribution(
            "alpha",
            person__email=f"p{i}@example.com",
            person__email_status="verified",
            person__provider_id="rec-9",
        )
        for i in range(2)
    ]
    lone = _keyless("alpha", "rec-9", "Engineer")
    # Two keyed clusters hold rec-9: ambiguous, so nothing joins.
    assert _sizes([*keyed, lone]) == [1, 1, 1]
    # One keyed cluster holds it: the keyless re-fetch joins that one.
    assert _sizes([keyed[0], lone]) == [2]


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_keyless_record_without_a_provider_id_stays_on_its_own() -> None:
    assert _sizes([_keyless("alpha", None, "A"), _keyless("alpha", None, "B")]) == [
        1,
        1,
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_keyless_refetch_that_differs_updates_the_same_stored_lead(
    backend: Backend,
) -> None:
    remerge(backend.engine, [_keyless("alpha", "rec-1", "Engineer")])
    [before] = active_leads(backend.engine)

    remerge(backend.engine, [_keyless("alpha", "rec-1", "Staff Engineer", minutes=5)])

    [after] = active_leads(backend.engine)
    assert after.lead_identity_id == before.lead_identity_id
    assert after.employments != before.employments  # the newer title is projected
