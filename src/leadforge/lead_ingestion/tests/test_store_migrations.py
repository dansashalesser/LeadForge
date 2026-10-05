"""Lead Store schema is delivered by Alembic migrations only (task 6.2)."""

import ast
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
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
