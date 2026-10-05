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


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_purge_keeps_rows_a_contribution_still_references_and_counts_them(
    session: Session, source_run_id: uuid.UUID
) -> None:
    kept = _add(session, source_run_id, DataMode.LIVE)
    gone = _add(session, source_run_id, DataMode.LIVE)
    session.add(
        m.SourceContribution(
            source_run_id=source_run_id,
            raw_response_id=kept,
            source_name="x",
            data_mode="live",
            fetched_at=T0,
            lead_scope="person",
        )
    )
    session.commit()
    result = RawResponseRepository.purge_expired(session, now=T0 + timedelta(days=99))
    session.commit()
    assert (result.deleted, result.skipped_referenced) == (1, 1)
    assert _ids(session) == {kept}
    assert gone not in _ids(session)


# Verifies: specs/lead-source-adapters/requirements.md#9.8
def test_purge_does_not_touch_append_only_contribution_tables(
    session: Session, source_run_id: uuid.UUID
) -> None:
    rid = _add(session, source_run_id, DataMode.LIVE)
    session.add(
        m.SourceContribution(
            source_run_id=source_run_id,
            raw_response_id=rid,
            source_name="x",
            data_mode="live",
            fetched_at=T0,
            lead_scope="person",
        )
    )
    session.commit()
    RawResponseRepository.purge_expired(session, now=T0 + timedelta(days=99))
    session.commit()
    assert (
        session.scalar(sa.select(sa.func.count()).select_from(m.SourceContribution))
        == 1
    )


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
