"""Credential values never reach logs or database rows (task 8.4, 10.5, 21.3)."""

import base64
import copy
import json
import re
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import structlog
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from leadforge.lead_ingestion import log_redaction
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.env_example import BUILTIN_SETTINGS
from leadforge.lead_ingestion.log_redaction import (
    MAX_VALUE_CHARS,
    Redactor,
    active_redactor,
    configure_logging,
    credential_manifest,
    redact,
    secrets_from_environ,
)
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
)
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.contributions import write_contribution
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.lead_ingestion.store.raw_responses import (
    RawResponseRepository,
    RetentionPolicy,
)

SECRET = "sk-live-SUPERSECRET-0123456789"
T0 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
MASK = "***"


@pytest.fixture(autouse=True)
def _restore_structlog() -> Iterator[None]:
    before = structlog.get_config().copy()
    before["processors"] = list(before["processors"])
    yield
    structlog.configure(**before)
    log_redaction._set_active(Redactor(()))


def _event(**kw: Any) -> dict[str, Any]:
    return {"event": "x", **kw}


# ---- manifest and seeding ------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_manifest_is_builtin_settings_plus_every_adapter_variable(
    tmp_path: Path,
) -> None:
    names = credential_manifest(SourceRegistry.discover())
    for variable, *_ in BUILTIN_SETTINGS:
        assert variable in names
    assert len(names) == len(set(names))


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_secrets_come_from_the_injected_environ_only(monkeypatch: Any) -> None:
    monkeypatch.setenv("A_KEY", "process-env-value-123")
    got = secrets_from_environ(
        {"A_KEY": SECRET, "OTHER": "zzzzzzzzzzzz"}, ["A_KEY", "B"]
    )
    assert got == (SECRET,)


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_non_secret_llm_settings_and_url_user_are_not_seeded() -> None:
    env = {
        "LLM_PROVIDER": "anthropic-provider",
        "LLM_MODEL": "some-model-name-long",
        "DATABASE_URL": "postgresql://bob:s3cretpassw0rd@db.example/leadforge",
        "A_KEY": SECRET,
    }
    got = secrets_from_environ(
        env, ["LLM_PROVIDER", "LLM_MODEL", "DATABASE_URL", "A_KEY"]
    )
    assert SECRET in got
    assert "s3cretpassw0rd" in got
    assert "anthropic-provider" not in got
    assert "some-model-name-long" not in got
    assert "postgresql://bob:s3cretpassw0rd@db.example/leadforge" not in got


# ---- the value scrubber --------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_accidental_interpolation_is_scrubbed() -> None:
    r = Redactor([SECRET])
    assert r.redact(f"calling with key={SECRET} now") == f"calling with key={MASK} now"
    assert r.redact("nothing here") == "nothing here"


# Verifies: specs/lead-source-adapters/requirements.md#10.5
@pytest.mark.parametrize("blank", ["", " ", "\n\t ", "   "])
def test_blank_secret_never_turns_every_string_into_mask(blank: str) -> None:
    r = Redactor([blank, SECRET])
    assert r.redact("hello world") == "hello world"
    assert r.redact("") == ""
    assert Redactor([blank]).redact("hello world") == "hello world"


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_very_short_secrets_are_ignored_and_mid_length_match_whole_tokens_only() -> (
    None
):
    assert Redactor(["ab1"]).redact("ab1 and cab1d") == "ab1 and cab1d"
    r = Redactor(["k7x9q"])
    assert r.redact("key k7x9q.") == f"key {MASK}."
    assert r.redact("word xk7x9qy and k7x9q_ and 1k7x9q") == (
        "word xk7x9qy and k7x9q_ and 1k7x9q"
    )


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_long_secret_is_scrubbed_even_inside_another_word() -> None:
    assert Redactor([SECRET]).redact(f"pre{SECRET}post") == f"pre{MASK}post"


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_regex_special_characters_in_values_match_literally() -> None:
    weird = "a.b*c+d?(e)[f]{g}|h^i$j\\k"
    r = Redactor([weird])
    assert r.redact(f"x {weird} y") == f"x {MASK} y"
    assert r.redact("a-b-c-d-e-f-g-h-i-j-k-xxx") == "a-b-c-d-e-f-g-h-i-j-k-xxx"


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_overlapping_secrets_prefer_the_longest_and_leave_no_fragment() -> None:
    r = Redactor(["abcdefgh12345678", "abcdefgh"])
    assert r.redact("v=abcdefgh12345678!") == f"v={MASK}!"
    r2 = Redactor(["abcdefgh12", "efgh12ZZZZ"])  # overlap at the join
    out = r2.redact("abcdefgh12ZZZZ")
    assert "abcd" not in out
    assert "ZZZZ" not in out


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_common_encodings_of_the_value_are_scrubbed() -> None:
    secret = "p@ss word/+=secret&1"
    r = Redactor([secret])
    from urllib.parse import quote, quote_plus

    b64 = base64.b64encode(secret.encode()).decode()
    urlsafe = base64.urlsafe_b64encode(secret.encode()).decode()
    for variant in (
        secret,
        quote(secret, safe=""),
        quote_plus(secret),
        b64,
        b64.rstrip("="),
        urlsafe,
        urlsafe.rstrip("="),
        json.dumps(secret)[1:-1],
    ):
        assert variant not in r.redact(f"https://h/?k={variant}&z=1"), variant


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_value_with_surrounding_whitespace_is_matched_stripped() -> None:
    assert Redactor([f"  {SECRET}\n"]).redact(f"k={SECRET}") == f"k={MASK}"


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_many_secrets_stay_fast() -> None:
    secrets = [f"secret-value-{i:06d}-xyz" for i in range(2000)]
    r = Redactor(secrets)
    text = "log line " * 200 + secrets[1999] + " tail"
    start = time.perf_counter()
    for _ in range(50):
        out = r.redact(text)
    assert time.perf_counter() - start < 5
    assert secrets[1999] not in out


# ---- recursion -----------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_nested_containers_keys_and_exceptions_are_scrubbed() -> None:
    r = Redactor([SECRET])
    exc = RuntimeError(f"boom {SECRET}")
    value = {
        f"k-{SECRET}": [f"a{SECRET}", ("t", {"deep": f"{SECRET}"})],
        "s": {f"set{SECRET}"},
        "b": f"bytes-{SECRET}".encode(),
        "e": exc,
        "n": 5,
        "none": None,
        "flag": True,
    }
    out = r.redact_value(value)
    assert SECRET not in repr(out)
    assert out["n"] == 5
    assert out["none"] is None
    assert out["flag"] is True
    assert isinstance(out[f"k-{MASK}"][1], tuple)


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_cyclic_and_deeply_nested_values_terminate() -> None:
    r = Redactor([SECRET])
    a: list[Any] = [SECRET]
    a.append(a)
    assert SECRET not in repr(r.redact_value(a))
    deep: Any = SECRET
    for _ in range(500):
        deep = [deep]
    assert SECRET not in repr(r.redact_value(deep))


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_object_whose_text_holds_a_secret_is_replaced_by_scrubbed_text() -> None:
    class Holder:
        def __str__(self) -> str:
            return f"Holder({SECRET})"

    class Clean:
        pass

    r = Redactor([SECRET])
    assert r.redact_value(Holder()) == f"Holder({MASK})"
    c = Clean()
    assert r.redact_value(c) is c


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_processor_does_not_mutate_the_callers_event_dict() -> None:
    r = Redactor([SECRET])
    original = _event(a=[SECRET, {"k": SECRET}], raw={"x": 1}, big="y" * 5000)
    snapshot = copy.deepcopy(original)
    out = r(None, "info", original)
    assert original == snapshot
    assert out is not original
    assert SECRET not in repr(out)


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_secret_split_across_two_fields_is_not_detected_documented_limit() -> None:
    r = Redactor([SECRET])
    out = r(None, "info", _event(a=SECRET[:12], b=SECRET[12:]))
    # Known false negative (choices.md): per-value matching cannot see a split.
    assert out["a"] + out["b"] == SECRET


# ---- exceptions in the chain ---------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_unrendered_exc_info_is_rendered_and_scrubbed() -> None:
    r = Redactor([SECRET])
    try:
        raise ValueError(f"bad key {SECRET}")
    except ValueError:
        import sys

        out = r(None, "error", _event(exc_info=sys.exc_info()))
    assert "exc_info" not in out
    assert "ValueError" in out["exception"]
    assert "Traceback" in out["exception"]
    assert SECRET not in repr(out)


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_exc_info_true_and_instance_forms_are_scrubbed() -> None:
    r = Redactor([SECRET])
    exc = ValueError(f"bad key {SECRET}")
    out = r(None, "error", _event(exc_info=exc))
    assert SECRET not in repr(out)
    assert "ValueError" in out["exception"]
    try:
        raise KeyError(SECRET)
    except KeyError:
        out2 = r(None, "error", _event(exc_info=True))
    assert SECRET not in repr(out2)


# ---- raw payload guard ---------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#21.3
@pytest.mark.parametrize(
    "key",
    [
        "raw",
        "payload",
        "body",
        "response",
        "raw_response",
        "provider_payload",
        "response_body",
        "json_body",
        "RAW_PAYLOAD",
        "raw_json",
    ],
)
def test_raw_payload_keys_are_dropped_to_a_size_marker(key: str) -> None:
    r = Redactor([SECRET])
    out = r(None, "info", _event(**{key: {"email": "a@b.c", "n": [1, 2, 3]}}))
    assert "a@b.c" not in repr(out)
    assert "omitted" in out[key]
    assert "dict" in out[key]


# Verifies: specs/lead-source-adapters/requirements.md#21.3
def test_raw_payload_key_nested_in_another_value_is_dropped() -> None:
    r = Redactor([])
    out = r(None, "info", _event(result={"status": 200, "body": {"people": ["x"]}}))
    assert out["result"]["status"] == 200
    assert "people" not in repr(out)


# Verifies: specs/lead-source-adapters/requirements.md#21.3
def test_non_raw_keys_with_similar_names_are_kept() -> None:
    r = Redactor([])
    out = r(None, "info", _event(confidence_raw="9", rawness=1, bodyguard="x", n_raw=2))
    assert out["confidence_raw"] == "9"
    assert out["rawness"] == 1
    assert out["bodyguard"] == "x"


# Verifies: specs/lead-source-adapters/requirements.md#21.3
def test_oversized_strings_are_truncated_with_a_count() -> None:
    r = Redactor([])
    out = r(None, "info", _event(note="y" * (MAX_VALUE_CHARS + 500)))
    assert len(out["note"]) < MAX_VALUE_CHARS + 100
    assert "truncated 500" in out["note"]


# Verifies: specs/lead-source-adapters/requirements.md#21.3
def test_oversized_container_under_an_innocent_key_is_omitted() -> None:
    r = Redactor([])
    big = {f"k{i}": "v" * 50 for i in range(200)}
    out = r(None, "info", _event(result=big, small={"a": 1}))
    assert "omitted" in out["result"]
    assert out["small"] == {"a": 1}


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_secret_is_redacted_before_truncation_so_no_fragment_survives() -> None:
    r = Redactor([SECRET])
    pad = "z" * (MAX_VALUE_CHARS - 10)
    out = r(None, "info", _event(note=pad + SECRET + "tail"))
    assert SECRET[:6] not in out["note"]


# Verifies: specs/lead-source-adapters/requirements.md#21.3
def test_traceback_text_gets_a_longer_bound_than_ordinary_values() -> None:
    r = Redactor([])
    text = "line\n" * (MAX_VALUE_CHARS // 2)  # longer than the ordinary bound
    out = r(None, "info", _event(exception=text))
    assert out["exception"] == text


# ---- configure_logging and capture_logs ---------------------------------------


def _json_lines(capsys: Any) -> list[dict[str, Any]]:
    text = capsys.readouterr().out
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_configure_logging_scrubs_emitted_lines_end_to_end(capsys: Any) -> None:
    configure_logging({"DATABASE_URL": f"postgresql://u:{SECRET}@h/d"})
    log = structlog.get_logger("t")
    try:
        raise RuntimeError(f"fail {SECRET}")
    except RuntimeError:
        log.error(
            "oops %s",
            f"pos-{SECRET}",
            note=f"n-{SECRET}",
            payload={"a": 1},
            exc_info=True,
        )
    out = capsys.readouterr().err
    assert out.strip()
    assert SECRET not in out
    line = json.loads(out.splitlines()[0])
    assert line["event"] == f"oops pos-{MASK}"
    assert "omitted" in line["payload"]


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_configure_logging_seeds_from_the_manifest_and_sets_active_redactor() -> None:
    first = SourceRegistry.discover()
    configure_logging({"DATABASE_URL": "postgresql://u:pw-pw-pw-pw@h/d"}, first)
    assert redact("x pw-pw-pw-pw y") == f"x {MASK} y"
    assert active_redactor().redact("pw-pw-pw-pw") == MASK


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_reconfiguring_replaces_previous_secrets() -> None:
    configure_logging({"DATABASE_URL": "postgresql://u:first-secret-1@h/d"})
    configure_logging({"DATABASE_URL": "postgresql://u:second-secret-2@h/d"})
    assert redact("first-secret-1") == "first-secret-1"
    assert redact("second-secret-2") == MASK


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_capture_logs_still_works_after_configure_logging() -> None:
    configure_logging({"DATABASE_URL": "postgresql://u:capture-secret-1@h/d"})
    log = structlog.get_logger("t")
    with capture_logs() as logs:
        log.info("evt", k="v")
    assert [e["event"] for e in logs] == ["evt"]
    # Documented: capture_logs disables the configured chain, so captured entries are
    # pre-redaction; tests that assert on them pass the redactor explicitly.
    with capture_logs(processors=[active_redactor()]) as logs:
        log.info("evt", k="capture-secret-1")
    assert logs[0]["k"] == MASK
    # and the chain is restored afterwards
    assert any(isinstance(p, Redactor) for p in structlog.get_config()["processors"])


# ---- reports -------------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_module_level_redact_is_the_reusable_report_entry_point() -> None:
    configure_logging({"DATABASE_URL": "postgresql://u:report-secret-1@h/d"})
    assert redact("run report: report-secret-1") == f"run report: {MASK}"


# ---- no credential in any non-raw table ---------------------------------------


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    upgrade_to_head(eng.url.render_as_string(hide_password=True))
    yield eng
    eng.dispose()


def _contribution(raw_path: str) -> LeadContribution:
    prov = FieldProvenance(
        canonical_path="person.full_name",
        source_name="acme",
        data_mode=DataMode.LIVE,
        fetched_at=T0,
        raw_field_path=raw_path,
        confidence_origin=ConfidenceOrigin.NONE,
        untrusted=False,
    )
    return LeadContribution(
        source_name="acme",
        values={"person.full_name": "Ada Lovelace"},
        provenance=(prov,),
    )


def _write(session: Session, raw_path: str, payload: Any) -> None:
    run = m.IngestionRun(started_at=T0, status="running")
    session.add(run)
    session.flush()
    sr = m.SourceRun(run_id=run.id, source_name="acme", resolved_mode="live")
    session.add(sr)
    session.flush()
    raw = RawResponseRepository.add(
        session,
        source_run_id=sr.id,
        endpoint_key="e",
        request_fingerprint="f",
        payload=payload,
        fetched_at=T0,
        mode=DataMode.LIVE,
        policy=RetentionPolicy(),
    )
    write_contribution(
        session,
        _contribution(raw_path),
        source_run_id=sr.id,
        raw_response_id=raw,
        data_mode=DataMode.LIVE,
        fetched_at=T0,
        lead_scope="person",
    )
    session.commit()


def _rows_containing(engine: Engine, needle: str) -> list[str]:
    """Tables other than the raw-response table with a cell holding ``needle``."""
    hits: list[str] = []
    with engine.connect() as conn:
        for table in m.Base.metadata.sorted_tables:
            if table.name == m.RawResponse.__tablename__:
                continue
            for row in conn.execute(table.select()):
                if needle in json.dumps(list(row), default=str):
                    hits.append(table.name)
    return hits


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_scan_helper_finds_a_planted_secret_in_a_non_raw_table(engine: Engine) -> None:
    with Session(engine) as s:
        _write(s, f"raw.{SECRET}", {})
    hits = _rows_containing(engine, SECRET)
    assert hits
    assert "raw_response" not in hits


# Verifies: specs/lead-source-adapters/requirements.md#10.5
# Verifies: specs/lead-source-adapters/requirements.md#21.3
def test_no_log_line_and_no_non_raw_row_holds_a_credential(
    engine: Engine, capsys: Any
) -> None:
    configure_logging({"DATABASE_URL": f"postgresql://u:{SECRET}@h/d"})
    log = structlog.get_logger("pipeline")
    with Session(engine) as s:
        # The provider echoes the key in the raw payload: allowed in raw_response only.
        _write(s, "raw.person.name", {"echo": {"api_key": SECRET}, "name": "Ada"})
        log.info(
            "contribution_written",
            source="acme",
            note=f"used {SECRET}",
            payload={"echo": SECRET},
            raw_field_path="raw.person.name",
        )
    out = capsys.readouterr().err
    assert out.strip()
    assert SECRET not in out
    assert _rows_containing(engine, SECRET) == []
    # the raw payload table is the one place the echo lives
    with Session(engine) as s:
        raw_id = s.execute(m.RawResponse.__table__.select()).first()
        assert raw_id is not None
        payload = RawResponseRepository.get_payload(s, raw_id.id)
        assert payload is not None
        assert payload["echo"]["api_key"] == SECRET


_ = (re, uuid)


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_very_long_secret_such_as_a_jwt_is_scrubbed() -> None:
    long = "".join(chr(97 + (i * 7) % 26) for i in range(6000))
    assert Redactor([long]).redact(f"x{long}y") == f"x{MASK}y"


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_secret_in_a_chained_exception_cause_is_scrubbed() -> None:
    r = Redactor([SECRET])
    try:
        try:
            raise KeyError(SECRET)
        except KeyError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        out = r(None, "error", _event(exc_info=outer))
    assert SECRET not in repr(out)
    assert "RuntimeError" in out["exception"]
