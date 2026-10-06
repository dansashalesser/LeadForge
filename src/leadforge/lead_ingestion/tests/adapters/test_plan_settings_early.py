"""Plan settings are validated before the run record exists (follow-up 2026-10-06).

``APOLLO_PLAN`` and ``SERPAPI_HOURLY_LIMIT`` size a live source's buckets. The
composition root reads them, for live sources only, before the store is touched: an
unusable value is a ``ConfigurationError`` naming the variable (never the value) and no
run is recorded. A synthetic source never reads its plan setting (7.5). Vendor-named,
so it lives with the adapter tests.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
import structlog
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from leadforge.lead_ingestion import cli
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.match_key_digest import MATCH_KEY_SECRET_ENV
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.socket_guard import SocketGuard, guard_for_mode

REPO_ROOT = Path(__file__).resolve().parents[5]
PROFILE = REPO_ROOT / "config" / "target_profile.yaml"
DISCOVERED = SourceRegistry.discover()


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in DISCOVERED.names():
        for variable in DISCOVERED.source_class(name).required_env:
            monkeypatch.delenv(variable, raising=False)
    for variable in ("LEADFORGE_MODE", "LEADFORGE_ENV_FILE", MATCH_KEY_SECRET_ENV):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
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


@pytest.fixture
def restore_structlog() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def runs_recorded(database: Path) -> int:
    """Ingestion runs in the store; 0 when the run never created the store."""
    if not database.exists():
        return 0
    engine = create_store_engine(f"sqlite:///{database}")
    try:
        if not sa.inspect(engine).has_table(m.IngestionRun.__tablename__):
            return 0
        with Session(engine) as session:
            return session.scalar(sa.select(sa.func.count(m.IngestionRun.id))) or 0
    finally:
        engine.dispose()


PLAN_SENTINELS = {
    "apollo": ("APOLLO_PLAN", "platinum-plan-sentinel"),
    "google_search": ("SERPAPI_HOURLY_LIMIT", "-77-limit-sentinel"),
}


def _live_with_bad_plan(monkeypatch: pytest.MonkeyPatch, source: str) -> str:
    """Make ``source`` resolve live (dummy credentials) with an unusable plan value."""
    for variable in DISCOVERED.source_class(source).required_env:
        monkeypatch.setenv(variable, "dummy-credential-not-used")
    variable, bad = PLAN_SENTINELS[source]
    monkeypatch.setenv(variable, bad)
    return bad


# Verifies: specs/lead-source-adapters/requirements.md#10.6
# Verifies: specs/lead-source-adapters/requirements.md#7.1
@pytest.mark.parametrize("source", sorted(PLAN_SENTINELS))
async def test_a_bad_plan_setting_fails_before_any_run_record(
    source: str,
    clean_environment: Path,
    guard: SocketGuard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = _live_with_bad_plan(monkeypatch, source)
    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(ConfigurationError) as caught,
    ):
        await run_ingestion(target_profile_path=PROFILE)
    assert PLAN_SENTINELS[source][0] in str(caught.value)
    assert bad not in repr(caught.value) + str(caught.value)
    assert bad not in json.dumps(logs, default=repr)
    assert runs_recorded(clean_environment) == 0


# Verifies: specs/lead-source-adapters/requirements.md#7.5
@pytest.mark.parametrize("source", sorted(PLAN_SENTINELS))
async def test_a_bad_plan_setting_is_not_read_for_a_synthetic_source(
    source: str,
    clean_environment: Path,
    guard: SocketGuard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variable, bad = PLAN_SENTINELS[source]
    monkeypatch.setenv(variable, bad)  # no credential: the source resolves synthetic
    outcome = await run_ingestion(target_profile_path=PROFILE)
    assert outcome.exit.exit_code == 0


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_the_ingest_command_names_a_bad_plan_setting_and_exits_two(
    clean_environment: Path,
    guard: SocketGuard,
    monkeypatch: pytest.MonkeyPatch,
    restore_structlog: None,
) -> None:
    bad = _live_with_bad_plan(monkeypatch, "apollo")

    result = CliRunner().invoke(cli.app, ["ingest"])

    assert result.exit_code == 2, result.output
    assert "APOLLO_PLAN" in result.output
    assert bad not in result.output
    assert runs_recorded(clean_environment) == 0
