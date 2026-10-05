"""Raw provider payload retention (task 6.5, requirement 9.8)."""

import ast
import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, inspect
from sqlalchemy.orm import Session, class_mapper

import leadforge.lead_ingestion as slice_pkg
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.raw_responses import (
    DEFAULT_LIVE_RETENTION,
    RAW_RETENTION_DAYS_ENV,
    RawResponseRepository,
    RetentionConfigError,
    RetentionPolicy,
)
from leadforge.lead_ingestion.store.transactions import StoreWriter

SLICE_ROOT = Path(slice_pkg.__file__).resolve().parent
T0 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
CANONICAL: tuple[type[m.Base], ...] = (
    m.IngestionRun,
    m.SourceRun,
    m.LeadIdentity,
    m.IdentityKey,
    m.SourceContribution,
    m.ContributionField,
    m.CanonicalLeadRow,
    m.CanonicalFieldProvenance,
)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    upgrade_to_head(eng.url.render_as_string(hide_password=True))
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s


@pytest.fixture
def source_run_id(session: Session) -> uuid.UUID:
    run = m.IngestionRun(started_at=T0, status="running")
    session.add(run)
    session.flush()
    sr = m.SourceRun(run_id=run.id, source_name="x", resolved_mode="live")
    session.add(sr)
    session.commit()
    return sr.id


def _add(
    session: Session,
    source_run_id: uuid.UUID,
    mode: DataMode,
    fetched_at: datetime = T0,
    policy: RetentionPolicy | None = None,
    payload: object = None,
) -> uuid.UUID:
    rid = RawResponseRepository.add(
        session,
        source_run_id=source_run_id,
        endpoint_key="people.search",
        request_fingerprint="abc",
        payload={"k": "v"} if payload is None else payload,
        fetched_at=fetched_at,
        mode=mode,
        policy=policy or RetentionPolicy(),
    )
    session.commit()
    return rid


def _ids(session: Session) -> set[uuid.UUID]:
    return set(session.scalars(sa.select(m.RawResponse.id)))


# --- policy -----------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_default_live_window_is_thirty_days() -> None:
    assert timedelta(days=30) == DEFAULT_LIVE_RETENTION
    assert RetentionPolicy().retention_until(DataMode.LIVE, T0) == T0 + timedelta(
        days=30
    )


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_synthetic_mode_is_indefinite() -> None:
    assert RetentionPolicy().retention_until(DataMode.SYNTHETIC, T0) is None
    assert (
        RetentionPolicy(live_window=timedelta(seconds=1)).retention_until(
            DataMode.SYNTHETIC, T0
        )
        is None
    )


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_live_window_is_configurable() -> None:
    pol = RetentionPolicy(live_window=timedelta(days=7))
    assert pol.retention_until(DataMode.LIVE, T0) == T0 + timedelta(days=7)


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_window_comes_from_environment_and_defaults_when_unset() -> None:
    assert RetentionPolicy.from_environ({}).live_window == timedelta(days=30)
    assert RetentionPolicy.from_environ({RAW_RETENTION_DAYS_ENV: " "}).live_window == (
        timedelta(days=30)
    )
    assert RetentionPolicy.from_environ({RAW_RETENTION_DAYS_ENV: "7"}).live_window == (
        timedelta(days=7)
    )


@pytest.mark.parametrize("bad", ["0", "-3", "abc", "1.5", "nan"])
def test_bad_window_from_environment_is_rejected_without_echoing(bad: str) -> None:
    with pytest.raises(RetentionConfigError) as ei:
        RetentionPolicy.from_environ({RAW_RETENTION_DAYS_ENV: bad})
    assert "abc" not in str(ei.value)  # never quote arbitrary input


@pytest.mark.parametrize("bad", [timedelta(0), timedelta(days=-1)])
def test_non_positive_window_is_rejected(bad: timedelta) -> None:
    with pytest.raises(RetentionConfigError):
        RetentionPolicy(live_window=bad)


def test_naive_fetched_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        RetentionPolicy().retention_until(DataMode.LIVE, datetime(2026, 1, 1))


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_retention_until_is_normalised_to_utc() -> None:
    plus5 = timezone(timedelta(hours=5))
    local = datetime(2026, 10, 5, 17, 0, tzinfo=plus5)  # == T0
    result = RetentionPolicy().retention_until(DataMode.LIVE, local)
    assert result == T0 + timedelta(days=30)
    assert result is not None
    assert result.utcoffset() == timedelta(0)


# --- repository -------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_payload_round_trips_only_through_the_explicit_repository(
    session: Session, source_run_id: uuid.UUID
) -> None:
    payload = {"people": [{"name": "Ada", "n": 1}], "ünï": "ç"}
    rid = _add(session, source_run_id, DataMode.LIVE, payload=payload)
    session.expire_all()
    assert RawResponseRepository.get_payload(session, rid) == payload
    assert RawResponseRepository.get_payload(session, uuid.uuid4()) is None


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_live_row_gets_expiry_and_synthetic_row_none(
    session: Session, source_run_id: uuid.UUID
) -> None:
    live = _add(session, source_run_id, DataMode.LIVE)
    syn = _add(session, source_run_id, DataMode.SYNTHETIC)
    assert RawResponseRepository.retention_until(session, live) == T0 + timedelta(
        days=30
    )
    assert RawResponseRepository.retention_until(session, syn) is None
    assert RawResponseRepository.retention_until(session, uuid.uuid4()) is None


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_retention_until_reads_back_timezone_aware(
    session: Session, source_run_id: uuid.UUID
) -> None:
    live = _add(session, source_run_id, DataMode.LIVE)
    session.expire_all()
    got = RawResponseRepository.retention_until(session, live)
    assert got is not None
    assert got.tzinfo is not None
    assert got.utcoffset() == timedelta(0)


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_payload_column_is_deferred_so_default_selects_exclude_it(
    session: Session, source_run_id: uuid.UUID
) -> None:
    rid = _add(session, source_run_id, DataMode.LIVE)
    session.expunge_all()
    row = session.get(m.RawResponse, rid)
    assert row is not None
    assert "payload" in inspect(row).unloaded
    stmt = sa.select(m.RawResponse)
    assert "payload" not in str(stmt.compile()).split("FROM")[0]


# --- purge ------------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_purge_deletes_expired_live_rows_and_keeps_unexpired(
    session: Session, source_run_id: uuid.UUID
) -> None:
    old = _add(session, source_run_id, DataMode.LIVE, fetched_at=T0)
    fresh = _add(
        session, source_run_id, DataMode.LIVE, fetched_at=T0 + timedelta(days=20)
    )
    result = RawResponseRepository.purge_expired(session, now=T0 + timedelta(days=31))
    session.commit()
    assert result.deleted == 1
    assert _ids(session) == {fresh}
    assert old not in _ids(session)


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_purge_boundary_row_is_expired_exactly_at_its_instant(
    session: Session, source_run_id: uuid.UUID
) -> None:
    rid = _add(session, source_run_id, DataMode.LIVE)
    expiry = T0 + timedelta(days=30)
    just_before = RawResponseRepository.purge_expired(
        session, now=expiry - timedelta(microseconds=1)
    )
    assert just_before.deleted == 0
    assert rid in _ids(session)
    at = RawResponseRepository.purge_expired(session, now=expiry)
    session.commit()
    assert at.deleted == 1
    assert rid not in _ids(session)


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_purge_never_deletes_indefinite_rows(
    session: Session, source_run_id: uuid.UUID
) -> None:
    syn = _add(session, source_run_id, DataMode.SYNTHETIC)
    far_future = T0 + timedelta(days=365 * 1000)
    result = RawResponseRepository.purge_expired(session, now=far_future)
    session.commit()
    assert result.deleted == 0
    assert _ids(session) == {syn}


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_purge_compares_instants_not_wall_clock_across_offsets(
    session: Session, source_run_id: uuid.UUID
) -> None:
    rid = _add(session, source_run_id, DataMode.LIVE)  # expires T0 + 30d (UTC)
    plus14 = timezone(timedelta(hours=14))
    # One second before expiry, written in a zone whose wall clock is 14h ahead:
    # a wall-clock comparison would call it expired.
    early = (T0 + timedelta(days=30) - timedelta(seconds=1)).astimezone(plus14)
    assert RawResponseRepository.purge_expired(session, now=early).deleted == 0
    assert rid in _ids(session)
    minus12 = timezone(timedelta(hours=-12))
    late = (T0 + timedelta(days=30, seconds=1)).astimezone(minus12)
    assert RawResponseRepository.purge_expired(session, now=late).deleted == 1


def test_purge_rejects_naive_now(session: Session) -> None:
    with pytest.raises(ValueError, match="timezone"):
        RawResponseRepository.purge_expired(session, now=datetime(2026, 1, 1))


def _contribution_row(
    source_run_id: uuid.UUID, raw_response_id: uuid.UUID
) -> m.SourceContribution:
    return m.SourceContribution(
        source_run_id=source_run_id,
        raw_response_id=raw_response_id,
        source_name="x",
        data_mode="live",
        fetched_at=T0,
        lead_scope="person",
    )


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_purge_deletes_expired_rows_a_contribution_references_and_detaches_it(
    session: Session, source_run_id: uuid.UUID
) -> None:
    referenced = _add(session, source_run_id, DataMode.LIVE)
    unreferenced = _add(session, source_run_id, DataMode.LIVE)
    row = _contribution_row(source_run_id, referenced)
    session.add(row)
    session.commit()
    contribution_id = row.id

    result = RawResponseRepository.purge_expired(session, now=T0 + timedelta(days=99))
    session.commit()

    assert (result.deleted, result.detached_contributions) == (2, 1)
    assert _ids(session) == set()
    assert unreferenced not in _ids(session)
    session.expire_all()
    kept = session.get(m.SourceContribution, contribution_id)
    assert kept is not None
    assert kept.raw_response_id is None


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_purge_refreshes_a_contribution_already_loaded_in_the_session(
    session: Session, source_run_id: uuid.UUID
) -> None:
    rid = _add(session, source_run_id, DataMode.LIVE)
    row = _contribution_row(source_run_id, rid)
    session.add(row)
    session.commit()
    assert row.raw_response_id == rid  # loaded, so a stale copy is possible

    RawResponseRepository.purge_expired(session, now=T0 + timedelta(days=99))

    assert row.raw_response_id is None


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_purge_counts_each_detached_contribution_once(
    session: Session, source_run_id: uuid.UUID
) -> None:
    rid = _add(session, source_run_id, DataMode.LIVE)
    session.add_all(
        [_contribution_row(source_run_id, rid), _contribution_row(source_run_id, rid)]
    )
    session.commit()
    result = RawResponseRepository.purge_expired(session, now=T0 + timedelta(days=99))
    assert (result.deleted, result.detached_contributions) == (1, 2)


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_purge_leaves_contributions_of_unexpired_and_indefinite_rows_attached(
    session: Session, source_run_id: uuid.UUID
) -> None:
    fresh = _add(session, source_run_id, DataMode.LIVE)
    synthetic = _add(session, source_run_id, DataMode.SYNTHETIC)
    session.add_all(
        [
            _contribution_row(source_run_id, fresh),
            _contribution_row(source_run_id, synthetic),
        ]
    )
    session.commit()
    result = RawResponseRepository.purge_expired(session, now=T0 + timedelta(days=1))
    session.commit()
    assert (result.deleted, result.detached_contributions) == (0, 0)
    refs = set(session.scalars(sa.select(m.SourceContribution.raw_response_id)))
    assert refs == {fresh, synthetic}


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_purge_never_updates_or_deletes_contribution_rows_through_the_orm(
    engine: Engine, session: Session, source_run_id: uuid.UUID
) -> None:
    rid = _add(session, source_run_id, DataMode.LIVE)
    session.add(_contribution_row(source_run_id, rid))
    session.commit()
    statements: list[str] = []

    def record(conn: object, cursor: object, statement: str, *rest: object) -> None:
        statements.append(statement)

    sa.event.listen(engine, "before_cursor_execute", record)
    try:
        RawResponseRepository.purge_expired(session, now=T0 + timedelta(days=99))
        session.commit()
    finally:
        sa.event.remove(engine, "before_cursor_execute", record)

    touching = [
        q
        for q in statements
        if "source_contribution" in q
        and q.lstrip().upper().startswith(("UPDATE", "DELETE"))
    ]
    assert touching == []  # the database detaches the row (ON DELETE SET NULL)
    assert any(
        q.lstrip().upper().startswith("DELETE FROM RAW_RESPONSE") for q in statements
    )


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_detaching_relies_on_foreign_key_enforcement_of_the_engine(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path / 'plain.db'}"
    upgrade_to_head(url)
    plain = sa.create_engine(url)  # no PRAGMA foreign_keys: SQLite ignores the FK
    try:
        with Session(plain) as s:
            run = m.IngestionRun(started_at=T0, status="running")
            s.add(run)
            s.flush()
            sr = m.SourceRun(run_id=run.id, source_name="x", resolved_mode="live")
            s.add(sr)
            s.flush()
            rid = RawResponseRepository.add(
                s,
                source_run_id=sr.id,
                endpoint_key="e",
                request_fingerprint="f",
                payload={},
                fetched_at=T0,
                mode=DataMode.LIVE,
                policy=RetentionPolicy(),
            )
            s.add(_contribution_row(sr.id, rid))
            s.commit()
            RawResponseRepository.purge_expired(s, now=T0 + timedelta(days=99))
            s.commit()
            s.expire_all()
            dangling = s.scalar(sa.select(m.SourceContribution.raw_response_id))
        assert dangling == rid  # documents why create_store_engine must be used
    finally:
        plain.dispose()


def test_store_engine_enforces_foreign_keys(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(sa.text("PRAGMA foreign_keys")).scalar() == 1


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_repository_works_inside_write_batch_returning_plain_values(
    engine: Engine, source_run_id: uuid.UUID
) -> None:
    writer = StoreWriter(engine)
    policy = RetentionPolicy(live_window=timedelta(days=1))

    def write(s: Session) -> uuid.UUID:
        return RawResponseRepository.add(
            s,
            source_run_id=source_run_id,
            endpoint_key="e",
            request_fingerprint="f",
            payload=[1, 2],
            fetched_at=T0,
            mode=DataMode.LIVE,
            policy=policy,
        )

    rid = asyncio.run(writer.write_batch(write))
    assert isinstance(rid, uuid.UUID)
    purged = asyncio.run(
        writer.write_batch(
            lambda s: (
                RawResponseRepository.purge_expired(
                    s, now=T0 + timedelta(days=2)
                ).deleted
            )
        )
    )
    assert purged == 1


# --- structure: default queries exclude raw payloads --------------------------


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_no_canonical_model_selects_from_or_relates_to_raw_response() -> None:
    for model in CANONICAL:
        froms = sa.select(model).get_final_froms()
        assert m.RawResponse.__table__ not in froms, model
        for rel in class_mapper(model).relationships:
            assert rel.mapper.class_ is not m.RawResponse, (model, rel)
    assert not class_mapper(m.RawResponse).relationships


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_only_the_repository_module_references_the_raw_response_model() -> None:
    allowed = {Path("store/models.py"), Path("store/raw_responses.py")}
    offenders: list[str] = []
    for path in SLICE_ROOT.rglob("*.py"):
        rel = path.relative_to(SLICE_ROOT)
        if "tests" in rel.parts or "migrations" in rel.parts or rel in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Name) and node.id == "RawResponse") or (
                isinstance(node, ast.Attribute) and node.attr == "RawResponse"
            ):
                offenders.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and any(
                a.name == "RawResponse" for a in node.names
            ):
                offenders.append(f"{rel}:{node.lineno} import")
    assert offenders == []
