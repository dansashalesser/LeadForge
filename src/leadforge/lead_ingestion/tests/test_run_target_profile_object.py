"""A run takes a ready Target Profile object instead of a path (outreach seam 1.1)."""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

import pytest

from leadforge.lead_ingestion import ingest_runner
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.target_profile import load_target_profile
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
async def test_a_profile_object_runs_like_the_same_profile_path(
    composed: Backend,
) -> None:
    by_path = await ingest_runner.run_ingestion(target_profile_path=PROFILE)
    by_object = await ingest_runner.run_ingestion(
        target_profile=load_target_profile(PROFILE)
    )

    assert by_object.exit == by_path.exit
    assert by_object.run_id != by_path.run_id


# Verifies: outreach requirements 1.2
async def test_giving_both_a_profile_and_a_path_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="not both"):
        await ingest_runner.run_ingestion(
            target_profile=load_target_profile(PROFILE),
            target_profile_path=PROFILE,
        )
