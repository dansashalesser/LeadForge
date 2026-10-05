"""The run record: pure builder and repository (task 18.1, Requirement 21.1)."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import BaseLeadSource, LiveAccess
from leadforge.lead_ingestion.mode_resolution import ModeResolution, resolve_data_mode
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceSettings
from leadforge.lead_ingestion.run_record import (
    RunRecord,
    RunRecordError,
    RunStatus,
    build_run_record,
)
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.run_records import RunRecordRepository

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=3)
RESOLUTIONS = {
    "alpha": ModeResolution(DataMode.LIVE, "all declared credentials present"),
    "bravo": ModeResolution(DataMode.SYNTHETIC, "missing credentials: BRAVO_API_KEY"),
}
SETTINGS = {
    "alpha": SourceSettings(trust_rank=2),
    "bravo": SourceSettings(mode=DataMode.SYNTHETIC, live_access=LiveAccess.GATED),
}


def build(**kw: object) -> RunRecord:
    args: dict[str, object] = {
        "started_at": NOW,
        "max_concurrent_sources": 4,
        "run_timeout_s": 600.0,
        "global_mode": None,
    }
    args.update(kw)
    return build_run_record(RESOLUTIONS, SETTINGS, **args)  # type: ignore[arg-type]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
    with Session(engine) as s:
        yield s


# ------------------------------------------------------------------ the pure builder


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_the_record_lists_every_enabled_source_with_its_mode_and_reason() -> None:
    record = build()
    assert [(s.source_name, s.mode, s.reason) for s in record.sources] == [
        ("alpha", DataMode.LIVE, "all declared credentials present"),
        ("bravo", DataMode.SYNTHETIC, "missing credentials: BRAVO_API_KEY"),
    ]
    assert record.started_at == NOW
    assert record.pool_size == 4


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_the_snapshot_holds_the_run_configuration_and_only_names_and_settings() -> None:
    snapshot = build(global_mode=DataMode.SYNTHETIC).config_snapshot
    assert snapshot == {
        "max_concurrent_sources": 4,
        "run_timeout_s": 600.0,
        "global_mode": "synthetic",
        "sources": {
            "alpha": {
                "trust_rank": 2,
                "mode_override": None,
                "live_access_override": None,
            },
            "bravo": {
                "trust_rank": SETTINGS["bravo"].trust_rank,
                "mode_override": "synthetic",
                "live_access_override": "gated",
            },
        },
    }


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_a_reason_longer_than_its_column_is_cut_not_refused() -> None:
    long = {
        "x": ModeResolution(DataMode.SYNTHETIC, "missing credentials: " + "A" * 400)
    }
    record = build_run_record(
        long,
        {"x": SourceSettings()},
        started_at=NOW,
        max_concurrent_sources=1,
        run_timeout_s=1.0,
        global_mode=None,
    )
    reason = record.sources[0].reason
    assert len(reason) == 255
    assert reason.startswith("missing credentials: AAAA")
    assert reason.endswith("…")  # the cut is visible, not silent


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_a_reason_at_the_limit_is_kept_whole_and_a_multibyte_cut_is_whole_chars() -> (
    None
):
    exact = "é" * 255
    wide = "日" * 300
    record = build_run_record(
        {
            "x": ModeResolution(DataMode.SYNTHETIC, exact),
            "y": ModeResolution(DataMode.SYNTHETIC, wide),
        },
        {"x": SourceSettings(), "y": SourceSettings()},
        started_at=NOW,
        max_concurrent_sources=1,
        run_timeout_s=1.0,
        global_mode=None,
    )
    x, y = record.sources
    assert x.reason == exact
    assert y.reason == "日" * 254 + "…"
    y.reason.encode("utf-8")  # a whole string, no half character


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_sources_are_listed_by_name_whatever_the_input_order() -> None:
    reverse = dict(reversed(list(RESOLUTIONS.items())))
    record = build_run_record(
        reverse,
        SETTINGS,
        started_at=NOW,
        max_concurrent_sources=4,
        run_timeout_s=1.0,
        global_mode=None,
    )
    assert [s.source_name for s in record.sources] == ["alpha", "bravo"]
    assert list(record.config_snapshot["sources"]) == ["alpha", "bravo"]


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_a_mode_that_is_not_a_data_mode_is_refused() -> None:
    bad = {"alpha": ModeResolution("sandbox", "r")}  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="alpha"):
        build_run_record(
            bad,
            SETTINGS,
            started_at=NOW,
            max_concurrent_sources=4,
            run_timeout_s=1.0,
            global_mode=None,
        )


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_an_empty_run_is_a_valid_record() -> None:
    record = build_run_record(
        {},
        {},
        started_at=NOW,
        max_concurrent_sources=4,
        run_timeout_s=1.0,
        global_mode=None,
    )
    assert record.sources == ()
    assert record.config_snapshot["sources"] == {}


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_the_builder_does_not_mutate_its_inputs() -> None:
    resolutions = dict(RESOLUTIONS)
    settings = dict(SETTINGS)
    build_run_record(
        resolutions,
        settings,
        started_at=NOW,
        max_concurrent_sources=4,
        run_timeout_s=1.0,
        global_mode=None,
    )
    assert resolutions == RESOLUTIONS
    assert settings == SETTINGS


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_a_naive_start_time_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        build(started_at=datetime(2026, 10, 5, 12, 0))


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_a_source_without_settings_is_refused() -> None:
    with pytest.raises(ValueError, match="alpha"):
        build_run_record(
            RESOLUTIONS,
            {"bravo": SETTINGS["bravo"]},
            started_at=NOW,
            max_concurrent_sources=4,
            run_timeout_s=1.0,
            global_mode=None,
        )


# ------------------------------------------------------------------ the repository


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_start_persists_one_running_record_with_every_source(session: Session) -> None:
    repo = RunRecordRepository(session)
    run_id = repo.start(build())
    session.commit()
    session.expire_all()

    assert isinstance(run_id, uuid.UUID)
    stored = RunRecordRepository(session).get(run_id)
    assert stored is not None
    assert stored.run_id == run_id
    assert stored.status == RunStatus.RUNNING
    assert stored.started_at == NOW
    assert stored.finished_at is None
    assert stored.exit_code is None
    assert stored.pool_size == 4
    assert stored.config_snapshot == build().config_snapshot
    assert [(s.source_name, s.mode, s.reason) for s in stored.sources] == [
        ("alpha", DataMode.LIVE, "all declared credentials present"),
        ("bravo", DataMode.SYNTHETIC, "missing credentials: BRAVO_API_KEY"),
    ]
    assert session.scalar(sa.select(sa.func.count()).select_from(m.IngestionRun)) == 1
    # the run id is the foreign key 18.2 and 18.3 will use
    rows = session.scalars(sa.select(m.SourceRun)).all()
    assert {r.run_id for r in rows} == {run_id}


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_the_start_instant_is_stored_in_utc_whatever_the_offset(
    session: Session,
) -> None:
    plus_two = NOW.astimezone(timezone(timedelta(hours=2)))
    run_id = RunRecordRepository(session).start(build(started_at=plus_two))
    session.commit()
    session.expire_all()
    stored = RunRecordRepository(session).get(run_id)
    assert stored is not None
    assert stored.started_at == NOW
    assert stored.started_at.utcoffset() == timedelta(0)


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_get_of_an_unknown_run_is_none(session: Session) -> None:
    assert RunRecordRepository(session).get(uuid.uuid4()) is None


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_finish_completes_the_record_and_touches_nothing_else(
    session: Session,
) -> None:
    repo = RunRecordRepository(session)
    run_id = repo.start(build())
    session.commit()
    repo.finish(run_id, status=RunStatus.COMPLETED, exit_code=1, finished_at=LATER)
    session.commit()
    session.expire_all()

    stored = repo.get(run_id)
    assert stored is not None
    assert stored.status == RunStatus.COMPLETED
    assert stored.exit_code == 1
    assert stored.finished_at == LATER
    assert stored.started_at == NOW
    assert stored.pool_size == 4
    assert stored.config_snapshot == build().config_snapshot
    assert len(stored.sources) == 2


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_an_aborted_run_is_marked_aborted_without_an_exit_code(
    session: Session,
) -> None:
    repo = RunRecordRepository(session)
    run_id = repo.start(build())
    repo.finish(run_id, status=RunStatus.ABORTED, exit_code=None, finished_at=LATER)
    session.commit()
    session.expire_all()
    stored = repo.get(run_id)
    assert stored is not None
    assert stored.status == RunStatus.ABORTED
    assert stored.exit_code is None
    assert stored.finished_at == LATER
    assert len(stored.sources) == 2  # the modes survive the abort


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_a_run_is_finished_once_only(session: Session) -> None:
    repo = RunRecordRepository(session)
    run_id = repo.start(build())
    repo.finish(run_id, status=RunStatus.ABORTED, exit_code=None, finished_at=LATER)
    with pytest.raises(RunRecordError, match="already finished"):
        repo.finish(run_id, status=RunStatus.COMPLETED, exit_code=0, finished_at=LATER)
    stored = repo.get(run_id)
    assert stored is not None
    assert stored.status == RunStatus.ABORTED


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_finishing_an_unknown_run_is_refused(session: Session) -> None:
    with pytest.raises(RunRecordError, match="unknown run"):
        RunRecordRepository(session).finish(
            uuid.uuid4(), status=RunStatus.ABORTED, exit_code=None, finished_at=LATER
        )


# Verifies: specs/lead-source-adapters/requirements.md#21.1
@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        (RunStatus.RUNNING, None),  # finish is not a start
        (RunStatus.COMPLETED, None),  # a completed run has an exit code
        (RunStatus.ABORTED, 1),  # an aborted run never reached one
    ],
)
def test_finish_refuses_an_inconsistent_status_and_exit_code(
    session: Session, status: RunStatus, exit_code: int | None
) -> None:
    repo = RunRecordRepository(session)
    run_id = repo.start(build())
    with pytest.raises(RunRecordError):
        repo.finish(run_id, status=status, exit_code=exit_code, finished_at=LATER)
    stored = repo.get(run_id)
    assert stored is not None
    assert stored.status == RunStatus.RUNNING


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_no_credential_value_reaches_the_record_through_a_real_resolution(
    session: Session,
) -> None:
    secret = "s3cr3t-key-value-0123456789"

    class Fake(BaseLeadSource):
        name = "fake"
        required_env = ("FAKE_KEY", "FAKE_OTHER")

    resolution = resolve_data_mode(
        Fake,  # type: ignore[type-abstract]
        SourceSettings(),
        {"FAKE_KEY": secret},
    )
    record = build_run_record(
        {"fake": resolution},
        {"fake": SourceSettings()},
        started_at=NOW,
        max_concurrent_sources=1,
        run_timeout_s=1.0,
        global_mode=None,
    )
    run_id = RunRecordRepository(session).start(record)
    session.commit()
    dumped = repr(RunRecordRepository(session).get(run_id))
    assert secret not in dumped
    assert "FAKE_OTHER" in dumped


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_a_stale_view_cannot_finish_a_run_that_another_session_finished(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
    with engine.begin() as conn:
        upgrade_to_head(conn)
    with Session(engine) as first, Session(engine) as second:
        run_id = RunRecordRepository(first).start(build())
        first.commit()
        held = first.get(m.IngestionRun, run_id)  # first now holds a 'running' view
        assert held is not None
        assert held.status == "running"
        RunRecordRepository(second).finish(
            run_id, status=RunStatus.ABORTED, exit_code=None, finished_at=LATER
        )
        second.commit()
        with pytest.raises(RunRecordError, match="already finished"):
            RunRecordRepository(first).finish(
                run_id, status=RunStatus.COMPLETED, exit_code=0, finished_at=LATER
            )
        first.rollback()
        stored = RunRecordRepository(first).get(run_id)
        assert stored is not None
        assert (stored.status, stored.exit_code) == (RunStatus.ABORTED, None)
    engine.dispose()


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_a_naive_finish_time_is_refused(session: Session) -> None:
    repo = RunRecordRepository(session)
    run_id = repo.start(build())
    with pytest.raises(RunRecordError, match="timezone"):
        repo.finish(
            run_id,
            status=RunStatus.ABORTED,
            exit_code=None,
            finished_at=datetime(2026, 10, 5, 12, 3),
        )
    stored = repo.get(run_id)
    assert stored is not None
    assert stored.status == RunStatus.RUNNING
