"""A run takes a ready Target Profile object (outreach seam 1.1)."""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

from leadforge.lead_ingestion import ingest_runner
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    PROFILE,
    composed,
)


# Verifies: outreach requirements 1.2
async def test_a_profile_object_runs(composed: Backend) -> None:
    outcome = await ingest_runner.run_ingestion(target_profile=PROFILE)

    assert outcome.exit.exit_code == 0
