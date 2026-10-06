"""A bad credit figure fails its own source only, on SQLite and PostgreSQL.

Follow-up (2026-10-06) to Requirements 21.2 and 21.5: a source whose
``credits_spent`` reports a float, or a figure finer than a milli-Credit, is a
contract violation for THAT source. It is recorded failed with an error naming the
source and the violation (no payload); every other source runs and the run's spend
record for them is written intact.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

from decimal import Decimal
from typing import Any, cast

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.orchestrator import SourceStatus
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.store_run_support import (
    Script,
    person,
    registry_of,
    scripted_source,
)
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    _alembic,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)


# Verifies: specs/lead-source-adapters/requirements.md#21.2
# Verifies: specs/lead-source-adapters/requirements.md#21.5
@pytest.mark.parametrize(
    ("bad", "violation"),
    [
        (0.1, "float"),
        (Decimal("0.0005"), "finer than 0.001"),
        # Past PostgreSQL's 32-bit milli-Credit column: refused here, not at the write.
        (Decimal(10**7), "exceed"),
    ],
)
async def test_a_bad_credit_figure_fails_its_source_alone(
    composed: Backend, bad: Any, violation: str
) -> None:
    sources = (
        scripted_source(
            "alpha",
            Script([person("ada@example.com", "Ada Lovelace")], credits=cast(Any, bad)),
        ),
        scripted_source(
            "bravo",
            Script([person("bob@example.com", "Bob Byte")], credits=Decimal("1.5")),
        ),
        scripted_source(
            "charlie",
            Script([person("cy@example.com", "Cy Cipher")], credits=cast(Any, 2)),
        ),
    )

    outcome = await run_ingestion(registry=registry_of(*sources))

    by_name = {r.source_name: r for r in outcome.results}
    alpha = by_name["alpha"].outcome
    assert alpha.status is SourceStatus.FAILED
    assert alpha.error is not None
    assert "[alpha]" in alpha.error
    assert "credits_spent" in alpha.error
    assert violation in alpha.error
    assert "ada@example.com" not in alpha.error
    assert by_name["alpha"].credits_consumed is None
    for name in ("bravo", "charlie"):
        assert by_name[name].outcome.status is SourceStatus.OK

    with Session(composed.engine) as s:
        rows = {r.source_name: r for r in s.scalars(sa.select(m.SourceRun))}
    assert rows["alpha"].failure_class == SourceStatus.FAILED.value
    assert rows["alpha"].credits_consumed is None
    assert rows["bravo"].credits_consumed == Decimal("1.5")
    assert rows["charlie"].credits_consumed == Decimal(2)
    assert rows["bravo"].failure_class is None
    assert rows["charlie"].failure_class is None
