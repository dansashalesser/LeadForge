"""A whole search with no keys: plan first, then ingest to first tick (1.4, 14.3)."""

# ruff: noqa: F811 - fixtures imported from the ingestion tests

import json
import os
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.catalog import load_catalog
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.socket_guard import guard_for_mode
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)
from leadforge.outreach.acceptance import SeededAcceptance
from leadforge.outreach.clock import FakeClock
from leadforge.outreach.config import load_outreach_config
from leadforge.outreach.decisions import Decision, Reason, record_decisions
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.errors import MessageGenerationError
from leadforge.outreach.report import build_report, render_json, render_markdown
from leadforge.outreach.search_plan import SearchPlan, parse_request
from leadforge.outreach.searches import start_search
from leadforge.outreach.service import SearchService
from leadforge.outreach.tables import (
    OutreachDecision,
    OutreachMessage,
    OutreachSearch,
    OutreachTriggerEvent,
)
from leadforge.outreach.tests.support import NOW, engine, make_plan  # noqa: F401
from leadforge.outreach.tests.test_triggers import _seed_lead
from leadforge.outreach.tick import tick

CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"


def _service(backend: Backend, tmp_path: Path, lines: list[str]) -> SearchService:
    config = load_outreach_config(CONFIG_DIR / "outreach.yaml")
    return SearchService(
        config=config,
        catalog=load_catalog(),
        environ={},
        clock=FakeClock(NOW),
        acceptance=SeededAcceptance(config.simulation),
        dispatcher=DryRunDispatcher(tmp_path / "outbox.jsonl", lines.append),
        engine=backend.engine,
    )


def _count(backend: Backend, model: type) -> int:
    with Session(backend.engine) as session:
        return session.scalar(sa.select(sa.func.count()).select_from(model)) or 0


@pytest.mark.parametrize(
    ("mode", "query"),
    [("users", "mongodb"), ("free_text", "teams running mongodb")],
)
# Verifies: outreach requirements 1.4
# Verifies: outreach requirements 14.3
async def test_a_zero_key_search_runs_end_to_end_and_says_what_was_synthetic(
    composed: Backend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    query: str,
) -> None:
    lines: list[str] = []
    service = _service(composed, tmp_path, lines)
    # A Postgres server is a socket by nature; the no-socket proof runs on SQLite.
    guard = guard_for_mode(DataMode.SYNTHETIC)
    if composed.name == "sqlite":
        guard.install(monkeypatch)
    shown: list[SearchPlan] = []

    plan = service.plan(
        parse_request(mode, query, products=[query] if mode == "users" else [])
    )
    assert _count(composed, OutreachSearch) == 0  # planning stores and spends nothing
    summary = await service.run(plan, show=shown.append)

    guard.assert_clean()
    assert shown == [plan]
    assert summary.gathered > 0
    assert (
        sum(summary.counts.values())
        == summary.gathered
        == _count(composed, OutreachDecision)
    )
    assert (
        summary.invited
        == _count(composed, OutreachTriggerEvent)
        == summary.counts["selected"]
    )
    assert _count(composed, OutreachMessage) == 2 * summary.counts["selected"]
    notes = "\n".join(summary.notes)
    assert "compiler: offline" in notes
    assert "source" in notes
    assert "live" not in notes
    assert "messages: offline templates" in notes
    assert "judge: off" in notes
    with Session(composed.engine) as session:
        row = session.scalars(sa.select(OutreachSearch)).one()
        assert (row.status, row.ingestion_run_id) == ("done", summary.run_id)


async def test_a_failed_model_call_sends_one_lead_to_manual_review_not_the_search(
    composed: Backend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(composed, tmp_path, [])
    write = SearchService._write
    calls: list[uuid.UUID] = []

    def flaky(self: SearchService, stored: Any, *args: Any) -> Any:
        calls.append(stored.lead_id)
        if len(calls) == 1:
            raise MessageGenerationError("the model call failed (APITimeoutError)")
        return write(self, stored, *args)

    monkeypatch.setattr(SearchService, "_write", flaky)
    plan = service.plan(parse_request("free_text", "teams running mongodb"))

    summary = await service.run(plan, show=lambda _: None)

    assert calls  # at least one Lead was selected and written
    assert summary.counts["manual_review"] >= 1
    assert "1 lead(s) to manual review after a model error" in "\n".join(
        summary.notes
    )
    with Session(composed.engine) as session:
        assert session.scalars(sa.select(OutreachSearch.status)).one() == "done"
        failed = session.scalars(
            sa.select(OutreachDecision).filter_by(lead_id=calls[0])
        ).one()
        assert failed.status == "manual_review"
        assert "message_model_failed" in json.dumps(failed.reasons)
    assert _count(composed, OutreachMessage) == 2 * summary.counts["selected"]


class _Accepts:
    """Accepts the invites of the given Leads, a day after they were sent."""

    def __init__(self, lead_ids: set[uuid.UUID]) -> None:
        self._ids = lead_ids

    def accepted(self, lead_id: uuid.UUID, invited_at: datetime) -> datetime | None:
        return invited_at + timedelta(days=1) if lead_id in self._ids else None


def _seeded(engine: sa.Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """Two searches over one store. A: a Lead who accepts (invite, then email), one
    who never answers and has no Verified Email (stalled), one rejected. B: one Lead."""
    config = load_outreach_config(CONFIG_DIR / "outreach.yaml")
    with Session(engine) as session, session.begin():
        a = start_search(session, make_plan(), now=NOW)
        b = start_search(session, make_plan(), now=NOW)
        accepting = _seed_lead(session, a)
        _seed_lead(session, a, email_status="unverified")
        _seed_lead(session, b)
        rejected = uuid.uuid4()
        session.add(m.LeadIdentity(id=rejected, created_at=NOW))
        session.flush()
        decision = Decision(
            lead_id=rejected,
            status="rejected",
            score=Decimal(0),
            reasons=(Reason(code="open_deal"),),
        )
        record_decisions(session, a, (decision,), now=NOW)
    wait = config.triggers.accept_delay_days + 1
    for days in (0, wait, config.triggers.invite_timeout_days):
        with Session(engine) as session, session.begin():
            for search in (a, b):
                tick(
                    session,
                    search,
                    NOW + timedelta(days=days),
                    cfg=config.triggers,
                    acceptance=_Accepts({accepting}),
                    dispatcher=DryRunDispatcher(Path(os.devnull), lambda _: None),
                )
    return a, b


def _scalar(engine: sa.Engine, sql: str, **bind: object) -> int:
    with engine.connect() as conn:
        return int(conn.execute(sa.text(sql), bind).scalar_one())


# Verifies: outreach requirements 11.1
def test_every_funnel_count_matches_a_direct_query(engine: sa.Engine) -> None:
    a, _ = _seeded(engine)
    with Session(engine) as session:
        funnel = build_report(session, a).funnel
    sid = a.hex
    where = "from outreach_decision where search_id = :s"
    by_event = (
        "select count(distinct decision_id) from outreach_trigger_event "
        "where kind in ({kinds}) and decision_id in "
        "(select id from outreach_decision where search_id = :s)"
    )

    assert funnel.gathered == _scalar(engine, f"select count(*) {where}", s=sid) == 3
    for name in ("selected", "rejected", "needs_enrichment", "manual_review"):
        assert getattr(funnel, name) == _scalar(
            engine, f"select count(*) {where} and status = '{name}'", s=sid
        )
    assert funnel.invited == _scalar(engine, by_event.format(kinds="'invite'"), s=sid)
    assert funnel.emailed == _scalar(
        engine, by_event.format(kinds="'email','fallback_email'"), s=sid
    )
    assert funnel.stalled == _scalar(engine, by_event.format(kinds="'stalled'"), s=sid)
    assert (funnel.selected, funnel.invited, funnel.emailed) == (2, 2, 1)
    assert (funnel.stalled, funnel.rejected) == (1, 1)


# Verifies: outreach requirements 11.2
# Verifies: outreach requirements 11.3
def test_a_row_shows_reasons_and_texts_and_markdown_and_json_agree(
    engine: sa.Engine,
) -> None:
    a, _ = _seeded(engine)
    with Session(engine) as session:
        report = build_report(session, a)

    emailed = next(r for r in report.leads if "email" in r.sequence)
    assert emailed.sequence == "invite > accepted > email"
    assert emailed.reasons
    assert emailed.invite == "invite body"
    assert (emailed.email_subject, emailed.email_body) == ("Hi", "email body")
    as_json = json.loads(render_json(report))
    markdown = render_markdown(report)
    assert as_json["funnel"] == report.funnel.model_dump()
    for name, count in as_json["funnel"].items():
        assert f"| {name} | {count} |" in markdown
    assert "> invite body" in markdown
    assert "> email body" in markdown
    assert "open_deal" in markdown


# Verifies: outreach requirements 11.4
def test_a_default_report_masks_emails_and_urls_and_reveal_shows_them(
    engine: sa.Engine,
) -> None:
    a, _ = _seeded(engine)
    with Session(engine) as session:
        masked = build_report(session, a)
        shown = build_report(session, a, reveal=True)

    text = render_json(masked) + render_markdown(masked)
    assert "pat@acme.example" not in text
    assert not re.search(r"[\w.+-]+@", text.replace("***@", ""))
    assert "linkedin.com/in/pat" not in text
    assert "Pat Doe" not in text
    revealed = render_json(shown) + render_markdown(shown)
    assert "pat@acme.example" in revealed
    assert "linkedin.com/in/pat" in revealed
    assert "Pat Doe" in revealed


# Verifies: outreach requirements 5.2
def test_a_report_for_one_search_never_lists_a_lead_found_only_by_another(
    engine: sa.Engine,
) -> None:
    a, b = _seeded(engine)
    with Session(engine) as session:
        in_a = {r.lead_id for r in build_report(session, a).leads}
        in_b = {r.lead_id for r in build_report(session, b).leads}

    assert len(in_a) == 3
    assert len(in_b) == 1
    assert not in_a & in_b


# Verifies: outreach requirements 14.3
async def test_the_report_of_a_zero_key_run_says_what_was_synthetic(
    composed: Backend, tmp_path: Path
) -> None:
    service = _service(composed, tmp_path, [])
    plan = service.plan(parse_request("users", "mongodb", products=["mongodb"]))
    summary = await service.run(plan, show=lambda _: None)
    with Session(composed.engine) as session:
        notes = "\n".join(build_report(session, summary.search_id).notes)

    assert "compiler: offline" in notes
    assert "source" in notes
    assert "synthetic" in notes
    assert "live" not in notes
    assert "judge: off" in notes
