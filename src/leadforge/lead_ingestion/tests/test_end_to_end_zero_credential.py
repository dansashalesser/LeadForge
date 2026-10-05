"""The zero-credential ingestion, end to end (task 20; the integration gate).

An EMPTY environment (no credential, no mode override, no ``.env``) and sockets blocked:
the run is composed by ``run_ingestion`` (the composition root the ``ingest`` command
calls) over every registered source, merged, persisted into a local SQLite store and
reported from the database. Nothing here names a vendor; the parts that do live in
tests/adapters/test_zero_credential_run_adapters.py.

Failure injection uses scripted neutral sources registered beside the real ones: the
seam is the registry the root is given.
"""

import asyncio
import re
import uuid
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import ClassVar

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from leadforge.lead_ingestion import cli
from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    LeadContribution,
    RateBucket,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.errors import (
    NormalizationError,
    SourceComplianceRestricted,
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
    SourceTimedOut,
    SourceTransient,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.ingest_runner import IngestionOutcome, run_ingestion
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import SourceStatus
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.run_exit import RunExit
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.socket_guard import SocketGuard, guard_for_mode

REPO_ROOT = Path(__file__).resolve().parents[4]
PROFILE = REPO_ROOT / "config" / "target_profile.yaml"
DISCOVERED = SourceRegistry.discover()


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No credential or override, no ``.env`` in reach, a store of its own."""
    for name in DISCOVERED.names():
        for variable in DISCOVERED.source_class(name).required_env:
            monkeypatch.delenv(variable, raising=False)
    for variable in ("LEADFORGE_MODE", "LEADFORGE_ENV_FILE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)  # a directory with no .env, no config/
    database = tmp_path / "store.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    return database


@pytest.fixture
def guard(
    clean_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SocketGuard]:
    blocked = guard_for_mode(DataMode.SYNTHETIC)
    blocked.install(monkeypatch)
    yield blocked
    blocked.assert_clean()


def store(database: Path) -> Engine:
    return create_store_engine(f"sqlite:///{database}")


def scripted(
    source_name: str,
    *,
    error: Callable[[str], SourceError] | None = None,
) -> type[BaseLeadSource]:
    """A search source that fails with ``error(name)``, or returns nothing."""

    class Scripted(BaseLeadSource):
        name: ClassVar[str] = source_name
        capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
        rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
        answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
        cost_class: ClassVar[CostClass] = CostClass.FREE
        charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
        yields_suppression: ClassVar[bool] = False
        target_vocabulary: ClassVar[Mapping[str, object]] = {}
        endpoints: ClassVar[Mapping[str, Endpoint]] = {}
        required_env: ClassVar[tuple[str, ...]] = ()

        async def fetch_raw(self, request: SourceRequest) -> RawBatch:
            if error is not None:
                raise error(self.name)
            return RawBatch(source_name=self.name, payload={})

        def normalize(self, raw: RawBatch) -> list[LeadContribution]:
            return []

    return Scripted


def real_plus(*extra: type[BaseLeadSource]) -> SourceRegistry:
    real = [DISCOVERED.source_class(n) for n in DISCOVERED.names()]
    return SourceRegistry(
        [*real, *extra],
        {c.name: SourceSettings(mode=DataMode.SYNTHETIC) for c in extra},
    )


def source_lines(outcome: IngestionOutcome) -> list[str]:
    return outcome.exit.summary.splitlines()


def canonical_leads(database: Path) -> int:
    engine = store(database)
    try:
        with Session(engine) as session:
            return session.scalar(sa.select(sa.func.count(m.CanonicalLeadRow.id))) or 0
    finally:
        engine.dispose()


# Verifies: specs/lead-source-adapters/requirements.md#4.5
# Verifies: specs/lead-source-adapters/requirements.md#6.5
async def test_an_empty_environment_runs_every_registered_source_and_persists_a_lead(
    clean_environment: Path, guard: SocketGuard
) -> None:
    outcome = await run_ingestion(target_profile_path=PROFILE)

    assert outcome.exit.exit_code == 0
    assert {r.source_name for r in outcome.results} == set(DISCOVERED.names())
    for result in outcome.results:
        assert result.resolved_mode is DataMode.SYNTHETIC, result.source_name
        assert result.outcome.status is SourceStatus.OK, result.source_name
    assert canonical_leads(clean_environment) >= 1
    assert outcome.stored.canonical_leads >= 1
    assert outcome.stored.contributions == sum(
        len(r.contributions or ()) for r in outcome.results
    )


# Verifies: specs/lead-source-adapters/requirements.md#6.5
async def test_the_run_reports_attempted_succeeded_and_failed_per_source(
    clean_environment: Path, guard: SocketGuard
) -> None:
    outcome = await run_ingestion(target_profile_path=PROFILE)

    latest = {r.source_name: r.outcome for r in outcome.results}
    assert set(latest) == set(DISCOVERED.names())
    for name, counts in latest.items():
        line = (
            f"{name}: attempted={counts.attempted} "
            f"succeeded={counts.succeeded} failed={counts.failed}"
        )
        assert line in source_lines(outcome)
        assert counts.attempted >= 1
        assert counts.succeeded >= 1
        assert counts.failed == 0


# Verifies: specs/lead-source-adapters/requirements.md#6.5
# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_the_report_comes_from_the_database_and_names_every_source(
    clean_environment: Path, guard: SocketGuard
) -> None:
    outcome = await run_ingestion(target_profile_path=PROFILE)

    engine = store(clean_environment)
    try:
        with Session(engine) as session:
            run = session.get_one(m.IngestionRun, outcome.run_id)
            rows = session.scalars(
                sa.select(m.SourceRun).where(m.SourceRun.run_id == outcome.run_id)
            ).all()
        assert (run.status, run.exit_code) == ("completed", 0)
        assert {r.source_name for r in rows} == set(DISCOVERED.names())
        assert {r.resolved_mode for r in rows} == {"synthetic"}
        assert {r.failure_class for r in rows} == {None}
    finally:
        engine.dispose()
    for name in DISCOVERED.names():
        assert f"{name}: mode=synthetic" in outcome.report_text
    assert f"run {outcome.run_id}" in outcome.report_text
    assert "exit code: 0" in outcome.report_text


# Verifies: specs/lead-source-adapters/requirements.md#6.1
# Verifies: specs/lead-source-adapters/requirements.md#6.5
async def test_one_failing_source_still_yields_every_other_sources_results(
    clean_environment: Path, guard: SocketGuard
) -> None:
    broken = scripted("broken", error=lambda name: SourceTransient(name, status=503))

    outcome = await run_ingestion(
        registry=real_plus(broken), target_profile_path=PROFILE
    )

    assert outcome.exit.exit_code == 0
    by_name = {r.source_name: r.outcome for r in outcome.results}
    assert by_name["broken"].status is SourceStatus.TRANSIENT
    assert by_name["broken"].failed == 1
    assert by_name["broken"].succeeded == 0
    for name in DISCOVERED.names():
        assert by_name[name].status is SourceStatus.OK, name
    assert canonical_leads(clean_environment) >= 1
    assert "broken: transient attempted=1 succeeded=0 failed=1" in source_lines(outcome)
    assert "broken: mode=synthetic" in outcome.report_text
    assert "failure=transient" in outcome.report_text
    engine = store(clean_environment)
    try:
        with Session(engine) as session:
            classes = {
                name: failure
                for name, failure in session.execute(
                    sa.select(m.SourceRun.source_name, m.SourceRun.failure_class)
                )
            }
    finally:
        engine.dispose()
    assert classes["broken"] == "transient"
    assert {classes[n] for n in DISCOVERED.names()} == {None}


# Verifies: specs/lead-source-adapters/requirements.md#6.4
async def test_every_source_failing_exits_non_zero_naming_each_failure_class(
    clean_environment: Path, guard: SocketGuard
) -> None:
    registry = SourceRegistry(
        [
            scripted("down", error=lambda n: SourceTransient(n, status=503)),
            scripted("locked", error=lambda n: SourceUnauthorized(n, endpoint="/v1/x")),
        ],
        {n: SourceSettings(mode=DataMode.SYNTHETIC) for n in ("down", "locked")},
    )

    outcome = await run_ingestion(registry=registry)

    assert outcome.exit.exit_code != 0
    lines = source_lines(outcome)
    assert lines[0] == "all enabled sources failed"
    assert "down: transient attempted=1 succeeded=0 failed=1" in lines
    assert "locked: unauthorized attempted=1 succeeded=0 failed=1" in lines
    engine = store(clean_environment)
    try:
        with Session(engine) as session:
            run = session.get_one(m.IngestionRun, outcome.run_id)
            assert (run.status, run.exit_code) == ("completed", outcome.exit.exit_code)
    finally:
        engine.dispose()
    assert canonical_leads(clean_environment) == 0
    assert "failure=transient" in outcome.report_text
    assert "failure=unauthorized" in outcome.report_text


FAILURES: dict[SourceStatus, Callable[[str], SourceError]] = {
    SourceStatus.UNAUTHORIZED: lambda n: SourceUnauthorized(n, endpoint="/v1/x"),
    SourceStatus.RATE_LIMITED: lambda n: SourceRateLimited(n, cause="test"),
    SourceStatus.QUOTA_EXHAUSTED: lambda n: SourceQuotaExhausted(n),
    SourceStatus.TRANSIENT: lambda n: SourceTransient(n, status=503),
    SourceStatus.TIMED_OUT: lambda n: SourceTimedOut(n),
    SourceStatus.COMPLIANCE_RESTRICTED: lambda n: SourceComplianceRestricted(
        n, subject="x"
    ),
    SourceStatus.NORMALIZATION_FAILED: lambda n: NormalizationError(
        n, raw_field_path="a", canonical_path="b"
    ),
}


# Verifies: specs/lead-source-adapters/requirements.md#6.1
# Verifies: specs/lead-source-adapters/requirements.md#6.5
@pytest.mark.parametrize("status", list(FAILURES), ids=lambda s: s.value)
async def test_each_failure_class_of_one_source_leaves_the_others_results(
    status: SourceStatus, clean_environment: Path, guard: SocketGuard
) -> None:
    broken = scripted("broken", error=FAILURES[status])

    outcome = await run_ingestion(
        registry=real_plus(broken), target_profile_path=PROFILE
    )

    assert outcome.exit.exit_code == 0
    by_name = {r.source_name: r.outcome for r in outcome.results}
    assert by_name["broken"].status is status
    for name in DISCOVERED.names():
        assert by_name[name].status is SourceStatus.OK, name
    assert canonical_leads(clean_environment) >= 1
    assert f"broken: {status.value} " in outcome.exit.summary


# Verifies: specs/lead-source-adapters/requirements.md#6.4
async def test_all_sources_failing_names_every_class_and_source(
    clean_environment: Path, guard: SocketGuard
) -> None:
    names = {status: f"src_{status.value}" for status in FAILURES}
    registry = SourceRegistry(
        [scripted(names[s], error=FAILURES[s]) for s in FAILURES],
        {n: SourceSettings(mode=DataMode.SYNTHETIC) for n in names.values()},
    )

    outcome = await run_ingestion(registry=registry)

    assert outcome.exit.exit_code == 1
    lines = source_lines(outcome)
    assert lines[0] == "all enabled sources failed"
    for status, name in names.items():
        assert any(line.startswith(f"{name}: {status.value} ") for line in lines)
    assert canonical_leads(clean_environment) == 0


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_crash_mid_run_is_recorded_aborted_and_propagates(
    clean_environment: Path, guard: SocketGuard
) -> None:
    def crash(name: str) -> SourceError:
        raise RuntimeError("programming error")

    registry = SourceRegistry(
        [scripted("crashing", error=crash)],
        {"crashing": SourceSettings(mode=DataMode.SYNTHETIC)},
    )

    with pytest.raises(ExceptionGroup):
        await run_ingestion(registry=registry)

    engine = store(clean_environment)
    try:
        with Session(engine) as session:
            run = session.scalars(sa.select(m.IngestionRun)).one()
    finally:
        engine.dispose()
    assert (run.status, run.exit_code) == ("aborted", None)
    assert canonical_leads(clean_environment) == 0


# Verifies: specs/lead-source-adapters/requirements.md#6.6
async def test_cancelling_the_run_propagates_and_marks_it_aborted(
    clean_environment: Path, guard: SocketGuard
) -> None:
    started = asyncio.Event()

    class Hangs(scripted("hanging")):  # type: ignore[misc]
        async def fetch_raw(self, request: SourceRequest) -> RawBatch:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    registry = SourceRegistry(
        [Hangs], {"hanging": SourceSettings(mode=DataMode.SYNTHETIC)}
    )
    task = asyncio.ensure_future(run_ingestion(registry=registry))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    engine = store(clean_environment)
    try:
        with Session(engine) as session:
            run = session.scalars(sa.select(m.IngestionRun)).one()
    finally:
        engine.dispose()
    assert run.status == "aborted"


# Verifies: specs/lead-source-adapters/requirements.md#22.1
async def test_no_lead_data_reaches_the_summary_or_the_report(
    clean_environment: Path, guard: SocketGuard
) -> None:
    outcome = await run_ingestion(target_profile_path=PROFILE)

    personal = {
        str(value)
        for result in outcome.results
        for c in result.contributions or ()
        for path, value in c.values.items()
        if path in {"person.email", "person.linkedin_url", "email"}
    }
    assert personal  # the run really carried personal data
    shown = outcome.exit.summary + outcome.report_text
    assert not any(p in shown for p in personal)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", shown)


# ------------------------------------------------------------------ the command


# Verifies: specs/lead-source-adapters/requirements.md#4.5
# Verifies: specs/lead-source-adapters/requirements.md#6.5
def test_the_ingest_command_runs_the_zero_credential_ingestion_and_exits_zero(
    clean_environment: Path, guard: SocketGuard
) -> None:
    result = CliRunner().invoke(cli.app, ["ingest"])

    assert result.exit_code == 0, result.output
    for name in DISCOVERED.names():
        assert f"{name}: mode=synthetic" in result.output
    assert "exit code: 0" in result.output


# Verifies: specs/lead-source-adapters/requirements.md#22.1
def test_the_ingest_command_output_carries_no_lead_data(
    clean_environment: Path, guard: SocketGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(
        REPO_ROOT
    )  # the shipped Target Profile: fixtures with an email flow

    result = CliRunner().invoke(cli.app, ["ingest"])

    assert result.exit_code == 0, result.output
    engine = store(clean_environment)
    try:
        with Session(engine) as session:
            email = session.scalars(
                sa.select(m.CanonicalLeadRow.email).where(
                    m.CanonicalLeadRow.email.is_not(None)
                )
            ).first()
            linkedin = session.scalars(
                sa.select(m.CanonicalLeadRow.linkedin_url).where(
                    m.CanonicalLeadRow.linkedin_url.is_not(None)
                )
            ).first()
    finally:
        engine.dispose()
    assert email is not None
    assert linkedin is not None
    for text in (result.stdout, result.stderr):
        assert email not in text
        assert linkedin not in text
        assert "Lovelace" not in text
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_the_ingest_command_exits_with_the_mapped_code_and_prints_the_summary(
    clean_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = IngestionOutcome(
        run_id=uuid.uuid4(),
        exit=RunExit(1, "all enabled sources failed\ndown: transient attempted=1"),
        results=(),
        report_text="REPORT",
        stored=None,  # type: ignore[arg-type]
    )

    async def fake(**_: object) -> IngestionOutcome:
        return failed

    monkeypatch.setattr(cli, "run_ingestion", fake)

    result = CliRunner().invoke(cli.app, ["ingest"])

    assert result.exit_code == 1
    assert "all enabled sources failed" in result.output
    assert "down: transient attempted=1" in result.output
    assert "REPORT" in result.output


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_the_ingest_command_exits_non_zero_and_names_each_failure_class(
    clean_environment: Path, guard: SocketGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = SourceRegistry(
        [
            scripted("down", error=lambda n: SourceTransient(n, status=503)),
            scripted("locked", error=lambda n: SourceUnauthorized(n, endpoint="/v1")),
        ],
        {n: SourceSettings(mode=DataMode.SYNTHETIC) for n in ("down", "locked")},
    )

    async def with_registry() -> IngestionOutcome:
        return await run_ingestion(registry=registry)

    monkeypatch.setattr(cli, "run_ingestion", with_registry)

    result = CliRunner().invoke(cli.app, ["ingest"])

    assert result.exit_code == 1
    assert "down: transient" in result.output
    assert "locked: unauthorized" in result.output


# Verifies: specs/lead-source-adapters/requirements.md#21.1
def test_the_ingest_command_exits_non_zero_when_the_run_crashes(
    clean_environment: Path, guard: SocketGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(name: str) -> SourceError:
        raise RuntimeError("programming error")

    registry = SourceRegistry(
        [scripted("crashing", error=crash)],
        {"crashing": SourceSettings(mode=DataMode.SYNTHETIC)},
    )

    async def with_registry() -> IngestionOutcome:
        return await run_ingestion(registry=registry)

    monkeypatch.setattr(cli, "run_ingestion", with_registry)

    result = CliRunner().invoke(cli.app, ["ingest"])

    assert result.exit_code != 0
    assert isinstance(result.exception, ExceptionGroup)


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_the_ingest_command_names_a_configuration_error_and_exits_two(
    clean_environment: Path,
) -> None:
    config = clean_environment.parent / "config"
    config.mkdir()
    (config / "sources.yaml").write_text("not_a_key: 1\n", encoding="utf-8")

    result = CliRunner().invoke(cli.app, ["ingest"])

    assert result.exit_code == 2
    assert "sources.yaml" in result.output
