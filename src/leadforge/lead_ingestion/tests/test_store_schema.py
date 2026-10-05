"""Lead Store schema: typed models, DB-enforced identity, portability, append-only."""

import ast
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import leadforge.lead_ingestion as slice_pkg
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.migrate import upgrade_to_head

SLICE_ROOT = Path(slice_pkg.__file__).resolve().parent
NOW = datetime(2026, 10, 5, tzinfo=UTC)

EXPECTED_TABLES = {
    "ingestion_run",
    "source_run",
    "raw_response",
    "source_contribution",
    "contribution_field",
    "lead_identity",
    "identity_key",
    "canonical_lead",
    "canonical_field_provenance",
}
PORTABLE_TYPES = (
    sa.Uuid,
    sa.String,
    sa.Integer,
    sa.Float,
    sa.Boolean,
    sa.JSON,
    sa.DateTime,
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
    with Session(engine) as s:
        yield s


def _seed_contribution(s: Session) -> tuple[m.SourceContribution, m.ContributionField]:
    run = m.IngestionRun(started_at=NOW, status="running")
    s.add(run)
    s.flush()
    sr = m.SourceRun(
        run_id=run.id, source_name="provider_one", resolved_mode="synthetic"
    )
    s.add(sr)
    s.flush()
    raw = m.RawResponse(
        source_run_id=sr.id,
        endpoint_key="people",
        request_fingerprint="f",
        payload={"a": 1},
        fetched_at=NOW,
    )
    s.add(raw)
    s.flush()
    c = m.SourceContribution(
        source_run_id=sr.id,
        raw_response_id=raw.id,
        source_name="provider_one",
        data_mode="synthetic",
        fetched_at=NOW,
        lead_scope="person",
    )
    s.add(c)
    s.flush()
    f = m.ContributionField(
        contribution_id=c.id,
        canonical_path="full_name",
        value="Ada",
        raw_field_path="name",
        confidence=1.0,
        untrusted=False,
        truncated=False,
        original_length=3,
    )
    s.add(f)
    s.commit()
    return c, f


# Verifies: specs/lead-source-adapters/requirements.md#9.1
def test_every_listed_table_exists_as_a_mapped_model() -> None:
    assert set(m.Base.metadata.tables) == EXPECTED_TABLES
    assert {mp.class_.__tablename__ for mp in m.Base.registry.mappers} == (
        EXPECTED_TABLES
    )


# Verifies: specs/lead-source-adapters/requirements.md#9.1
def test_models_are_typed_mapped_annotations() -> None:
    for mp in m.Base.registry.mappers:
        for name in mp.columns.keys():  # noqa: SIM118
            assert name in mp.class_.__annotations__, (mp.class_.__name__, name)


# Verifies: specs/lead-source-adapters/requirements.md#9.1
def test_full_graph_round_trips_through_orm(session: Session) -> None:
    c, f = _seed_contribution(session)
    ident = m.LeadIdentity(created_at=NOW, primary_key_type="email")
    session.add(ident)
    session.flush()
    lead = m.CanonicalLeadRow(
        lead_identity_id=ident.id,
        full_name="Ada",
        computed_at=NOW,
        projection_version=1,
        contributing_sources=["provider_one"],
    )
    session.add(lead)
    session.flush()
    session.add(
        m.CanonicalFieldProvenance(
            canonical_lead_id=lead.id,
            canonical_path="full_name",
            winning_field_id=f.id,
            agreeing_source_count=1,
            superseded_field_ids=[],
        )
    )
    session.commit()
    assert (
        session.scalars(sa.select(m.CanonicalFieldProvenance)).one().winning_field_id
        == f.id
    )
    assert c.id is not None


# Verifies: specs/lead-source-adapters/requirements.md#9.1
def test_match_key_unique_on_type_and_value_enforced_by_database(
    session: Session,
) -> None:
    a = m.LeadIdentity(created_at=NOW, primary_key_type="email")
    b = m.LeadIdentity(created_at=NOW, primary_key_type="email")
    session.add_all([a, b])
    session.flush()
    session.add(
        m.IdentityKey(lead_identity_id=a.id, key_type="email", key_value="x@y.z")
    )
    session.commit()
    # same value under a different type is a different key
    session.add(
        m.IdentityKey(lead_identity_id=b.id, key_type="linkedin", key_value="x@y.z")
    )
    session.commit()
    session.add(
        m.IdentityKey(lead_identity_id=b.id, key_type="email", key_value="x@y.z")
    )
    with pytest.raises(sa.exc.IntegrityError):
        session.commit()


# Verifies: specs/lead-source-adapters/requirements.md#9.4
def test_only_portable_column_types_and_no_dialect_defaults() -> None:
    for table in m.Base.metadata.tables.values():
        for col in table.columns:
            assert type(col.type).__module__.startswith("sqlalchemy.sql.sqltypes"), (
                table.name,
                col.name,
                col.type,
            )
            assert isinstance(col.type, PORTABLE_TYPES), (table.name, col.name)
            assert col.server_default is None, (table.name, col.name)
            if isinstance(col.type, sa.DateTime):
                assert col.type.timezone, (table.name, col.name)


SQL_SHAPE = re.compile(
    r"^\s*(SELECT\s.+\sFROM\s|INSERT\s+INTO\s|UPDATE\s+\w+\s+SET\s|DELETE\s+FROM\s"
    r"|CREATE\s+(TABLE|INDEX|UNIQUE)\s|DROP\s+(TABLE|INDEX)\s|ALTER\s+TABLE\s)",
    re.IGNORECASE | re.DOTALL,
)
ANY_RECEIVER_ATTRS = {"dialect", "create_all", "drop_all"}
SQLALCHEMY_ATTRS = {"text", "DDL"}
SQLALCHEMY_NAMES = {"sa", "sqlalchemy"}


def _sql_offenders(root: Path) -> list[str]:
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if "tests" in rel.parts or "migrations" in rel.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                names = {a.name for a in node.names}
                if node.module.startswith("sqlalchemy.dialects") or (
                    node.module == "sqlalchemy" and names & {"text", "DDL"}
                ):
                    offenders.append(f"{rel}:{node.lineno} import")
            elif isinstance(node, ast.Attribute) and (
                node.attr in ANY_RECEIVER_ATTRS
                or (
                    node.attr in SQLALCHEMY_ATTRS
                    and isinstance(node.value, ast.Name)
                    and node.value.id in SQLALCHEMY_NAMES
                )
            ):
                offenders.append(f"{rel}:{node.lineno} .{node.attr}")
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SQL_SHAPE.match(node.value)
            ):
                offenders.append(f"{rel}:{node.lineno} sql string")
    return offenders


# Verifies: specs/lead-source-adapters/requirements.md#9.4
def test_no_raw_sql_or_dialect_branching_in_package_outside_migrations() -> None:
    assert _sql_offenders(SLICE_ROOT) == []


# Verifies: specs/lead-source-adapters/requirements.md#9.4
@pytest.mark.parametrize(
    "source",
    [
        'Q = "SELECT id FROM lead"\n',
        'Q = "update lead set x = 1"\n',
        "from sqlalchemy import text\n",
        "from sqlalchemy.dialects.postgresql import JSONB\n",
        "import sqlalchemy as sa\nx = sa.text('1')\n",
        "def f(engine):\n    return engine.dialect.name\n",
        "def f(engine, base):\n    base.metadata.create_all(engine)\n",
    ],
)
def test_sql_scan_flags_offenders(tmp_path: Path, source: str) -> None:
    (tmp_path / "x.py").write_text(source)
    assert _sql_offenders(tmp_path) != []


# Verifies: specs/lead-source-adapters/requirements.md#9.4
def test_sql_scan_ignores_prose_and_migrations(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text('"""Drop the cached token, select one."""\n')
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "v.py").write_text('Q = "DROP TABLE t"\n')
    assert _sql_offenders(tmp_path) == []


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_orm_update_of_a_contribution_is_refused(session: Session) -> None:
    c, _ = _seed_contribution(session)
    c.source_name = "other"
    with pytest.raises(m.AppendOnlyViolationError):
        session.commit()


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_orm_update_of_a_contribution_field_is_refused(session: Session) -> None:
    _, f = _seed_contribution(session)
    f.confidence = 0.1
    with pytest.raises(m.AppendOnlyViolationError):
        session.commit()


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_orm_delete_of_contribution_and_field_is_refused(session: Session) -> None:
    c, f = _seed_contribution(session)
    session.delete(f)
    with pytest.raises(m.AppendOnlyViolationError):
        session.commit()
    session.rollback()
    session.delete(c)
    with pytest.raises(m.AppendOnlyViolationError):
        session.commit()


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_bulk_update_and_delete_of_contributions_are_refused(
    session: Session,
) -> None:
    _seed_contribution(session)
    for model in (m.SourceContribution, m.ContributionField):
        with pytest.raises(m.AppendOnlyViolationError):
            session.execute(sa.update(model).values(id=uuid.uuid4()))
        with pytest.raises(m.AppendOnlyViolationError):
            session.execute(sa.delete(model))


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_inserting_contributions_and_updating_other_tables_still_works(
    session: Session,
) -> None:
    _seed_contribution(session)
    run = session.scalars(sa.select(m.IngestionRun)).one()
    run.status = "done"
    session.commit()
    assert session.scalars(sa.select(m.IngestionRun)).one().status == "done"
    assert len(session.scalars(sa.select(m.SourceContribution)).all()) == 1


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_contribution_raw_link_is_nullable_and_detaches_on_raw_delete() -> None:
    column = m.SourceContribution.__table__.c.raw_response_id
    assert column.nullable is True
    (fk,) = column.foreign_keys
    assert fk.ondelete == "SET NULL"
    assert fk.column.table.name == "raw_response"


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_no_code_in_package_updates_or_deletes_contribution_models() -> None:
    names = {"SourceContribution", "ContributionField"}
    offenders: list[str] = []
    for path in SLICE_ROOT.rglob("*.py"):
        rel = path.relative_to(SLICE_ROOT)
        if "tests" in rel.parts or (path.name == "models.py" and "store" in rel.parts):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"update", "delete", "merge"}
                and any(
                    isinstance(n, ast.Name) and n.id in names
                    for a in node.args
                    for n in ast.walk(a)
                )
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []


# Verifies: specs/lead-source-adapters/requirements.md#9.1
def test_untrusted_value_over_16000_bytes_is_storable(session: Session) -> None:
    c, _ = _seed_contribution(session)
    text = "\U0001f600" * 4000  # 4 UTF-8 bytes each: 16000 bytes
    session.add(
        m.ContributionField(
            contribution_id=c.id,
            canonical_path="bio",
            value=text,
            raw_field_path="bio",
            confidence=1.0,
            untrusted=True,
            truncated=False,
            original_length=4000,
        )
    )
    session.commit()
    got = session.scalars(
        sa.select(m.ContributionField).where(
            m.ContributionField.canonical_path == "bio"
        )
    ).one()
    assert got.value == text


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_legacy_query_delete_of_contribution_fields_is_refused(
    session: Session,
) -> None:
    _seed_contribution(session)
    with pytest.raises(m.AppendOnlyViolationError):
        session.query(m.ContributionField).delete()
    assert len(session.scalars(sa.select(m.ContributionField)).all()) == 1
