"""The ingestion-to-persistence path runs on SQLite and on PostgreSQL (task 6.7, 9.5).

One parameterized module: every test that takes the ``backend`` fixture runs once per
engine (ids ``sqlite`` and ``postgres``) against a freshly migrated store, and asserts
the same observable result on both. Where the engines genuinely differ, the difference
is a visible parameterized expectation (``EXPECT``), never an unasserted gap.

The Postgres leg is a required gate, not a skip. ``LEADFORGE_TEST_POSTGRES_URL`` names
a server (``docker compose up -d postgres``, or a CI service); when it is unset an
ephemeral cluster is started from the installed server binaries in a temp directory
and removed after the session. If neither is possible the leg FAILS with the
instruction to run docker compose or set the URL.

Isolation: each Postgres test gets its own schema (created and dropped with SQLAlchemy
DDL constructs, so the schema and the migrations are the only SQL) selected through the
connection's ``search_path``. Credentials are held as ``URL`` objects and rendered only
through ``redact_url``.
"""

import ast
import asyncio
import os
import pwd
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.database import create_store_engine, redact_url
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
    UntrustedText,
)
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.contributions import (
    read_contribution,
    write_contribution,
)
from leadforge.lead_ingestion.store.migrate import (
    alembic_config,
    downgrade_to_base,
    upgrade_to_head,
)
from leadforge.lead_ingestion.store.raw_responses import (
    PurgeResult,
    RawResponseRepository,
    RetentionPolicy,
)
from leadforge.lead_ingestion.store.transactions import StoreWriter

TEST_POSTGRES_URL_ENV = "LEADFORGE_TEST_POSTGRES_URL"
POSTGRES_HINT = (
    "The Postgres leg of requirement 9.5 is a required gate, not an optional one. "
    "Run `docker compose up -d postgres` and export "
    f"{TEST_POSTGRES_URL_ENV}="
    "postgresql://leadforge:leadforge@localhost:5432/leadforge "
    "(see README), or install the PostgreSQL 16 server binaries so an ephemeral "
    "cluster can be started."
)
MODEL_TABLES = set(m.Base.metadata.tables)
T0 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
NOW = T0 + timedelta(days=40)

type BackendName = Literal["sqlite", "postgres"]

# Differences between the engines, asserted rather than ignored.
EXPECT: dict[str, dict[BackendName, Any]] = {
    # SQLite has no timestamp-with-zone type: it hands back naive values.
    "aware_datetimes_from_unmanaged_columns": {"sqlite": False, "postgres": True},
    # Through Core (no ORM guard) an over-long String is only refused by Postgres.
    "core_overlong_string_error": {"sqlite": None, "postgres": sa.exc.DataError},
    # Unnamed foreign keys carry the engine's default name only on Postgres (0001).
    "unnamed_fk_name_at_0001": {
        "sqlite": None,
        "postgres": "source_contribution_raw_response_id_fkey",
    },
}


# ---------------------------------------------------------------- Postgres provisioning


def _find_server_bin_dir() -> Path | None:
    """Directory with ``initdb``, ``pg_ctl`` and ``postgres``; newest version first."""
    candidates: list[Path] = []
    on_path = shutil.which("pg_ctl")
    if on_path:
        candidates.append(Path(on_path).resolve().parent)
    versions = sorted(
        Path("/usr/lib/postgresql").glob("*/bin"),
        key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0,
        reverse=True,
    )
    candidates.extend(versions)
    for directory in candidates:
        if all(
            (directory / tool).is_file() for tool in ("initdb", "pg_ctl", "postgres")
        ):
            return directory
    return None


def _run_tool(
    argv: list[str], *, user: str | None, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        user=user,
        cwd="/",
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _start_ephemeral(bin_dir: Path) -> tuple[URL, Callable[[], None]]:
    """Start a throwaway cluster on a unix socket in a temp dir (never as root)."""
    user: str | None = None
    if os.geteuid() == 0:
        try:
            pwd.getpwnam("postgres")
        except KeyError:
            pytest.fail(
                "cannot start an ephemeral PostgreSQL as root: no `postgres` OS "
                f"user exists. {POSTGRES_HINT}"
            )
        user = "postgres"
    root = Path(tempfile.mkdtemp(prefix="lfpg"))  # short: unix socket paths are capped
    if user:
        shutil.chown(root, user=user)
    data = root / "data"

    def stop() -> None:
        _run_tool(
            [str(bin_dir / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
            user=user,
        )
        shutil.rmtree(root, ignore_errors=True)

    init = _run_tool(
        [
            str(bin_dir / "initdb"),
            "-D",
            str(data),
            "-A",
            "trust",
            "-U",
            "postgres",
            "-E",
            "UTF8",
            "--no-locale",
            "--no-sync",
        ],
        user=user,
    )
    if init.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        pytest.fail(f"initdb failed ({init.stderr.strip()[-300:]}). {POSTGRES_HINT}")
    options = f"-c listen_addresses='' -c unix_socket_directories={root} -c fsync=off"
    start = _run_tool(
        [
            str(bin_dir / "pg_ctl"),
            "-D",
            str(data),
            "-o",
            options,
            "-l",
            str(root / "server.log"),
            "-w",
            "start",
        ],
        user=user,
    )
    if start.returncode != 0:
        stop()
        pytest.fail(
            f"pg_ctl start failed ({start.stderr.strip()[-300:]}). {POSTGRES_HINT}"
        )
    url = URL.create(
        "postgresql",
        username="postgres",
        database="postgres",
        query={"host": str(root)},
    )
    return url, stop


def _check_reachable(url: URL) -> None:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(sa.select(sa.literal(1)))
    except sa.exc.SQLAlchemyError as exc:
        # Never echo the driver message: only the class and the redacted URL.
        pytest.fail(
            f"cannot connect to PostgreSQL at {redact_url(url)} "
            f"({type(exc).__name__}). {POSTGRES_HINT}"
        )
    finally:
        engine.dispose()


def _resolve_postgres(
    environ: Mapping[str, str],
    *,
    find_server: Callable[[], Path | None] = _find_server_bin_dir,
) -> tuple[URL, Callable[[], None]]:
    """The Postgres under test and how to stop it; fails (never skips) if none."""
    raw = environ.get(TEST_POSTGRES_URL_ENV, "").strip()
    if raw:
        url = make_url(raw)
        _check_reachable(url)
        return url, lambda: None
    bin_dir = find_server()
    if bin_dir is None:
        pytest.fail(
            f"{TEST_POSTGRES_URL_ENV} is unset and no PostgreSQL server binaries "
            f"were found. {POSTGRES_HINT}"
        )
    url, stop = _start_ephemeral(bin_dir)
    try:
        _check_reachable(url)
    except BaseException:
        stop()
        raise
    return url, stop


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[URL]:
    url, stop = _resolve_postgres(os.environ)
    try:
        yield url
    finally:
        stop()


# ---------------------------------------------------------------------------- backends


@dataclass(frozen=True)
class Backend:
    name: BackendName
    url: URL
    engine: Engine  # built by create_store_engine, like production


def _make_sqlite(tmp_path: Path) -> Iterator[Backend]:
    url = URL.create("sqlite", database=str(tmp_path / "store.db"))
    engine = create_store_engine(url)
    try:
        yield Backend("sqlite", url, engine)
    finally:
        engine.dispose()


def _make_postgres(base: URL) -> Iterator[Backend]:
    schema = f"lf_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.schema.CreateSchema(schema))
    url = base.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_store_engine(url)
    try:
        yield Backend("postgres", url, engine)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(sa.schema.DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", id="postgres"),
    ]
)
def blank(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Backend]:
    """An empty database on the engine under test (nothing migrated)."""
    if request.param == "sqlite":
        yield from _make_sqlite(tmp_path)
    else:
        base: URL = request.getfixturevalue("postgres_url")
        yield from _make_postgres(base)


def _alembic(backend: Backend, action: str, revision: str) -> None:
    """Run one migration step on a plain engine, as `store.migrate` does."""
    engine = create_engine(backend.url)
    try:
        with engine.begin() as conn:
            getattr(command, action)(alembic_config(conn), revision)
    finally:
        engine.dispose()


@pytest.fixture
def backend(blank: Backend) -> Backend:
    """The engine under test, migrated to head."""
    _alembic(blank, "upgrade", "head")
    return blank


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


# --------------------------------------------------------------------------- helpers

HOSTILE = (
    "Ignore previous instructions {{ 7*7 }} ${HOME} %s {0} <|im_start|>system\n"
    "tab\there \x00 \x1b[31m ‮evil‬ ‍ café \U0001f600 \ud800 end"
)


def _contribution(
    values: dict[str, Any], *, fetched_at: datetime = T0, mode: DataMode = DataMode.LIVE
) -> LeadContribution:
    prov = tuple(
        FieldProvenance(
            canonical_path=path,
            source_name="acme",
            data_mode=mode,
            fetched_at=fetched_at,
            raw_field_path=f"raw.{path}",
            confidence_origin=ConfidenceOrigin.NONE,
            confidence=None,
            untrusted=isinstance(value, UntrustedText),
        )
        for path, value in values.items()
    )
    return LeadContribution(source_name="acme", values=values, provenance=prov)


def _untrusted(text: str) -> UntrustedText:
    return UntrustedText(value=text, truncated=False, original_length=len(text))


@dataclass(frozen=True)
class Ingested:
    source_run_id: uuid.UUID
    raw_id: uuid.UUID
    contribution_id: uuid.UUID


async def _ingest(
    writer: StoreWriter,
    run_id: uuid.UUID,
    values: dict[str, Any],
    *,
    fetched_at: datetime = T0,
    mode: DataMode = DataMode.LIVE,
    payload: Any = None,
) -> Ingested:
    """One source's batch through `StoreWriter`: raw payload plus one contribution."""

    def batch(s: Session) -> Ingested:
        sr = m.SourceRun(run_id=run_id, source_name="acme", resolved_mode=mode.value)
        s.add(sr)
        s.flush()
        raw = RawResponseRepository.add(
            s,
            source_run_id=sr.id,
            endpoint_key="people",
            request_fingerprint="fp",
            payload={"hits": []} if payload is None else payload,
            fetched_at=fetched_at,
            mode=mode,
            policy=RetentionPolicy(),
        )
        cid = write_contribution(
            s,
            _contribution(values, fetched_at=fetched_at, mode=mode),
            source_run_id=sr.id,
            raw_response_id=raw,
            data_mode=mode,
            fetched_at=fetched_at,
            lead_scope="person",
        )
        return Ingested(sr.id, raw, cid)

    return await writer.write_batch(batch)


def _count(engine: Engine, model: type[m.Base]) -> int:
    with Session(engine) as s:
        return s.scalar(sa.select(sa.func.count()).select_from(model)) or 0


# ------------------------------------------------------------------------ backend gate


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_each_leg_runs_on_the_engine_it_names(backend: Backend) -> None:
    dialect = backend.engine.dialect
    if backend.name == "sqlite":
        assert dialect.name == "sqlite"
    else:
        assert dialect.name == "postgresql"
        assert dialect.driver == "psycopg"
        with backend.engine.connect() as conn:
            version = conn.dialect.server_version_info
        assert version is not None
        assert isinstance(version[0], int)
        assert version[0] >= 16


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_postgres_gate_fails_loudly_when_no_server_is_available() -> None:
    with pytest.raises(pytest.fail.Exception, match="docker compose up -d postgres"):
        _resolve_postgres({}, find_server=lambda: None)


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_postgres_gate_failure_never_echoes_credentials() -> None:
    environ = {TEST_POSTGRES_URL_ENV: "postgresql://dev:s3cretpw@127.0.0.1:1/leadforge"}
    with pytest.raises(pytest.fail.Exception) as raised:
        _resolve_postgres(environ)
    message = str(raised.value)
    assert "s3cretpw" not in message
    assert "dev:***@" in message
    assert "docker compose up -d postgres" in message


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_this_module_has_no_skip_or_xfail_so_the_gate_cannot_go_silent() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    banned = {"skip", "skipif", "importorskip", "xfail"}
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in banned
        and isinstance(node.value, ast.Name)
        and node.value.id == "pytest"
    ]
    assert offenders == []


# ------------------------------------------------------------------------- migrations


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_upgrade_to_head_creates_every_modelled_table(blank: Backend) -> None:
    upgrade_to_head(_connection_free(blank))
    assert _tables(blank.engine) == MODEL_TABLES


def _connection_free(backend: Backend) -> str:
    """The URL form of `upgrade_to_head`; tests only, so the password may be shown."""
    return backend.url.render_as_string(hide_password=False)


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_upgrade_through_a_connection_and_twice_is_idempotent(blank: Backend) -> None:
    with blank.engine.begin() as conn:
        upgrade_to_head(conn)
    with blank.engine.begin() as conn:
        upgrade_to_head(conn)
    assert _tables(blank.engine) == MODEL_TABLES


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_head_matches_orm_metadata_with_no_drift(backend: Backend) -> None:
    with backend.engine.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": True}
        )
        assert compare_metadata(context, m.Base.metadata) == []


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_head_schema_has_the_nullable_links_and_set_null_foreign_key(
    backend: Backend,
) -> None:
    insp = inspect(backend.engine)
    contribution = {c["name"]: c for c in insp.get_columns("source_contribution")}
    field = {c["name"]: c for c in insp.get_columns("contribution_field")}
    assert contribution["raw_response_id"]["nullable"] is True
    assert field["confidence"]["nullable"] is True
    (fk,) = (
        f
        for f in insp.get_foreign_keys("source_contribution")
        if f["constrained_columns"] == ["raw_response_id"]
    )
    assert fk["name"] == "source_contribution_raw_response_id_fkey"
    assert fk["referred_table"] == "raw_response"
    assert fk["options"].get("ondelete") == "SET NULL"
    unique = {
        tuple(u["column_names"]) for u in insp.get_unique_constraints("identity_key")
    }
    assert ("key_type", "key_value") in unique


def _seed_at_0001(engine: Engine) -> uuid.UUID:
    """Rows written under the original schema; returns the field id."""
    run, sr, raw, contribution, field = (uuid.uuid4() for _ in range(5))
    with engine.begin() as conn:
        conn.execute(
            sa.insert(m.IngestionRun).values(id=run, started_at=T0, status="done")
        )
        conn.execute(
            sa.insert(m.SourceRun).values(
                id=sr, run_id=run, source_name="acme", resolved_mode="live"
            )
        )
        conn.execute(
            sa.insert(m.RawResponse).values(
                id=raw,
                source_run_id=sr,
                endpoint_key="e",
                request_fingerprint="f",
                payload={"k": "vé"},
                fetched_at=T0,
                retention_until=T0 + timedelta(days=30),
            )
        )
        conn.execute(
            sa.insert(m.SourceContribution).values(
                id=contribution,
                source_run_id=sr,
                raw_response_id=raw,
                source_name="acme",
                data_mode="live",
                fetched_at=T0,
                lead_scope="person",
            )
        )
        conn.execute(
            sa.insert(m.ContributionField).values(
                id=field,
                contribution_id=contribution,
                canonical_path="bio",
                value="hello",
                raw_field_path="raw.bio",
                confidence=0.5,
                untrusted=True,
                truncated=False,
                original_length=5,
            )
        )
    return field


def _field_state(engine: Engine, field: uuid.UUID) -> tuple[Any, Any]:
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(
                m.ContributionField.confidence,
                m.SourceContribution.raw_response_id,
            )
            .join_from(
                m.ContributionField,
                m.SourceContribution,
                m.ContributionField.contribution_id == m.SourceContribution.id,
            )
            .where(m.ContributionField.id == field)
        ).one()
    return row.confidence, row.raw_response_id


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_migrations_walk_up_to_head_and_down_to_base_keeping_rows(
    blank: Backend,
) -> None:
    _alembic(blank, "upgrade", "0001")
    # Postgres names the unnamed 0001 foreign key by convention; 0003 relies on it.
    fk_names = {
        f["name"]
        for f in inspect(blank.engine).get_foreign_keys("source_contribution")
        if f["constrained_columns"] == ["raw_response_id"]
    }
    assert fk_names == {EXPECT["unnamed_fk_name_at_0001"][blank.name]}

    field = _seed_at_0001(blank.engine)
    with pytest.raises(sa.exc.IntegrityError), blank.engine.begin() as conn:
        conn.execute(
            sa.insert(m.ContributionField).values(
                id=uuid.uuid4(),
                contribution_id=uuid.uuid4(),
                canonical_path="x",
                value="x",
                raw_field_path="x",
                confidence=None,
            )
        )
    state = _field_state(blank.engine, field)
    assert state[0] == 0.5

    for revision in ("0002", "0003"):
        _alembic(blank, "upgrade", revision)
        assert _field_state(blank.engine, field) == state
    for revision in ("0002", "0001"):
        _alembic(blank, "downgrade", revision)
        assert _field_state(blank.engine, field) == state

    downgrade_to_base(_connection_free(blank))
    assert _tables(blank.engine) == set()
    upgrade_to_head(_connection_free(blank))
    assert _tables(blank.engine) == MODEL_TABLES


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_downgrade_of_0003_refuses_when_a_payload_was_already_purged(
    blank: Backend,
) -> None:
    _alembic(blank, "upgrade", "0003")
    field = _seed_at_0001(blank.engine)
    with blank.engine.begin() as conn:
        conn.execute(sa.delete(m.RawResponse))
    assert _field_state(blank.engine, field)[1] is None

    with pytest.raises(sa.exc.IntegrityError):
        _alembic(blank, "downgrade", "0002")


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_downgrade_of_0002_refuses_when_a_field_has_no_confidence(
    blank: Backend,
) -> None:
    _alembic(blank, "upgrade", "0002")
    field = _seed_at_0001(blank.engine)
    with blank.engine.begin() as conn:
        conn.execute(
            sa.update(m.ContributionField)
            .where(m.ContributionField.id == field)
            .values(confidence=None)
        )

    with pytest.raises(sa.exc.IntegrityError):
        _alembic(blank, "downgrade", "0001")


# ------------------------------------------------------------ the ingestion round trip


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_contribution_and_raw_payload_round_trip_with_hostile_text(
    backend: Backend,
) -> None:
    writer = StoreWriter(backend.engine)
    run_id = await writer.begin_run(status="running", pool_size=4)
    sent = {
        "bio": _untrusted(HOSTILE),
        "nul": _untrusted("a\x00b"),
        "surrogate": _untrusted("a\ud800b\udc00c"),
        "bidi": _untrusted("‮abc‬⁦x⁩"),
        "cut": UntrustedText(value="abcdefghij", truncated=True, original_length=99),
        "emoji": _untrusted("\U0001f600" * 4000),
        "title": "plain \x00 trusted",
        "score": 3,
        "tags": ["a", "café", {"k": 1.5}],
    }
    payload = {"hits": [{"bio": HOSTILE, "n": 1, "nested": {"x": ["é", None]}}]}

    ing = await _ingest(writer, run_id, sent, payload=payload)

    def read(s: Session) -> tuple[Any, Any, Any]:
        stored = read_contribution(s, ing.contribution_id)
        return (
            stored,
            RawResponseRepository.get_payload(s, ing.raw_id),
            RawResponseRepository.retention_until(s, ing.raw_id),
        )

    stored, raw_payload, retention = await writer.write_batch(read)
    assert stored.values == sent
    assert stored.data_mode is DataMode.LIVE
    assert stored.fetched_at == T0
    assert stored.raw_response_id == ing.raw_id
    assert raw_payload == payload
    assert retention == T0 + timedelta(days=30)
    for path in ("bio", "nul", "surrogate", "bidi", "cut", "emoji"):
        assert isinstance(stored.values[path], UntrustedText), path
    assert not isinstance(stored.values["title"], UntrustedText)


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_a_trusted_marker_shaped_string_is_never_reclassified(
    backend: Backend,
) -> None:
    writer = StoreWriter(backend.engine)
    run_id = await writer.begin_run(status="running")
    marker = '{"untrusted": true, "value": "x", "truncated": false}'
    ing = await _ingest(writer, run_id, {"note": marker})

    stored = await writer.write_batch(
        lambda s: read_contribution(s, ing.contribution_id)
    )
    assert stored.values == {"note": marker}
    assert not isinstance(stored.values["note"], UntrustedText)


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_instants_are_equal_in_utc_whatever_the_offset_written(
    backend: Backend,
) -> None:
    writer = StoreWriter(backend.engine)
    run_id = await writer.begin_run(status="running")
    offsets = (timedelta(hours=14), timedelta(hours=-12), timedelta(0))
    for index, offset in enumerate(offsets):
        fetched = datetime(2026, 10, 5, 23, 30, tzinfo=timezone(offset))
        # A distinct observation each time: the same one is stored once (0006).
        ing = await _ingest(writer, run_id, {"a": index}, fetched_at=fetched)

        def read(s: Session, ing: Ingested = ing) -> tuple[Any, Any]:
            return (
                read_contribution(s, ing.contribution_id),
                RawResponseRepository.retention_until(s, ing.raw_id),
            )

        stored, retention = await writer.write_batch(read)
        assert stored.fetched_at == fetched
        assert stored.fetched_at.utcoffset() == timedelta(0)
        assert retention == fetched + timedelta(days=30)
        assert retention.utcoffset() == timedelta(0)


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_timestamps_of_unmanaged_columns_read_back_as_the_same_instant(
    backend: Backend,
) -> None:
    with Session(backend.engine) as s:
        s.add(m.IngestionRun(started_at=T0, status="running"))
        s.commit()
        started = s.scalars(sa.select(m.IngestionRun.started_at)).one()

    aware = started.tzinfo is not None
    assert aware is EXPECT["aware_datetimes_from_unmanaged_columns"][backend.name]
    assert (started if aware else started.replace(tzinfo=UTC)) == T0


# --------------------------------------------------------- engine-sensitive value rules


@pytest.mark.parametrize(
    ("column", "value"),
    [
        pytest.param("status", "x" * 33, id="over-long"),
        pytest.param("status", "a\x00b", id="nul"),
        pytest.param("status", "a\ud800b", id="lone-surrogate"),
    ],
)
async def test_string_columns_refuse_what_only_one_engine_would_refuse(
    backend: Backend, column: str, value: str
) -> None:
    writer = StoreWriter(backend.engine)

    def bad(s: Session) -> None:
        s.add(m.IngestionRun(started_at=T0, **{column: value}))

    with pytest.raises(m.ColumnValueError) as raised:
        await writer.write_batch(bad)
    assert "ingestion_run.status" in str(raised.value)
    assert value not in str(raised.value)
    assert _count(backend.engine, m.IngestionRun) == 0


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_a_string_exactly_at_its_limit_is_accepted(backend: Backend) -> None:
    writer = StoreWriter(backend.engine)
    run_id = await writer.begin_run(status="x" * 32)
    assert run_id is not None
    assert _count(backend.engine, m.IngestionRun) == 1


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_updating_a_string_column_is_checked_like_inserting_one(
    backend: Backend,
) -> None:
    writer = StoreWriter(backend.engine)
    run_id = await writer.begin_run(status="running")

    def finish(s: Session) -> None:
        run = s.get(m.IngestionRun, run_id)
        assert run is not None
        run.status = "x" * 33

    with pytest.raises(m.ColumnValueError, match=r"ingestion_run\.status"):
        await writer.write_batch(finish)
    with Session(backend.engine) as s:
        assert s.scalars(sa.select(m.IngestionRun.status)).one() == "running"


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_an_over_long_contribution_path_fails_the_whole_batch_identically(
    backend: Backend,
) -> None:
    writer = StoreWriter(backend.engine)
    run_id = await writer.begin_run(status="running")
    long_path = "p" * 256  # canonical_path is String(255)

    with pytest.raises(m.ColumnValueError, match=r"contribution_field\.canonical_path"):
        await _ingest(writer, run_id, {long_path: 1})

    assert _count(backend.engine, m.SourceContribution) == 0
    assert _count(backend.engine, m.RawResponse) == 0
    assert _count(backend.engine, m.SourceRun) == 0
    assert _count(backend.engine, m.IngestionRun) == 1  # the run record survives


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_without_the_orm_guard_only_postgres_refuses_an_over_long_string(
    backend: Backend,
) -> None:
    expected = EXPECT["core_overlong_string_error"][backend.name]
    insert = sa.insert(m.IngestionRun).values(
        id=uuid.uuid4(), started_at=T0, status="x" * 33
    )
    if expected is None:
        with backend.engine.begin() as conn:
            conn.execute(insert)  # SQLite stores it: lengths are not enforced
    else:
        with pytest.raises(expected), backend.engine.begin() as conn:
            conn.execute(insert)


# ------------------------------------------------------------------- constraints, guard


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_duplicate_match_key_raises_and_rolls_the_whole_batch_back(
    backend: Backend,
) -> None:
    writer = StoreWriter(backend.engine)

    def first(s: Session) -> uuid.UUID:
        identity = m.LeadIdentity(created_at=T0)
        s.add(identity)
        s.flush()
        s.add(
            m.IdentityKey(
                lead_identity_id=identity.id, key_type="email", key_value="a@b.c"
            )
        )
        return identity.id

    identity_id = await writer.write_batch(first)

    def duplicate(s: Session) -> None:
        other = m.LeadIdentity(created_at=T0)
        s.add(other)
        s.flush()
        # Same value under another type is a different key and is fine ...
        s.add(
            m.IdentityKey(
                lead_identity_id=other.id, key_type="linkedin", key_value="a@b.c"
            )
        )
        s.flush()
        # ... the same type and value is not.
        s.add(
            m.IdentityKey(
                lead_identity_id=other.id, key_type="email", key_value="a@b.c"
            )
        )

    with pytest.raises(sa.exc.IntegrityError):
        await writer.write_batch(duplicate)

    assert _count(backend.engine, m.LeadIdentity) == 1
    assert _count(backend.engine, m.IdentityKey) == 1
    with Session(backend.engine) as s:
        assert s.scalars(sa.select(m.IdentityKey.lead_identity_id)).one() == identity_id


# Verifies: specs/lead-source-adapters/requirements.md#9.5
def test_foreign_keys_are_enforced(backend: Backend) -> None:
    orphan = m.SourceRun(run_id=uuid.uuid4(), source_name="a", resolved_mode="live")
    with Session(backend.engine) as s:
        s.add(orphan)
        with pytest.raises(sa.exc.IntegrityError):
            s.commit()


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_append_only_guard_still_refuses_update_and_delete(
    backend: Backend,
) -> None:
    writer = StoreWriter(backend.engine)
    run_id = await writer.begin_run(status="running")
    ing = await _ingest(writer, run_id, {"a": 1})

    def update(s: Session) -> None:
        row = s.get(m.SourceContribution, ing.contribution_id)
        assert row is not None
        row.source_name = "other"

    def delete(s: Session) -> None:
        row = s.get(
            m.ContributionField, s.scalars(sa.select(m.ContributionField.id)).one()
        )
        assert row is not None
        s.delete(row)

    def bulk(s: Session) -> None:
        s.execute(sa.delete(m.ContributionField))

    for attempt in (update, delete, bulk):
        with pytest.raises(m.AppendOnlyViolationError):
            await writer.write_batch(attempt)

    assert _count(backend.engine, m.SourceContribution) == 1
    assert _count(backend.engine, m.ContributionField) == 1


# ------------------------------------------------------------------- retention and purg


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_purge_deletes_expired_payloads_and_detaches_their_contributions(
    backend: Backend,
) -> None:
    writer = StoreWriter(backend.engine)
    run_id = await writer.begin_run(status="running")
    old = await _ingest(
        writer, run_id, {"bio": _untrusted(HOSTILE)}, fetched_at=T0, payload={"o": 1}
    )
    fresh = await _ingest(
        writer, run_id, {"a": 1}, fetched_at=NOW - timedelta(days=1), payload={"f": 1}
    )
    synthetic = await _ingest(
        writer, run_id, {"a": 2}, fetched_at=T0, mode=DataMode.SYNTHETIC
    )

    def orphan(s: Session) -> uuid.UUID:
        sr = s.scalars(sa.select(m.SourceRun.id)).first()
        assert sr is not None
        return RawResponseRepository.add(
            s,
            source_run_id=sr,
            endpoint_key="x",
            request_fingerprint="y",
            payload={},
            fetched_at=T0,
            mode=DataMode.LIVE,
            policy=RetentionPolicy(),
        )

    orphan_id = await writer.write_batch(orphan)

    result = await writer.write_batch(
        lambda s: RawResponseRepository.purge_expired(s, now=NOW)
    )
    assert result == PurgeResult(deleted=2, detached_contributions=1)

    def read(s: Session) -> dict[str, Any]:
        return {
            "old": read_contribution(s, old.contribution_id),
            "fresh": read_contribution(s, fresh.contribution_id),
            "synthetic": read_contribution(s, synthetic.contribution_id),
            "old_payload": RawResponseRepository.get_payload(s, old.raw_id),
            "orphan_payload": RawResponseRepository.get_payload(s, orphan_id),
            "fresh_payload": RawResponseRepository.get_payload(s, fresh.raw_id),
            "synthetic_retention": RawResponseRepository.retention_until(
                s, synthetic.raw_id
            ),
        }

    got = await writer.write_batch(read)
    assert got["old"].raw_response_id is None
    assert got["old"].values == {"bio": _untrusted(HOSTILE)}  # fields survive
    assert got["fresh"].raw_response_id == fresh.raw_id
    assert got["synthetic"].raw_response_id == synthetic.raw_id
    assert got["old_payload"] is None
    assert got["orphan_payload"] is None
    assert got["fresh_payload"] == {"f": 1}
    assert got["synthetic_retention"] is None
    assert _count(backend.engine, m.SourceContribution) == 3
    assert _count(backend.engine, m.ContributionField) == 3
    assert _count(backend.engine, m.RawResponse) == 2


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_purge_of_unreferenced_payloads_detaches_nothing(
    backend: Backend,
) -> None:
    writer = StoreWriter(backend.engine)
    run_id = await writer.begin_run(status="running")

    def seed(s: Session) -> None:
        sr = m.SourceRun(run_id=run_id, source_name="acme", resolved_mode="live")
        s.add(sr)
        s.flush()
        for i in range(3):
            RawResponseRepository.add(
                s,
                source_run_id=sr.id,
                endpoint_key=f"e{i}",
                request_fingerprint="f",
                payload={"i": i},
                fetched_at=T0,
                mode=DataMode.LIVE,
                policy=RetentionPolicy(),
            )

    await writer.write_batch(seed)

    early = await writer.write_batch(
        lambda s: RawResponseRepository.purge_expired(s, now=T0 + timedelta(days=29))
    )
    assert early == PurgeResult(deleted=0, detached_contributions=0)
    on_time = await writer.write_batch(
        lambda s: RawResponseRepository.purge_expired(s, now=T0 + timedelta(days=30))
    )
    assert on_time == PurgeResult(deleted=3, detached_contributions=0)
    assert _count(backend.engine, m.RawResponse) == 0


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_purge_leaves_the_append_only_guard_intact(backend: Backend) -> None:
    writer = StoreWriter(backend.engine)
    run_id = await writer.begin_run(status="running")
    ing = await _ingest(writer, run_id, {"a": 1})
    await writer.write_batch(lambda s: RawResponseRepository.purge_expired(s, now=NOW))

    def update(s: Session) -> None:
        row = s.get(m.SourceContribution, ing.contribution_id)
        assert row is not None
        row.lead_scope = "company"

    with pytest.raises(m.AppendOnlyViolationError):
        await writer.write_batch(update)


# Verifies: specs/lead-source-adapters/requirements.md#9.5
async def test_concurrent_source_batches_all_commit_on_each_engine(
    backend: Backend,
) -> None:
    writer = StoreWriter(backend.engine)
    run_id = await writer.begin_run(status="running")

    results = await asyncio.gather(
        *(_ingest(writer, run_id, {"n": i}, payload={"i": i}) for i in range(4))
    )

    assert len({r.contribution_id for r in results}) == 4
    assert _count(backend.engine, m.SourceContribution) == 4
    assert _count(backend.engine, m.RawResponse) == 4


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_tie_resolution_round_trips_and_the_first_write_wins(
    backend: Backend,
) -> None:
    from leadforge.lead_ingestion.store.tie_resolutions import (
        TieResolutionRepository,
    )
    from leadforge.lead_ingestion.tie_resolution import (
        TieResolutionRecord,
        tie_key,
    )

    key = tie_key(("a.com", "b.com", "c.com"), ("a.com", "b.com"))
    first = TieResolutionRecord("b.com", ("a.com", "b.com"), "fake", "p1", T0)
    second = TieResolutionRecord("a.com", ("a.com", "b.com"), "other", "p2", NOW)
    with Session(backend.engine) as s:
        assert TieResolutionRepository(s).put(key, first) == first
        s.commit()
    with Session(backend.engine) as s:
        assert TieResolutionRepository(s).put(key, second) == first
        s.commit()
    with Session(backend.engine) as s:
        assert TieResolutionRepository(s).get(key) == first
        with pytest.raises(m.AppendOnlyViolationError):
            s.execute(sa.delete(m.PrimaryDomainTieResolution))


# Verifies: specs/lead-source-adapters/requirements.md#21.1
async def test_a_run_record_is_written_at_start_and_completed_through_the_writer(
    backend: Backend,
) -> None:
    from leadforge.lead_ingestion.mode_resolution import ModeResolution
    from leadforge.lead_ingestion.registry import SourceSettings
    from leadforge.lead_ingestion.run_record import RunStatus, build_run_record
    from leadforge.lead_ingestion.store.run_records import RunRecordRepository

    record = build_run_record(
        {
            "alpha": ModeResolution(DataMode.LIVE, "all declared credentials present"),
            "bravo": ModeResolution(DataMode.SYNTHETIC, "global override: synthetic"),
        },
        {"alpha": SourceSettings(), "bravo": SourceSettings()},
        started_at=T0,
        max_concurrent_sources=4,
        run_timeout_s=600.0,
        global_mode=None,
    )
    writer = StoreWriter(backend.engine)
    run_id = await writer.write_batch(lambda s: RunRecordRepository(s).start(record))

    started = await writer.write_batch(lambda s: RunRecordRepository(s).get(run_id))
    assert started is not None
    assert started.status == RunStatus.RUNNING
    assert started.started_at == T0
    assert started.config_snapshot == record.config_snapshot
    assert started.sources == tuple(sorted(record.sources, key=lambda x: x.source_name))

    await writer.write_batch(
        lambda s: RunRecordRepository(s).finish(
            run_id, status=RunStatus.COMPLETED, exit_code=0, finished_at=NOW
        )
    )
    done = await writer.write_batch(lambda s: RunRecordRepository(s).get(run_id))
    assert done is not None
    assert (done.status, done.exit_code, done.finished_at) == ("completed", 0, NOW)
    assert done.sources == started.sources


# Verifies: specs/lead-source-adapters/requirements.md#21.2
async def test_per_source_counts_commit_with_the_finish_on_each_engine(
    backend: Backend,
) -> None:
    from leadforge.lead_ingestion.run_record import (
        RunRecord,
        RunStatus,
        SourceCounts,
        SourceMode,
    )
    from leadforge.lead_ingestion.store.run_records import RunRecordRepository

    record = RunRecord(
        T0,
        2,
        {},
        (
            SourceMode("alpha", DataMode.LIVE, "r", live_access=True),
            SourceMode("bravo", DataMode.SYNTHETIC, "r", live_access=False),
        ),
    )
    writer = StoreWriter(backend.engine)
    run_id = await writer.write_batch(lambda s: RunRecordRepository(s).start(record))

    def finish(session: Session) -> None:
        repo = RunRecordRepository(session)
        repo.finish(run_id, status=RunStatus.COMPLETED, exit_code=0, finished_at=NOW)
        repo.record_source_counts(
            run_id,
            (
                SourceCounts("alpha", "rate_limited", 4, 2, 1, 3, {"api": 5}, ["w"]),
                SourceCounts("bravo", None, 0, 0, 0, 0, None, None),
            ),
        )

    await writer.write_batch(finish)
    with Session(backend.engine) as s:
        rows = {r.source_name: r for r in s.scalars(sa.select(m.SourceRun))}
    a = rows["alpha"]
    assert (a.failure_class, a.leads_found, a.retries, a.throttle_waits) == (
        "rate_limited",
        4,
        2,
        1,
    )
    assert (a.http_429_count, a.quota_remaining, a.warnings) == (3, {"api": 5}, ["w"])
    assert (a.live_access, rows["bravo"].live_access) == (True, False)
    assert (a.credits_consumed, a.credential_present) == (None, None)


# Verifies: specs/lead-source-adapters/requirements.md#21.5
async def test_the_run_report_is_built_from_the_store_on_each_engine(
    backend: Backend,
) -> None:
    from leadforge.lead_ingestion.run_record import (
        RunRecord,
        RunStatus,
        SourceCounts,
        SourceMode,
    )
    from leadforge.lead_ingestion.run_report import (
        build_run_report,
        render_run_report,
    )
    from leadforge.lead_ingestion.store.run_records import RunRecordRepository

    record = RunRecord(
        T0,
        2,
        {"sources": {"alpha": {"live_access": "gated"}}},
        (
            SourceMode("bravo", DataMode.SYNTHETIC, "r", live_access=False),
            SourceMode("alpha", DataMode.LIVE, "r", live_access=True),
        ),
    )
    writer = StoreWriter(backend.engine)
    run_id = await writer.write_batch(lambda s: RunRecordRepository(s).start(record))

    def finish(session: Session) -> None:
        repo = RunRecordRepository(session)
        repo.finish(run_id, status=RunStatus.COMPLETED, exit_code=0, finished_at=NOW)
        repo.record_source_counts(
            run_id,
            (
                SourceCounts("alpha", "rate_limited", 4, 2, 1, 3, {"api": 5}, ["w"]),
                SourceCounts("bravo", None, 0, 0, 0, 0, None, None),
            ),
        )

    await writer.write_batch(finish)
    with Session(backend.engine) as s:
        report = build_run_report(s)
    assert [r.source_name for r in report.sources] == ["alpha", "bravo"]
    alpha, bravo = report.sources
    assert (alpha.live_access, bravo.live_access) == ("gated", "unavailable")
    assert (alpha.failure_class, alpha.leads_normalized) == ("rate_limited", 4)
    assert dict(alpha.quota_remaining or {}) == {"api": 5}
    assert report.started_at == T0
    text = render_run_report(report)
    assert "quota=api:5" in text
    assert "warning: w" in text
