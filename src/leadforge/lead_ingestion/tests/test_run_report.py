"""The run report is a query over the store, never orchestrator memory (task 18.3).

Requirement 21.5. Runs are made by the real orchestrator against a real SQLite store;
the report is then built from a fresh engine connection with the orchestrator gone.
"""

import inspect
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import LiveAccess
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.errors import (
    SourceComplianceRestricted,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceSettings
from leadforge.lead_ingestion.run_record import (
    RunRecord,
    RunRecordError,
    RunStatus,
    SourceMode,
)
from leadforge.lead_ingestion.run_report import (
    RunNotFoundError,
    build_run_report,
    render_run_report,
)
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.run_records import RunRecordRepository
from leadforge.lead_ingestion.store.transactions import StoreWriter
from leadforge.lead_ingestion.tests.test_run_source_counts import (
    CANARY,
    REQUEST,
    T0,
    Plan,
    make,
    raiser,
)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    with engine.begin() as conn:
        upgrade_to_head(conn)
    yield engine
    engine.dispose()


def report_text(engine: Engine, run_id: uuid.UUID | None = None) -> str:
    with Session(engine) as session:
        return render_run_report(build_run_report(session, run_id))


def only_run_id(engine: Engine) -> uuid.UUID:
    with Session(engine) as s:
        return s.scalars(sa.select(m.IngestionRun.id)).one()


async def start(engine: Engine, record: RunRecord) -> uuid.UUID:
    return await StoreWriter(engine).write_batch(
        lambda s: RunRecordRepository(s).start(record)
    )


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_the_report_of_a_real_run_comes_from_a_fresh_connection_after_it(
    tmp_path: Path, engine: Engine
) -> None:
    plan = Plan()
    plan.leads = {"alpha": 3}
    plan.fetch = {"bravo": raiser(SourceUnauthorized("bravo", endpoint="/x"))}
    await make(
        engine,
        plan,
        classes={"alpha": {"live_access": LiveAccess.AVAILABLE}},
        settings={"bravo": SourceSettings(live_access=LiveAccess.GATED)},
    ).run(REQUEST)
    url = engine.url
    engine.dispose()  # the orchestrator, its store writer and its engine are gone

    fresh = create_store_engine(url)
    try:
        with Session(fresh) as session:
            report = build_run_report(session)
        text = render_run_report(report)
    finally:
        fresh.dispose()

    assert report.status == "completed"
    assert report.exit_code == 0
    assert report.started_at == T0
    assert [s.source_name for s in report.sources] == ["alpha", "bravo"]
    alpha, bravo = report.sources
    assert (alpha.mode, alpha.reason, alpha.live_access) == (
        DataMode.SYNTHETIC,
        "why",
        "available",
    )
    assert alpha.failure_class is None
    assert alpha.leads_normalized == 3
    assert (bravo.failure_class, bravo.live_access) == ("unauthorized", "gated")
    assert "alpha: mode=synthetic" in text
    assert "failure=unauthorized" in text


# Verifies: specs/lead-source-adapters/requirements.md#21.5
def test_the_report_function_takes_no_orchestrator_or_outcome_argument() -> None:
    params = inspect.signature(build_run_report).parameters
    assert list(params) == ["session", "run_id"]
    assert params["run_id"].default is None
    source = inspect.getsource(inspect.getmodule(build_run_report))  # type: ignore[arg-type]
    assert "lead_ingestion.orchestrator" not in source
    assert "SourceResult" not in source


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_the_latest_run_is_reported_when_no_id_is_given(engine: Engine) -> None:
    older = RunRecord(T0, 2, {}, (SourceMode("alpha", DataMode.SYNTHETIC, "r"),))
    newer = RunRecord(
        T0 + timedelta(hours=1), 2, {}, (SourceMode("bravo", DataMode.SYNTHETIC, "r"),)
    )
    old_id = await start(engine, older)
    new_id = await start(engine, newer)
    with Session(engine) as session:
        assert build_run_report(session).run_id == new_id
        assert build_run_report(session, old_id).run_id == old_id


# Verifies: specs/lead-source-adapters/requirements.md#21.5
def test_an_unknown_run_id_and_an_empty_store_raise_a_named_error(
    engine: Engine,
) -> None:
    with Session(engine) as session:
        with pytest.raises(RunNotFoundError, match="unknown run"):
            build_run_report(session, uuid.uuid4())
        with pytest.raises(RunNotFoundError, match="no run"):
            build_run_report(session)
    assert issubclass(RunNotFoundError, RunRecordError)


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_a_still_running_run_renders_honestly_with_nothing_invented(
    engine: Engine,
) -> None:
    run_id = await start(
        engine, RunRecord(T0, 2, {}, (SourceMode("alpha", DataMode.LIVE, "r"),))
    )
    text = report_text(engine, run_id)
    assert "status: running" in text
    assert "still running or crashed" in text
    assert "finished: not recorded" in text
    assert "exit code: not recorded" in text
    assert "leads_normalized=not recorded" in text
    assert "retries=not recorded" in text


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_an_aborted_run_says_no_per_source_counts_were_recorded(
    engine: Engine,
) -> None:
    run_id = await start(
        engine, RunRecord(T0, 2, {}, (SourceMode("alpha", DataMode.LIVE, "r"),))
    )
    await StoreWriter(engine).write_batch(
        lambda s: RunRecordRepository(s).finish(
            run_id,
            status=RunStatus.ABORTED,
            exit_code=None,
            finished_at=T0 + timedelta(seconds=5),
        )
    )
    text = report_text(engine, run_id)
    assert "status: aborted" in text
    assert "aborted: no per-source counts recorded" in text
    assert "exit code: not recorded" in text
    assert "failure=ok" not in text
    assert "leads_normalized=0" not in text


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_a_completed_source_with_no_failure_is_not_called_ok_outright(
    engine: Engine,
) -> None:
    await make(engine, Plan(), names=("alpha",)).run(REQUEST)
    text = report_text(engine)
    # NULL failure_class is ok OR not run: the report cannot tell, so it says so.
    assert "failure=none recorded" in text
    assert "leads_normalized=0" in text  # a completed run did record its count


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_unrecorded_figures_are_never_rendered_as_zero(engine: Engine) -> None:
    await make(engine, Plan(), names=("alpha",)).run(REQUEST)
    text = report_text(engine)
    assert "fetched=not recorded" in text
    assert "merged=not recorded" in text
    assert "credits=not recorded" in text
    assert "quota=not stated" in text


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_the_leads_figure_is_labelled_as_contributions_not_distinct_leads(
    engine: Engine,
) -> None:
    await make(engine, Plan(), names=("alpha",)).run(REQUEST)
    assert "not distinct leads" in report_text(engine)


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_a_provider_stated_quota_and_the_counters_are_shown(
    engine: Engine,
) -> None:
    run_id = await start(
        engine, RunRecord(T0, 2, {}, (SourceMode("alpha", DataMode.LIVE, "r"),))
    )

    def finish(s: Session) -> None:
        RunRecordRepository(s).finish(
            run_id, status=RunStatus.COMPLETED, exit_code=0, finished_at=T0
        )
        row = s.scalars(sa.select(m.SourceRun)).one()
        row.quota_remaining = {"minute": 7, "day": 40}
        row.credits_consumed = 12
        row.retries, row.throttle_waits, row.http_429_count = 2, 3, 1

    await StoreWriter(engine).write_batch(finish)
    text = report_text(engine, run_id)
    assert "quota=day:40,minute:7" in text
    assert "credits=12" in text
    assert "retries=2 throttle_waits=3 http_429=1" in text


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_the_report_says_which_sources_could_run_live(engine: Engine) -> None:
    await make(
        engine,
        Plan(),
        names=("alpha", "bravo", "charlie"),
        classes={
            "alpha": {"live_access": LiveAccess.AVAILABLE},
            "bravo": {"live_access": LiveAccess.UNAVAILABLE},
            "charlie": {"live_access": LiveAccess.UNAVAILABLE},
        },
        settings={"charlie": SourceSettings(live_access=LiveAccess.GATED)},
    ).run(REQUEST)
    text = report_text(engine)
    assert "could run live: alpha, charlie" in text
    assert "synthetic-only by necessity: bravo" in text


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_a_run_without_a_stored_classification_says_not_recorded(
    engine: Engine,
) -> None:
    run_id = await start(
        engine, RunRecord(T0, 2, {}, (SourceMode("alpha", DataMode.LIVE, "r"),))
    )
    text = report_text(engine, run_id)
    assert "live_access=not recorded" in text
    assert "classification not recorded: alpha" in text


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_sources_are_listed_in_name_order_whatever_the_insert_order(
    engine: Engine,
) -> None:
    run_id = await start(
        engine,
        RunRecord(
            T0,
            2,
            {},
            (
                SourceMode("zulu", DataMode.LIVE, "r"),
                SourceMode("alpha", DataMode.LIVE, "r"),
            ),
        ),
    )
    with Session(engine) as session:
        names = [s.source_name for s in build_run_report(session, run_id).sources]
    assert names == ["alpha", "zulu"]


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_the_rendering_is_deterministic_and_has_no_time_but_recorded_ones(
    engine: Engine,
) -> None:
    await make(engine, Plan()).run(REQUEST)
    first = report_text(engine)
    assert first == report_text(engine)
    assert T0.isoformat() in first
    assert str(datetime.now(UTC).year) not in first.replace(T0.isoformat(), "")


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_control_characters_and_long_text_cannot_break_the_layout(
    engine: Engine,
) -> None:
    hostile = "evil\nname\x1b[2J"
    run_id = await start(
        engine, RunRecord(T0, 2, {}, (SourceMode(hostile, DataMode.LIVE, "a\nb"),))
    )

    def warn(s: Session) -> None:
        row = s.scalars(sa.select(m.SourceRun)).one()
        row.warnings = ["w\nforged line: status: completed " + "x" * 5000]

    await StoreWriter(engine).write_batch(warn)
    text = report_text(engine, run_id)
    assert "\x1b" not in text
    assert "evil\\nname" in text
    assert "reason=a\\nb" in text
    assert "\nforged line" not in text
    assert max(len(line) for line in text.splitlines()) <= 300


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_a_person_named_by_an_error_never_reaches_the_rendered_report(
    engine: Engine,
) -> None:
    plan = Plan()
    plan.fetch = {"alpha": raiser(SourceComplianceRestricted("alpha", subject=CANARY))}
    await make(engine, plan, names=("alpha",)).run(REQUEST)
    text = report_text(engine)
    assert "compliance_restricted" in text
    assert CANARY not in text
    assert "canary" not in text


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_a_source_with_recorded_activity_and_no_failure_class_is_ok(
    engine: Engine,
) -> None:
    plan = Plan()
    plan.leads = {"alpha": 3}
    await make(engine, plan, names=("alpha", "bravo")).run(REQUEST)
    text = report_text(engine)
    # Three leads were normalized, so alpha certainly ran and ended ok; bravo's row
    # (no leads, no failure) cannot be told from a source that never ran.
    assert "alpha: mode=synthetic reason=why live_access=" in text
    alpha_lines = text.split("alpha: mode=")[1].splitlines()
    assert "failure=ok" in alpha_lines[1]
    bravo_lines = text.split("bravo: mode=")[1].splitlines()
    assert "failure=none recorded" in bravo_lines[1]


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_an_unknown_mode_or_classification_never_crashes_the_report(
    engine: Engine,
) -> None:
    run_id = await start(
        engine,
        RunRecord(
            T0,
            2,
            {"sources": {"alpha": {"live_access": "future\nclass"}, "bravo": []}},
            (
                SourceMode("alpha", DataMode.LIVE, "r", live_access=True),
                SourceMode("bravo", DataMode.LIVE, "r"),
            ),
        ),
    )

    def mutate(s: Session) -> None:
        for row in s.scalars(sa.select(m.SourceRun)):
            if row.source_name == "bravo":
                row.resolved_mode = "hybrid"

    await StoreWriter(engine).write_batch(mutate)
    text = report_text(engine, run_id)
    assert "bravo: mode=hybrid" in text
    assert "classification not recorded: alpha, bravo" in text


async def test_a_non_mapping_snapshot_is_tolerated(engine: Engine) -> None:
    run_id = await start(
        engine,
        RunRecord(T0, 2, {}, (SourceMode("alpha", DataMode.LIVE, "r"),)),
    )

    def mutate(s: Session) -> None:
        s.scalars(sa.select(m.IngestionRun)).one().config_snapshot = ["old"]  # type: ignore[assignment]

    await StoreWriter(engine).write_batch(mutate)
    assert "live_access=not recorded" in report_text(engine, run_id)


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_quota_values_are_escaped_too(engine: Engine) -> None:
    run_id = await start(
        engine, RunRecord(T0, 2, {}, (SourceMode("alpha", DataMode.LIVE, "r"),))
    )

    def mutate(s: Session) -> None:
        s.scalars(sa.select(m.SourceRun)).one().quota_remaining = {
            "k\n": "v\nforged: status: completed\x1b[2J"
        }

    await StoreWriter(engine).write_batch(mutate)
    text = report_text(engine, run_id)
    assert "\x1b" not in text
    assert "\nforged" not in text


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_the_report_shows_only_whitelisted_snapshot_fields(
    engine: Engine,
) -> None:
    canary = "canary@example.com sk-CANARYTOKEN /home/secret/path"
    run_id = await start(
        engine,
        RunRecord(
            T0,
            2,
            {
                "api_key": canary,
                "pool": canary,
                "sources": {"alpha": {"live_access": "gated", "note": canary}},
            },
            (SourceMode("alpha", DataMode.LIVE, "reason names VARS only"),),
        ),
    )
    with Session(engine) as session:
        report = build_run_report(session, run_id)
    text = render_run_report(report) + repr(report)
    for needle in ("canary@example.com", "sk-CANARYTOKEN", "/home/secret"):
        assert needle not in text


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_building_a_report_reads_only_and_uses_a_constant_number_of_queries(
    engine: Engine,
) -> None:
    def many(n: int) -> RunRecord:
        return RunRecord(
            T0,
            2,
            {},
            tuple(SourceMode(f"s{i:02}", DataMode.LIVE, "r") for i in range(n)),
        )

    small = await start(engine, many(2))
    big = await start(engine, many(20))
    statements: list[str] = []

    def spy(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    sa.event.listen(engine, "before_cursor_execute", spy)
    try:
        counts = []
        for run_id in (small, big):
            statements.clear()
            with Session(engine) as session:
                build_run_report(session, run_id)
                assert not session.new
                assert not session.dirty
            counts.append(len(statements))
            assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    finally:
        sa.event.remove(engine, "before_cursor_execute", spy)
    assert counts[0] == counts[1] <= 3


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_the_report_names_what_the_run_does_not_persist(engine: Engine) -> None:
    await make(engine, Plan(), names=("alpha",)).run(REQUEST)
    text = report_text(engine)
    assert "over-merge suspects: not recorded" in text
    assert "primary-domain tie fallbacks: not recorded" in text


# Verifies: specs/lead-source-adapters/requirements.md#21.5
def test_the_report_module_does_not_import_the_orchestrator_registry_or_adapters() -> (
    None
):
    import ast

    import leadforge.lead_ingestion.run_report as mod

    tree = ast.parse(inspect.getsource(mod))
    imported = {
        f"{n.module}.{a.name}" if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in n.names
    }
    for name in imported:
        assert not name.endswith(("orchestrator", "registry")), name
        assert "orchestrator." not in name, name
        assert "registry." not in name, name
        assert ".adapters" not in name, name


# Verifies: specs/lead-source-adapters/requirements.md#21.4
@pytest.mark.parametrize(
    ("snapshot", "line"),
    [
        (
            {"match_key_digests": "per_run"},
            "match-key digests: per-run random key, not comparable across runs "
            "(set LEADFORGE_MATCH_KEY_SECRET)",
        ),
        (
            {"match_key_digests": "keyed"},
            "match-key digests: keyed, comparable across runs with the same secret",
        ),
        ({}, "match-key digests: not recorded"),
        (
            {"match_key_digests": "forged\nstatus: completed"},
            "match-key digests: not recorded",
        ),
    ],
)
async def test_the_report_states_whether_match_key_digests_compare_across_runs(
    engine: Engine, snapshot: dict[str, object], line: str
) -> None:
    run_id = await start(
        engine,
        RunRecord(T0, 2, snapshot, (SourceMode("alpha", DataMode.LIVE, "r"),)),
    )
    text = report_text(engine, run_id)
    assert line in text.splitlines()
    assert "forged" not in text
