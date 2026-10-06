"""Three all-in-one Hunter verifications are 1.5 Credits, stored and reported exactly.

Follow-up fu3 (2026-10-06) to Requirements 16.8 and 21.2: the adapter used to round a
batch's half Credits up. It now reports an exact ``Decimal``; the store keeps it as
integer milli-Credits (0008) and the report prints it unrounded, on SQLite and
PostgreSQL.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

from decimal import Decimal

from leadforge.lead_ingestion.adapters.hunter import credits_in
from leadforge.lead_ingestion.base_source import RawBatch
from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.tests.store_run_support import (
    Script,
    person,
    registry_of,
    scripted_source,
)
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)
from leadforge.lead_ingestion.tests.test_spend_record_both_engines import (
    assert_exact_credits,
)

THREE_ALL_IN_ONE_VERIFICATIONS = RawBatch(
    source_name="hunter",
    payload={
        "searches": [],
        "finds": [],
        "verifications": [
            {"email": f"p{n}@example.com", "response": None} for n in range(3)
        ],
        "credits_billable": True,
        "plan": "all-in-one",
    },
)


# Verifies: specs/lead-source-adapters/requirements.md#16.8
def test_three_all_in_one_verifications_are_exactly_one_and_a_half_credits() -> None:
    spent = credits_in(THREE_ALL_IN_ONE_VERIFICATIONS)
    assert isinstance(spent, Decimal)
    assert spent == Decimal("1.5")


# Verifies: specs/lead-source-adapters/requirements.md#16.8
# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_the_adapters_one_and_a_half_credits_are_stored_and_reported(
    composed: Backend,
) -> None:
    spent = credits_in(THREE_ALL_IN_ONE_VERIFICATIONS)
    source = scripted_source(
        "alpha", Script([person("ada@example.com", "Ada Lovelace")], credits=spent)
    )
    outcome = await run_ingestion(registry=registry_of(source))
    assert_exact_credits(composed, outcome.report_text, Decimal("1.5"))
