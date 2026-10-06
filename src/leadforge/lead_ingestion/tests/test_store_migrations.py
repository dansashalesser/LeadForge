"""Lead Store schema is delivered by Alembic migrations only (task 6.2)."""

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import leadforge.lead_ingestion as slice_pkg
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.migrate import (
    alembic_config,
    downgrade_to_base,
    upgrade_to_head,
)

SLICE_ROOT = Path(slice_pkg.__file__).resolve().parent
SRC_ROOT = SLICE_ROOT.parents[1]
MIGRATIONS_DIR = SLICE_ROOT / "store" / "migrations"
MODEL_TABLES = set(m.Base.metadata.tables)
CREATE_STYLE_ATTRS = {"create_all", "drop_all"}


def _app_tables(engine: sa.Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_upgrade_to_head_creates_every_modelled_table() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)

    assert _app_tables(engine) == MODEL_TABLES


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_upgrade_to_head_accepts_a_database_url(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'leads.db'}"
    upgrade_to_head(url)

    assert _app_tables(create_engine(url)) == MODEL_TABLES


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_upgrade_to_head_twice_is_a_no_op(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'leads.db'}"
    upgrade_to_head(url)
    upgrade_to_head(url)

    assert _app_tables(create_engine(url)) == MODEL_TABLES


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_migration_history_is_linear_with_one_head_and_one_root() -> None:
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))

    assert len(script.get_heads()) == 1
    assert len(script.get_bases()) == 1


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_migration_head_matches_orm_metadata_with_no_drift() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": True}
        )
        diff = compare_metadata(context, m.Base.metadata)

    assert diff == []


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_drift_check_detects_a_missing_column() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
        conn.exec_driver_sql("ALTER TABLE canonical_lead DROP COLUMN email")
        context = MigrationContext.configure(conn)
        diff = compare_metadata(context, m.Base.metadata)

    assert diff != []


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_downgrade_to_base_removes_every_table() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
        downgrade_to_base(conn)

    assert _app_tables(engine) == set()


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_migrated_schema_enforces_match_key_uniqueness() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
    uniques = inspect(engine).get_unique_constraints("identity_key")
    indexes = inspect(engine).get_indexes("identity_key")

    columns = [set(u["column_names"]) for u in uniques] + [
        set(i["column_names"]) for i in indexes if i["unique"]
    ]
    assert {"key_type", "key_value"} in columns


def _create_style_calls(root: Path) -> list[str]:
    """Calls such as ``metadata.create_all`` outside tests and migrations."""
    found: list[str] = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if "tests" in rel.parts or "migrations" in rel.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and node.attr in CREATE_STYLE_ATTRS:
                found.append(f"{rel}:{node.lineno} .{node.attr}")
            elif isinstance(node, ast.Name) and node.id in CREATE_STYLE_ATTRS:
                found.append(f"{rel}:{node.lineno} {node.id}")
    return found


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_no_implicit_schema_creation_anywhere_in_application_source() -> None:
    assert _create_style_calls(SRC_ROOT) == []


# Verifies: specs/lead-source-adapters/requirements.md#9.7
@pytest.mark.parametrize(
    "source",
    [
        "def f(engine, base):\n    base.metadata.create_all(engine)\n",
        "def f(meta, engine):\n    meta.drop_all(engine)\n",
        "from x import create_all\ncreate_all()\n",
    ],
)
def test_create_scan_flags_offenders(tmp_path: Path, source: str) -> None:
    (tmp_path / "app.py").write_text(source)
    assert _create_style_calls(tmp_path) != []


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_create_scan_allows_migrations_and_tests(tmp_path: Path) -> None:
    for folder in ("migrations", "tests"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "x.py").write_text("meta.create_all(engine)\n")
    assert _create_style_calls(tmp_path) == []


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_using_the_orm_without_migrating_does_not_create_the_schema() -> None:
    engine = create_engine("sqlite://")
    with Session(engine) as session, pytest.raises(OperationalError):
        session.get(m.IngestionRun, uuid.uuid4())

    assert inspect(engine).get_table_names() == []


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_migrations_directory_ships_inside_the_package() -> None:
    assert (MIGRATIONS_DIR / "env.py").is_file()
    assert list((MIGRATIONS_DIR / "versions").glob("*.py"))


def _raw_fk(engine: sa.Engine) -> dict[str, object]:
    (fk,) = [
        f
        for f in inspect(engine).get_foreign_keys("source_contribution")
        if f["constrained_columns"] == ["raw_response_id"]
    ]
    return dict(fk)


def _raw_column_nullable(engine: sa.Engine) -> bool:
    cols = {c["name"]: c for c in inspect(engine).get_columns("source_contribution")}
    return bool(cols["raw_response_id"]["nullable"])


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_head_makes_raw_response_link_nullable_with_set_null() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)

    assert _raw_column_nullable(engine) is True
    fk = _raw_fk(engine)
    assert fk["referred_table"] == "raw_response"
    assert fk["options"].get("ondelete") == "SET NULL"  # type: ignore[attr-defined]
    # the batch rebuild keeps the other keys and the index
    names = {
        tuple(f["constrained_columns"])
        for f in inspect(engine).get_foreign_keys("source_contribution")
    }
    assert names == {("source_run_id",), ("lead_identity_id",), ("raw_response_id",)}
    assert "ix_source_contribution_lead_identity_id" in {
        i["name"] for i in inspect(engine).get_indexes("source_contribution")
    }


def _seed_contribution_with_raw(conn: sa.Connection, raw_id: uuid.UUID | None) -> None:
    when = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    # Core inserts render only the columns given (and defaulted), so the run rows can
    # be seeded at a revision older than the models (0005 added run columns).
    run_id, source_run_id = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        sa.insert(m.IngestionRun).values(id=run_id, started_at=when, status="running")
    )
    conn.execute(
        sa.insert(m.SourceRun).values(
            id=source_run_id, run_id=run_id, source_name="x", resolved_mode="live"
        )
    )
    with Session(bind=conn) as s:
        raw = None
        if raw_id is not None:
            raw = m.RawResponse(
                id=raw_id,
                source_run_id=source_run_id,
                endpoint_key="e",
                request_fingerprint="f",
                payload={},
                fetched_at=when,
            )
            s.add(raw)
            s.flush()
        s.add(
            m.SourceContribution(
                source_run_id=source_run_id,
                raw_response_id=raw_id,
                source_name="x",
                data_mode="live",
                fetched_at=when,
                lead_scope="person",
            )
        )
        s.flush()


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_downgrade_to_0002_restores_not_null_and_keeps_attached_rows() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
        _seed_contribution_with_raw(conn, uuid.uuid4())
        command.downgrade(alembic_config(conn), "0002")

    assert _raw_column_nullable(engine) is False
    assert _raw_fk(engine)["options"].get("ondelete") is None  # type: ignore[attr-defined]
    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT count(*) FROM source_contribution")).scalar()
            == 1
        )


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_downgrade_to_0002_refuses_while_a_detached_contribution_exists() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
        _seed_contribution_with_raw(conn, None)  # seeding succeeds at head
    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT count(*) FROM source_contribution")).scalar()
            == 1
        )
    with engine.begin() as conn, pytest.raises(sa.exc.IntegrityError):
        command.downgrade(alembic_config(conn), "0002")


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_upgrade_through_0002_then_head_preserves_existing_rows() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "0002")
        _seed_contribution_with_raw(conn, uuid.uuid4())
        upgrade_to_head(conn)

    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT count(*) FROM source_contribution")).scalar()
            == 1
        )
