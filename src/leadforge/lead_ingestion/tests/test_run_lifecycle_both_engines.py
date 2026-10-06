"""Run lifecycle and persisted per-source counts on SQLite and PostgreSQL (follow-up).

Open items of the run record (choices.md, follow-up 2026-10-06):

* a run is ``completed`` only once every write of the run has committed: the merge
  write and the completion share one transaction, and a failure after the sources ran
  marks the run ``aborted`` with a PII-free reason (stage and exception class only);
* per-source ``attempted``, ``succeeded``, ``failed``, records fetched, Credits and
  ``contributions_written`` are persisted (migration 0005) and the report reads them
  back from the database.

The engine fixtures are those of ``test_persistence_both_engines`` (the Postgres leg is
a required gate there, never a skip); importing them is how pytest shares them without
a conftest, hence the file-wide F811 waiver.
"""

# ruff: noqa: F811

import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion import ingest_runner
from leadforge.lead_ingestion.match_key_digest import MATCH_KEY_SECRET_ENV
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.run_record import (
    RunRecord,
    RunRecordError,
    RunStatus,
    SourceCounts,
    SourceMode,
)
from leadforge.lead_ingestion.run_report import build_run_report, render_run_report
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.contributions import write_contribution
from leadforge.lead_ingestion.store.merged_leads import persist_merge
from leadforge.lead_ingestion.store.raw_responses import (
    RawResponseRepository,
    RetentionPolicy,
)
from leadforge.lead_ingestion.store.run_records import RunRecordRepository
from leadforge.lead_ingestion.store.transactions import StoreWriter
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    NOW,
    T0,
    Backend,
    _alembic,
    _contribution,
    backend,
    blank,
    postgres_url,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PROFILE = REPO_ROOT / "config" / "target_profile.yaml"
SENTINEL_EMAIL = "lifecycle-sentinel@example.com"


def _record() -> RunRecord:
    return RunRecord(
        T0,
        2,
        {},
        (
            SourceMode("alpha", DataMode.LIVE, "r", live_access=True),
            SourceMode("bravo", DataMode.SYNTHETIC, "r", live_access=False),
        ),
    )


async def _started(backend: Backend) -> tuple[StoreWriter, uuid.UUID]:
    writer = StoreWriter(backend.engine)
    run_id = await writer.write_batch(lambda s: RunRecordRepository(s).start(_record()))
    return writer, run_id


def _rows(backend: Backend) -> dict[str, m.SourceRun]:
    with Session(backend.engine) as s:
        return {r.source_name: r for r in s.scalars(sa.select(m.SourceRun))}


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_an_aborted_run_keeps_a_pii_free_reason_on_each_engine(
    backend: Backend,
) -> None:
    writer, run_id = await _started(backend)

    await writer.write_batch(
        lambda s: RunRecordRepository(s).finish(
            run_id,
            status=RunStatus.ABORTED,
            exit_code=None,
            finished_at=NOW,
            reason="merge_write: RuntimeError",
        )
    )

    stored = await writer.write_batch(lambda s: RunRecordRepository(s).get(run_id))
    assert stored is not None
    assert (stored.status, stored.exit_code) == ("aborted", None)
    assert stored.failure_reason == "merge_write: RuntimeError"
    with Session(backend.engine) as s:
        text = render_run_report(build_run_report(s, run_id))
    assert "abort reason: merge_write: RuntimeError" in text.splitlines()


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_completed_run_cannot_carry_an_abort_reason(backend: Backend) -> None:
    writer, run_id = await _started(backend)

    with pytest.raises(RunRecordError):
        await writer.write_batch(
            lambda s: RunRecordRepository(s).finish(
                run_id,
                status=RunStatus.COMPLETED,
                exit_code=0,
                finished_at=NOW,
                reason="x",
            )
        )


# Verifies: specs/lead-source-adapters/requirements.md#21.2
# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_call_counts_fetched_and_credits_commit_with_the_finish(
    backend: Backend,
) -> None:
    writer, run_id = await _started(backend)

    def finish(session: Session) -> None:
        repo = RunRecordRepository(session)
        repo.finish(run_id, status=RunStatus.COMPLETED, exit_code=0, finished_at=NOW)
        repo.record_source_counts(
            run_id,
            (
                SourceCounts(
                    "alpha",
                    "transient",
                    4,
                    2,
                    1,
                    3,
                    None,
                    None,
                    attempted=5,
                    succeeded=1,
                    failed=2,
                    records_fetched=7,
                    credits_consumed=3,
                ),
                SourceCounts("bravo", None, 0, 0, 0, 0, None, None),
            ),
        )

    await writer.write_batch(finish)

    rows = _rows(backend)
    a, b = rows["alpha"], rows["bravo"]
    assert (a.attempted, a.succeeded, a.failed) == (5, 1, 2)
    assert (a.records_fetched, a.credits_consumed) == (7, 3)
    assert (b.attempted, b.succeeded, b.failed) == (0, 0, 0)
    assert (b.records_fetched, b.credits_consumed) == (None, None)
    with Session(backend.engine) as s:
        report = build_run_report(s, run_id)
    alpha = report.sources[0]
    assert (alpha.attempted, alpha.succeeded, alpha.failed) == (5, 1, 2)
    assert (alpha.records_fetched, alpha.credits_consumed) == (7, 3)
    lines = render_run_report(report).splitlines()
    assert "  calls: attempted=5 succeeded=1 failed=2" in lines
    assert any("fetched=7 " in line and line.startswith("  failure=") for line in lines)
    assert any("credits=3 " in line for line in lines)
    assert any(
        line.startswith("  failure=") and "fetched=not recorded" in line
        for line in lines
    )


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_contributions_written_is_counted_from_the_stored_rows(
    backend: Backend,
) -> None:
    writer, run_id = await _started(backend)

    def store_two(session: Session) -> None:
        alpha = session.scalars(
            sa.select(m.SourceRun).where(
                m.SourceRun.run_id == run_id, m.SourceRun.source_name == "alpha"
            )
        ).one()
        raw = RawResponseRepository.add(
            session,
            source_run_id=alpha.id,
            endpoint_key="people",
            request_fingerprint="fp",
            payload={"hits": []},
            fetched_at=T0,
            mode=DataMode.LIVE,
            policy=RetentionPolicy(),
        )
        for value in ("a@example.test", "b@example.test"):
            write_contribution(
                session,
                _contribution({"person.email": value}),
                source_run_id=alpha.id,
                raw_response_id=raw,
                data_mode=DataMode.LIVE,
                fetched_at=T0,
                lead_scope="person",
            )

    await writer.write_batch(store_two)

    def finish(session: Session) -> None:
        repo = RunRecordRepository(session)
        repo.finish(run_id, status=RunStatus.COMPLETED, exit_code=0, finished_at=NOW)
        repo.record_contributions_written(run_id)

    await writer.write_batch(finish)

    rows = _rows(backend)
    written = {name: row.contributions_written for name, row in rows.items()}
    assert written == {"alpha": 2, "bravo": 0}
    with Session(backend.engine) as s:
        text = render_run_report(build_run_report(s, run_id))
    assert any("contributions_written=2" in line for line in text.splitlines())


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_0005_walks_up_and_down_keeping_run_rows(blank: Backend) -> None:
    _alembic(blank, "upgrade", "0004")
    run_id = uuid.uuid4()
    with blank.engine.begin() as conn:
        # Only the columns 0004 has are rendered.
        conn.execute(
            sa.insert(m.IngestionRun).values(
                id=run_id, started_at=T0, status="completed"
            )
        )
    _alembic(blank, "upgrade", "0005")
    with Session(blank.engine) as s:
        run = s.get_one(m.IngestionRun, run_id)
        assert run.failure_reason is None
    _alembic(blank, "downgrade", "0004")
    with blank.engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM ingestion_run")).scalar() == 1
        columns = {c["name"] for c in sa.inspect(conn).get_columns("source_run")}
        run_columns = {c["name"] for c in sa.inspect(conn).get_columns("ingestion_run")}
    assert not {"attempted", "succeeded", "failed", "records_fetched"} & columns
    assert (
        not {
            "failure_reason",
            "projection_version",
            "projection_fingerprint",
            "primary_domain_ties_flagged",
        }
        & run_columns
    )


# ------------------------------------------------------- the composed run, both engines


@pytest.fixture
def composed(
    backend: Backend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Backend:
    """``run_ingestion`` over the engine under test, empty environment."""
    discovered = SourceRegistry.discover()
    for name in discovered.names():
        for variable in discovered.source_class(name).required_env:
            monkeypatch.delenv(variable, raising=False)
    for variable in ("LEADFORGE_MODE", "LEADFORGE_ENV_FILE", MATCH_KEY_SECRET_ENV):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "DATABASE_URL", backend.url.render_as_string(hide_password=False)
    )
    return backend


def _only_run(backend: Backend) -> m.IngestionRun:
    with Session(backend.engine) as s:
        return s.scalars(sa.select(m.IngestionRun)).one()


def _count(backend: Backend, model: Any) -> int:
    with Session(backend.engine) as s:
        return s.scalar(sa.select(sa.func.count()).select_from(model)) or 0


# Verifies: specs/lead-source-adapters/requirements.md#21.1
# Verifies: specs/lead-source-adapters/requirements.md#6.4
async def test_a_merge_write_failure_marks_the_run_aborted_on_each_engine(
    composed: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = persist_merge

    def write_then_fail(session: Session, **kwargs: Any) -> Any:
        real(session, **kwargs)  # the rows are written, then the write fails
        raise RuntimeError(f"disk full near {SENTINEL_EMAIL}")

    monkeypatch.setattr(ingest_runner, "persist_merge", write_then_fail)

    with pytest.raises(RuntimeError):
        await ingest_runner.run_ingestion(target_profile_path=PROFILE)

    run = _only_run(composed)
    assert (run.status, run.exit_code) == ("aborted", None)
    assert run.finished_at is not None
    assert run.failure_reason == "merge_write: RuntimeError"
    # The merge and the completion are one transaction: nothing of it survived.
    assert _count(composed, m.CanonicalLeadRow) == 0
    assert _count(composed, m.SourceContribution) == 0


# Verifies: specs/lead-source-adapters/requirements.md#21.1
# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_a_composed_run_completes_only_with_its_merge_and_counts(
    composed: Backend,
) -> None:
    outcome = await ingest_runner.run_ingestion(target_profile_path=PROFILE)

    run = _only_run(composed)
    assert (run.status, run.exit_code, run.failure_reason) == ("completed", 0, None)
    rows = _rows(composed)
    by_source = {r.source_name: r.outcome for r in outcome.results}
    for name, row in rows.items():
        if name not in by_source:
            continue
        counts = by_source[name]
        assert (row.attempted, row.succeeded, row.failed) == (
            counts.attempted,
            counts.succeeded,
            counts.failed,
        ), name
    assert sum(r.contributions_written for r in rows.values()) == (
        outcome.stored.contributions
    )
    assert outcome.stored.contributions > 0
    assert "calls: attempted=" in outcome.report_text


# Verifies: specs/lead-source-adapters/requirements.md#8.13
# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_the_projection_version_is_stamped_and_bumped_on_each_engine(
    composed: Backend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = await ingest_runner.run_ingestion(target_profile_path=PROFILE)
    again = await ingest_runner.run_ingestion(target_profile_path=PROFILE)
    monkeypatch.setenv(MATCH_KEY_SECRET_ENV, "lifecycle-stable-secret-" + "s" * 32)
    exclusions = tmp_path / "exclusions.yaml"
    exclusions.write_text("emails:\n  - nobody@example.org\n", encoding="utf-8")
    bumped = await ingest_runner.run_ingestion(
        target_profile_path=PROFILE, exclusions_path=exclusions
    )

    with Session(composed.engine) as s:
        runs = {r.id: r for r in s.scalars(sa.select(m.IngestionRun))}
        versions = {
            v
            for v in s.scalars(
                sa.select(m.CanonicalLeadRow.projection_version).join(
                    m.LeadIdentity,
                    m.LeadIdentity.id == m.CanonicalLeadRow.lead_identity_id,
                )
            )
        }
        stale_rows = s.scalar(
            sa.select(sa.func.count(m.CanonicalLeadRow.id)).where(
                m.CanonicalLeadRow.projection_version != 2
            )
        )
    assert [runs[o.run_id].projection_version for o in (first, again, bumped)] == [
        1,
        1,
        2,
    ]
    fingerprints = [runs[o.run_id].projection_fingerprint for o in (first, again)]
    assert fingerprints[0] == fingerprints[1]
    assert len(fingerprints[0] or "") == 64
    assert runs[bumped.run_id].projection_fingerprint != fingerprints[0]
    assert versions == {1, 2}
    assert runs[first.run_id].primary_domain_ties_flagged == 0
    lines = bumped.report_text.splitlines()
    assert "projection version: 2" in lines
    assert f"stale projections: {stale_rows}" in lines
    assert (stale_rows or 0) > 0
    assert "primary-domain tie fallbacks: 0 flagged" in lines


# ------------------------------------------- a failure at each write step (self-review)


class _MergeBrokeError(Exception):
    """A merge failure the tests raise (its text is never stored)."""


_WRITE_STEPS = (
    "record_projection",
    "finish",
    "record_source_counts",
    "record_contributions_written",
)


# Verifies: specs/lead-source-adapters/requirements.md#21.1
# Verifies: specs/lead-source-adapters/requirements.md#21.2
@pytest.mark.parametrize("step", _WRITE_STEPS)
async def test_a_failure_at_any_completing_write_rolls_the_whole_merge_back(
    composed: Backend, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    real = getattr(RunRecordRepository, step)

    def fail_after(self: RunRecordRepository, *args: Any, **kwargs: Any) -> Any:
        if step == "finish" and kwargs.get("status") is not RunStatus.COMPLETED:
            return real(self, *args, **kwargs)  # the abort marker itself still works
        real(self, *args, **kwargs)  # the step's rows are written, then it fails
        raise RuntimeError(f"lost near {SENTINEL_EMAIL}")

    monkeypatch.setattr(RunRecordRepository, step, fail_after)

    with pytest.raises(RuntimeError):
        await ingest_runner.run_ingestion(target_profile_path=PROFILE)

    run = _only_run(composed)
    assert (run.status, run.exit_code) == ("aborted", None)
    assert run.failure_reason == "merge_write: RuntimeError"
    assert run.projection_version is None
    assert _count(composed, m.CanonicalLeadRow) == 0
    assert _count(composed, m.SourceContribution) == 0
    for name, row in _rows(composed).items():
        assert (row.attempted, row.contributions_written) == (None, 0), name


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_merge_failure_before_the_write_is_aborted_as_merge(
    composed: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: Any, **__: Any) -> Any:
        raise _MergeBrokeError(SENTINEL_EMAIL)

    monkeypatch.setattr(ingest_runner, "cluster_contributions", fail)

    with pytest.raises(_MergeBrokeError):
        await ingest_runner.run_ingestion(target_profile_path=PROFILE)

    run = _only_run(composed)
    assert (run.status, run.failure_reason) == ("aborted", "merge: _MergeBrokeError")


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_failing_abort_marker_never_hides_the_original_error(
    composed: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_merge(*_: Any, **__: Any) -> Any:
        raise _MergeBrokeError("merge broke")

    real_finish = RunRecordRepository.finish

    def no_abort(self: RunRecordRepository, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("status") is RunStatus.ABORTED:
            raise sa.exc.OperationalError("UPDATE", {}, Exception("store gone"))
        real_finish(self, *args, **kwargs)

    monkeypatch.setattr(ingest_runner, "cluster_contributions", fail_merge)
    monkeypatch.setattr(RunRecordRepository, "finish", no_abort)

    with pytest.raises(_MergeBrokeError) as caught:
        await ingest_runner.run_ingestion(target_profile_path=PROFILE)

    assert any("could not be marked aborted" in n for n in caught.value.__notes__)
    # The marker's own transaction rolled back: the record is left running (a crash).
    assert _only_run(composed).status == "running"
