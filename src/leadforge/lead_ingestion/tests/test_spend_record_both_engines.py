"""Exact fractional Credits and a spend record that survives the contribution write.

Follow-up (2026-10-06) to Requirements 16.8, 21.2 and 21.5, on SQLite and PostgreSQL:

* Credits are exact: 1.5 Credits are stored as integer milli-Credits (migration 0008)
  and read, summed and printed as a ``Decimal`` with no rounding and no float (the
  adapter's own proof, three half-Credit verifications, is in
  ``tests/adapters/test_exact_credits_both_engines.py``);
* the spend (calls, records fetched, Credits) commits in its own transaction BEFORE the
  run's contributions are stored, so a failing contribution write leaves the aborted
  run with an accurate spend record; writing the spend again sets the same figures,
  never adds to them.
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

import dataclasses
import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import BaseLeadSource
from leadforge.lead_ingestion.credits import MAX_MILLI, CreditValueError
from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.orchestrator import Phase
from leadforge.lead_ingestion.run_record import build_source_counts
from leadforge.lead_ingestion.run_report import build_run_report, render_run_report
from leadforge.lead_ingestion.store import merged_leads
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.run_records import RunRecordRepository
from leadforge.lead_ingestion.tests.store_run_support import (
    Script,
    count,
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
from leadforge.lead_ingestion.tests.test_run_source_counts import result

ADA = person("ada@example.com", "Ada Lovelace")
SPENT = Decimal("1.5")


def _paid(name: str = "alpha") -> type[BaseLeadSource]:
    return scripted_source(name, Script([ADA], credits=SPENT))


def source_row(backend: Backend) -> m.SourceRun:
    with Session(backend.engine) as s:
        return s.scalars(sa.select(m.SourceRun)).one()


def assert_exact_credits(backend: Backend, report_text: str, spent: Decimal) -> None:
    """Stored as ``spent`` exactly, and printed so by the report, read back."""
    row = source_row(backend)
    assert isinstance(row.credits_consumed, Decimal)
    assert row.credits_consumed == spent
    assert f"credits={spent} " in report_text
    with Session(backend.engine) as s:
        assert f"credits={spent} " in render_run_report(build_run_report(s, row.run_id))


# Verifies: specs/lead-source-adapters/requirements.md#21.2
# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_one_and_a_half_credits_are_stored_and_reported_exactly(
    composed: Backend,
) -> None:
    outcome = await run_ingestion(registry=registry_of(_paid()))
    assert_exact_credits(composed, outcome.report_text, SPENT)


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_credits_sum_exactly_over_phases() -> None:
    halves = [
        dataclasses.replace(result("alpha", phase=p), credits_consumed=Decimal("0.5"))
        for p in (Phase.DISCOVERY, Phase.ENRICHMENT)
    ] + [dataclasses.replace(result("alpha"), credits_consumed=Decimal("0.5"))]
    [alpha] = build_source_counts(tuple(halves))
    assert alpha.credits_consumed == Decimal("1.5")


# Verifies: specs/lead-source-adapters/requirements.md#21.2
# The store applies the one credit contract (``credits.exact_credits``): a float, a
# figure finer than a milli-Credit, or a negative figure is refused, never stored.
@pytest.mark.parametrize(
    "bad", [0.5, Decimal("0.0005"), Decimal("-1.5"), -1, Decimal("NaN"), True]
)
async def test_a_credit_figure_outside_the_contract_is_refused_by_the_store(
    backend: Backend, bad: Any
) -> None:
    run_id = uuid.uuid4()
    with Session(backend.engine) as s, s.begin():
        s.add(m.IngestionRun(id=run_id, started_at=sa.func.now(), status="running"))
        s.add(m.SourceRun(run_id=run_id, source_name="alpha", resolved_mode="live"))
    with (
        Session(backend.engine) as s,
        s.begin(),
        pytest.raises(sa.exc.StatementError) as refused,
    ):
        s.execute(
            sa.update(m.SourceRun)
            .where(m.SourceRun.run_id == run_id)
            .values(credits_consumed=bad)
        )
    assert isinstance(refused.value.orig, CreditValueError)


# ------------------------------------------------------------ migration 0008, both ways


_ZEROED = (
    "leads_found",
    "contributions_written",
    "throttle_waits",
    "retries",
    "http_429_count",
)
_SOURCE_RUN_0007 = sa.table(
    "source_run",
    sa.column("id", sa.Uuid),
    sa.column("run_id", sa.Uuid),
    sa.column("source_name", sa.String),
    sa.column("resolved_mode", sa.String),
    sa.column("credits_consumed", sa.Integer),
    *(sa.column(name, sa.Integer) for name in _ZEROED),
)
_RUN_0007 = sa.table(
    "ingestion_run",
    sa.column("id", sa.Uuid),
    sa.column("started_at", sa.DateTime(timezone=True)),
    sa.column("status", sa.String),
)


def _credits_at_0007(blank: Backend) -> dict[str, int | None]:
    with blank.engine.connect() as conn:
        return dict(
            conn.execute(
                sa.select(
                    _SOURCE_RUN_0007.c.source_name, _SOURCE_RUN_0007.c.credits_consumed
                )
            ).all()
        )


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_migration_0008_keeps_every_credit_figure_both_ways(blank: Backend) -> None:
    _alembic(blank, "upgrade", "0007")
    run_id = uuid.uuid4()
    with blank.engine.begin() as conn:
        conn.execute(
            sa.insert(_RUN_0007).values(
                id=run_id, started_at=sa.func.now(), status="completed"
            )
        )
        conn.execute(
            sa.insert(_SOURCE_RUN_0007),
            [
                {
                    "id": uuid.uuid4(),
                    "run_id": run_id,
                    "source_name": name,
                    "resolved_mode": "live",
                    "credits_consumed": credits,
                    **dict.fromkeys(_ZEROED, 0),
                }
                for name, credits in (("alpha", 3), ("bravo", None), ("charlie", 0))
            ],
        )

    _alembic(blank, "upgrade", "head")
    with Session(blank.engine) as s, s.begin():
        rows = {r.source_name: r for r in s.scalars(sa.select(m.SourceRun))}
        assert {n: r.credits_consumed for n, r in rows.items()} == {
            "alpha": Decimal(3),
            "bravo": None,
            "charlie": Decimal(0),
        }
        assert isinstance(rows["alpha"].credits_consumed, Decimal)
        rows["charlie"].credits_consumed = Decimal("1.5")
    with Session(blank.engine) as s:
        assert s.scalars(
            sa.select(m.SourceRun.credits_consumed).where(
                m.SourceRun.source_name == "charlie"
            )
        ).one() == Decimal("1.5")

    # Down: whole Credits are kept; a fraction rounds UP (never under-reports spend).
    _alembic(blank, "downgrade", "0007")
    assert _credits_at_0007(blank) == {"alpha": 3, "bravo": None, "charlie": 2}

    _alembic(blank, "upgrade", "head")
    with Session(blank.engine) as s:
        assert {
            r.source_name: r.credits_consumed for r in s.scalars(sa.select(m.SourceRun))
        } == {"alpha": Decimal(3), "bravo": None, "charlie": Decimal(2)}


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_a_total_past_32_bits_of_milli_credits_is_stored_and_reported_exactly(
    backend: Backend,
) -> None:
    """Each figure is within the per-figure ceiling; their sum is not, and the
    BIGINT column (0010) keeps it exact on both engines rather than overflowing."""
    each = Decimal(MAX_MILLI) / 1000  # the largest one figure: 2147483.647
    figures = [
        dataclasses.replace(result("alpha", phase=p), credits_consumed=each)
        for p in (Phase.DISCOVERY, Phase.ENRICHMENT)
    ]
    counts = build_source_counts(tuple(figures))
    total = each * 2
    assert counts[0].credits_consumed == total
    assert total * 1000 > 2**31 - 1
    run_id = uuid.uuid4()
    with Session(backend.engine) as s, s.begin():
        s.add(m.IngestionRun(id=run_id, started_at=sa.func.now(), status="running"))
        s.add(m.SourceRun(run_id=run_id, source_name="alpha", resolved_mode="live"))
        s.flush()
        RunRecordRepository(s).record_source_counts(run_id, counts)
    row = source_row(backend)
    assert (type(row.credits_consumed), row.credits_consumed) == (Decimal, total)
    with Session(backend.engine) as s:
        assert f"credits={total} " in render_run_report(build_run_report(s, run_id))


_MILLI_COLUMN = sa.table(
    "source_run", sa.column("id", sa.Uuid), sa.column("credits_consumed_milli")
)


def _milli_column_type(blank: Backend) -> Any:
    (column,) = (
        c
        for c in sa.inspect(blank.engine).get_columns("source_run")
        if c["name"] == "credits_consumed_milli"
    )
    return column["type"]


# Verifies: specs/lead-source-adapters/requirements.md#21.2
def test_migration_0010_widens_the_milli_credits_and_reverses(blank: Backend) -> None:
    _alembic(blank, "upgrade", "0009")
    run_id, source_id = uuid.uuid4(), uuid.uuid4()
    with Session(blank.engine) as s, s.begin():
        s.add(m.IngestionRun(id=run_id, started_at=sa.func.now(), status="running"))
        s.add(
            m.SourceRun(
                id=source_id,
                run_id=run_id,
                source_name="alpha",
                resolved_mode="live",
                credits_consumed=Decimal("1.5"),
            )
        )
    assert not isinstance(_milli_column_type(blank), sa.BigInteger)

    _alembic(blank, "upgrade", "head")
    assert isinstance(_milli_column_type(blank), sa.BigInteger)
    assert source_row(blank).credits_consumed == Decimal("1.5")  # kept exactly
    past_32_bits = Decimal(2**40) / 1000
    with Session(blank.engine) as s, s.begin():
        s.get_one(m.SourceRun, source_id).credits_consumed = past_32_bits
    assert source_row(blank).credits_consumed == past_32_bits

    with blank.engine.begin() as conn:  # down to 32 bits: a figure that fits
        conn.execute(sa.update(_MILLI_COLUMN).values(credits_consumed_milli=1500))
    _alembic(blank, "downgrade", "0009")
    assert not isinstance(_milli_column_type(blank), sa.BigInteger)
    assert source_row(blank).credits_consumed == Decimal("1.5")
    _alembic(blank, "upgrade", "head")
    assert isinstance(_milli_column_type(blank), sa.BigInteger)


# --------------------------------------------------- the spend commits on its own first


# Verifies: specs/lead-source-adapters/requirements.md#21.2
# Verifies: specs/lead-source-adapters/requirements.md#8.12
async def test_a_failed_contribution_write_keeps_an_accurate_spend_record(
    composed: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("contribution write broke near ada@example.com")

    monkeypatch.setattr(merged_leads, "write_contribution", broken)
    with pytest.raises(RuntimeError):
        await run_ingestion(registry=registry_of(_paid()))

    with Session(composed.engine) as s:
        run = s.scalars(sa.select(m.IngestionRun)).one()
    assert (run.status, run.failure_reason) == ("aborted", "observe: RuntimeError")
    row = source_row(composed)
    assert (row.attempted, row.succeeded, row.failed) == (1, 1, 0)
    assert (row.records_fetched, row.credits_consumed) == (1, Decimal("1.5"))
    assert row.contributions_written == 0
    assert count(composed.engine, m.SourceContribution) == 0


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_the_spend_written_again_is_not_counted_twice(
    composed: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retried store writes the same spend: the figures are set, never added."""
    real = RunRecordRepository.record_source_counts

    def twice(self: RunRecordRepository, run_id: uuid.UUID, counts: Any) -> None:
        real(self, run_id, counts)
        real(self, run_id, counts)

    monkeypatch.setattr(RunRecordRepository, "record_source_counts", twice)
    await run_ingestion(registry=registry_of(_paid()))
    row = source_row(composed)
    assert (row.attempted, row.credits_consumed) == (1, Decimal("1.5"))
    assert row.contributions_written == 1


class _ClockBrokeError(Exception):
    """A failure after the sources ran, before anything was stored."""


# Verifies: specs/lead-source-adapters/requirements.md#21.2
# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_the_spend_commits_before_anything_else_can_fail(
    composed: Backend,
) -> None:
    """The spend is written first: a failure preparing the observations (here the
    run's clock) aborts after it, and the aborted run's report still shows it."""

    def broken_clock() -> Any:
        raise _ClockBrokeError

    with pytest.raises(_ClockBrokeError):
        await run_ingestion(registry=registry_of(_paid()), clock=broken_clock)

    with Session(composed.engine) as s:
        run = s.scalars(sa.select(m.IngestionRun)).one()
        text = render_run_report(build_run_report(s, run.id))
    assert (run.status, run.failure_reason) == ("aborted", "observe: _ClockBrokeError")
    row = source_row(composed)
    assert (row.attempted, row.records_fetched) == (1, 1)
    assert f"credits={SPENT} " in text
