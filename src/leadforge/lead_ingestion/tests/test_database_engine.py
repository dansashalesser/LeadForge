"""Database engine resolution from configuration (task 6.3)."""

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import leadforge.lead_ingestion as slice_pkg
from leadforge.lead_ingestion import database as db
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.structure_guard import ENGINE_SPECIFIC_ALLOWLIST

SLICE_ROOT = Path(slice_pkg.__file__).resolve().parent
SECRET = "s3cr3t-pass"
PG_URL = f"postgresql://app:{SECRET}@db.example.com:5432/leads"


# Verifies: specs/lead-source-adapters/requirements.md#9.2
@pytest.mark.parametrize("environ", [{}, {"DATABASE_URL": ""}, {"DATABASE_URL": "  "}])
def test_unset_or_blank_url_defaults_to_local_sqlite_file(
    environ: dict[str, str], tmp_path: Path
) -> None:
    url = db.resolve_database_url(environ, base_dir=tmp_path)
    assert url.get_backend_name() == "sqlite"
    assert url.database == str(tmp_path / ".leadforge" / "leadforge.db")


# Verifies: specs/lead-source-adapters/requirements.md#9.2
def test_default_start_creates_parent_dir_and_works_after_migration(
    tmp_path: Path,
) -> None:
    engine = db.create_store_engine(environ={}, base_dir=tmp_path)
    try:
        assert (tmp_path / ".leadforge").is_dir()
        upgrade_to_head(engine.url.render_as_string(hide_password=False))
        with Session(engine) as s:
            s.add(m.IngestionRun(started_at=_now(), status="running"))
            s.commit()
            assert s.scalar(sa.select(sa.func.count()).select_from(m.IngestionRun)) == 1
    finally:
        engine.dispose()
    assert (tmp_path / ".leadforge" / "leadforge.db").is_file()


def _now() -> datetime:
    return datetime.now(UTC)


# Verifies: specs/lead-source-adapters/requirements.md#9.2
def test_default_does_not_touch_cwd_or_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    url = db.resolve_database_url({})
    assert url.database == str(elsewhere.resolve() / ".leadforge" / "leadforge.db")
    assert not (elsewhere / ".leadforge").exists()  # resolving is side-effect free


# Verifies: specs/lead-source-adapters/requirements.md#9.3
def test_env_url_is_read_from_database_url(tmp_path: Path) -> None:
    target = tmp_path / "x.db"
    url = db.resolve_database_url({"DATABASE_URL": f"sqlite:///{target}"})
    assert url.database == str(target)
    assert db.DATABASE_URL_ENV == "DATABASE_URL"


# Verifies: specs/lead-source-adapters/requirements.md#9.3
@pytest.mark.parametrize(
    ("raw", "driver_name"),
    [
        (f"postgresql://u:{SECRET}@h/db", "psycopg"),
        (f"postgres://u:{SECRET}@h/db", "psycopg"),
        (f"postgresql+psycopg://u:{SECRET}@h/db", "psycopg"),
        (f"postgres+psycopg://u:{SECRET}@h/db", "psycopg"),
    ],
)
def test_postgres_urls_normalise_and_pick_driver_from_url_alone(
    raw: str, driver_name: str
) -> None:
    url = db.resolve_database_url({"DATABASE_URL": raw})
    assert url.get_backend_name() == "postgresql"
    assert url.get_driver_name() == driver_name
    assert url.host == "h"
    assert url.password == SECRET


# Verifies: specs/lead-source-adapters/requirements.md#9.3
def test_postgres_engine_gets_plain_create_engine_with_no_sqlite_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Postgres server or driver is needed: `create_engine` is intercepted."""
    calls: list[tuple[URL, dict[str, object]]] = []
    sentinel = sa.create_engine("sqlite://")

    def fake(url: URL, **kwargs: object) -> sa.Engine:
        calls.append((url, kwargs))
        return sentinel

    monkeypatch.setattr(db, "create_engine", fake)
    engine = db.create_store_engine(PG_URL)
    assert engine is sentinel
    [(url, kwargs)] = calls
    assert url.get_backend_name() == "postgresql"
    assert url.password == SECRET
    assert kwargs == {}
    assert not sa.event.contains(sentinel, "connect", db._enable_foreign_keys)


# Verifies: specs/lead-source-adapters/requirements.md#9.3
def test_missing_driver_is_reported_without_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(url: URL, **kwargs: object) -> sa.Engine:
        raise ModuleNotFoundError("No module named 'psycopg'")

    monkeypatch.setattr(db, "create_engine", boom)
    with pytest.raises(db.DatabaseConfigError) as info:
        db.create_store_engine(PG_URL)
    assert SECRET not in str(info.value)
    assert "psycopg" in str(info.value)
    assert info.value.__cause__ is None


# Verifies: specs/lead-source-adapters/requirements.md#9.2
def test_unsupported_backend_is_rejected_without_leaking() -> None:
    with pytest.raises(db.DatabaseConfigError) as info:
        db.resolve_database_url({"DATABASE_URL": f"mysql://u:{SECRET}@h/d"})
    assert SECRET not in str(info.value)
    assert "mysql" in str(info.value)


# Verifies: specs/lead-source-adapters/requirements.md#9.2
@pytest.mark.parametrize("raw", [f"not a url {SECRET}", f"://u:{SECRET}@h", "x"])
def test_unparseable_url_raises_config_error_without_echoing_it(raw: str) -> None:
    with pytest.raises(db.DatabaseConfigError) as info:
        db.resolve_database_url({"DATABASE_URL": raw})
    assert SECRET not in str(info.value)
    assert SECRET not in repr(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


# Verifies: specs/lead-source-adapters/requirements.md#9.3
def test_credentials_are_redacted_in_every_rendering() -> None:
    url = db.resolve_database_url({"DATABASE_URL": PG_URL})
    assert SECRET not in db.redact_url(url)
    assert "***" in db.redact_url(url)
    assert SECRET not in repr(url)
    assert SECRET not in str(url)


# Verifies: specs/lead-source-adapters/requirements.md#9.3
def test_relative_sqlite_path_is_made_absolute_against_base_dir(
    tmp_path: Path,
) -> None:
    url = db.resolve_database_url(
        {"DATABASE_URL": "sqlite:///rel/x.db"}, base_dir=tmp_path
    )
    assert url.database == str(tmp_path / "rel" / "x.db")


# Verifies: specs/lead-source-adapters/requirements.md#9.2
@pytest.mark.parametrize("raw", ["sqlite://", "sqlite:///:memory:"])
def test_in_memory_sqlite_shares_one_connection(raw: str) -> None:
    engine = db.create_store_engine(raw)
    try:
        assert isinstance(engine.pool, StaticPool)
        with engine.begin() as conn:
            upgrade_to_head(conn)
        # a second checkout must see the migrated schema (same underlying database)
        with engine.connect() as conn:
            assert "lead_identity" in sa.inspect(conn).get_table_names()
    finally:
        engine.dispose()


# Verifies: specs/lead-source-adapters/requirements.md#9.4
def test_sqlite_foreign_keys_are_enforced_on_every_connection(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'fk.db'}"
    upgrade_to_head(url)
    engine = db.create_store_engine(url)
    try:
        for _ in range(2):  # fresh pooled connection each time is also covered
            with Session(engine) as s:
                s.add(
                    m.SourceRun(
                        id=uuid.uuid4(),
                        run_id=uuid.uuid4(),  # no such ingestion_run
                        source_name="x",
                        resolved_mode="synthetic",
                    )
                )
                with pytest.raises(IntegrityError):
                    s.commit()
    finally:
        engine.dispose()


# Verifies: specs/lead-source-adapters/requirements.md#9.4
def test_foreign_keys_are_not_enforced_by_a_plain_sqlite_engine(
    tmp_path: Path,
) -> None:
    """Control: proves the previous test fails without the hook, not by accident."""
    url = f"sqlite:///{tmp_path / 'plain.db'}"
    upgrade_to_head(url)
    engine = sa.create_engine(url)
    try:
        with Session(engine) as s:
            s.add(
                m.SourceRun(
                    run_id=uuid.uuid4(), source_name="x", resolved_mode="synthetic"
                )
            )
            s.commit()
    finally:
        engine.dispose()


# Verifies: specs/lead-source-adapters/requirements.md#9.3
def test_percent_encoded_password_survives_and_stays_redacted() -> None:
    url = db.resolve_database_url({"DATABASE_URL": "postgresql://u:p%40ss%2Fw@h/db"})
    assert url.password == "p@ss/w"
    assert "p%40ss" not in db.redact_url(url)
    assert "ss/w" not in db.redact_url(url)


# Verifies: specs/lead-source-adapters/requirements.md#9.3
def test_a_url_object_override_is_validated_like_a_string() -> None:
    with pytest.raises(db.DatabaseConfigError):
        db.create_store_engine(make_url(f"mysql://u:{SECRET}@h/d"))


# Verifies: specs/lead-source-adapters/requirements.md#9.3
def test_backend_branching_lives_only_in_the_database_module() -> None:
    """Only the `ENGINE_SPECIFIC_ALLOWLIST` modules may name a backend (9.3)."""
    offenders: list[str] = []
    for path in SLICE_ROOT.rglob("*.py"):
        rel = path.relative_to(SLICE_ROOT)
        if (
            "tests" in rel.parts
            or "migrations" in rel.parts
            or rel.as_posix() in ENGINE_SPECIFIC_ALLOWLIST
        ):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and node.attr in {
                "get_backend_name",
                "get_dialect",
                "dialect",
            }:
                offenders.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                if "pragma" in low or low in {"sqlite", "postgresql", "postgres"}:
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []
