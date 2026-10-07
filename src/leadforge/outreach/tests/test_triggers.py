"""Trigger logic: the fold, clock, seeded acceptance, dry-run dispatch (8.x, 9.x)."""

# ruff: noqa: F811 - fixtures imported from support

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.tests.socket_guard import SocketGuard, guard_for_mode
from leadforge.outreach.acceptance import SeededAcceptance
from leadforge.outreach.clock import FakeClock
from leadforge.outreach.config import load_outreach_config
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.searches import start_search
from leadforge.outreach.tables import (
    OutreachDecision,
    OutreachMessage,
    OutreachTriggerEvent,
)
from leadforge.outreach.tests.support import (  # noqa: F401 - fixtures
    NOW,
    backend,
    blank,
    engine,
    make_plan,
    postgres_url,
)
from leadforge.outreach.tick import tick
from leadforge.outreach.triggers import ContactFacts, Event, due

CONFIG = load_outreach_config(
    Path(__file__).resolve().parents[4] / "config" / "outreach.yaml"
)
TRIGGERS = CONFIG.triggers
DELAY, TIMEOUT = TRIGGERS.accept_delay, TRIGGERS.invite_timeout
OK = ContactFacts(opted_out=False, suppressed=False, usable_email=True)
NO_EMAIL = ContactFacts(opted_out=False, suppressed=False, usable_email=False)


def _kinds(events: list[Event], now: datetime, facts: ContactFacts = OK) -> list[str]:
    return [a.kind for a in due(events, now, TRIGGERS, facts)]


# ------------------------------------------------------------------ 6.1 the fold


# Verifies: outreach requirements 8.1
def test_the_invite_comes_first_and_only_once() -> None:
    assert _kinds([], NOW) == ["invite"]
    assert _kinds([Event("invite", NOW)], NOW) == []


# Verifies: outreach requirements 8.2
def test_the_email_follows_acceptance_only_after_the_delay() -> None:
    accepted = NOW + timedelta(days=1)
    events = [Event("invite", NOW), Event("accepted", accepted)]

    assert _kinds(events, accepted) == []
    assert _kinds(events, accepted + DELAY - timedelta(seconds=1)) == []
    assert _kinds(events, accepted + DELAY) == ["email"]


# Verifies: outreach requirements 8.3
def test_an_unanswered_invite_falls_back_to_email_for_a_verified_email() -> None:
    events = [Event("invite", NOW)]

    assert _kinds(events, NOW + TIMEOUT - timedelta(seconds=1)) == []
    assert _kinds(events, NOW + TIMEOUT) == ["fallback_email"]


# Verifies: outreach requirements 8.4
def test_an_unanswered_invite_with_no_usable_email_stalls() -> None:
    assert _kinds([Event("invite", NOW)], NOW + TIMEOUT, NO_EMAIL) == ["stalled"]


# Verifies: outreach requirements 8.5
@pytest.mark.parametrize(
    "facts",
    [
        ContactFacts(opted_out=True, suppressed=False, usable_email=True),
        ContactFacts(opted_out=False, suppressed=True, usable_email=True),
    ],
)
def test_an_opt_out_or_suppression_halts_at_any_point(facts: ContactFacts) -> None:
    assert _kinds([], NOW, facts) == ["halted"]
    assert _kinds([Event("invite", NOW)], NOW + TIMEOUT, facts) == ["halted"]
    accepted = [Event("invite", NOW), Event("accepted", NOW)]
    assert _kinds(accepted, NOW + DELAY, facts) == ["halted"]


# Verifies: outreach requirements 8.5
def test_after_a_halt_nothing_is_ever_due_even_if_the_lead_is_clear_again() -> None:
    events = [Event("invite", NOW), Event("halted", NOW + timedelta(days=1))]

    assert _kinds(events, NOW + timedelta(days=30)) == []


# Verifies: outreach requirements 8.3
@pytest.mark.parametrize("final", ["email", "fallback_email", "stalled"])
def test_an_email_fallback_or_stall_already_recorded_is_final(final: str) -> None:
    events = [Event("invite", NOW), Event(final, NOW + TIMEOUT)]  # type: ignore[arg-type]

    assert _kinds(events, NOW + timedelta(days=90)) == []


# Verifies: outreach requirements 8.6
def test_a_fake_clock_advances_days_without_waiting() -> None:
    clock = FakeClock(NOW)

    assert clock.advance(days=5) == NOW + timedelta(days=5)
    assert clock.now() == NOW + timedelta(days=5)
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(days=-1)
    with pytest.raises(ValueError, match="aware"):
        FakeClock(datetime(2026, 1, 1))


# ------------------------------------------------------------------ 6.2 seeded


# Verifies: outreach requirements 8.7
def test_the_same_seed_gives_the_same_acceptances_and_another_seed_differs() -> None:
    leads = [uuid.UUID(int=i) for i in range(200)]
    first = SeededAcceptance(CONFIG.simulation)
    again = SeededAcceptance(CONFIG.simulation)
    other = SeededAcceptance(CONFIG.simulation.model_copy(update={"seed": 8}))

    a = [first.accepted(x, NOW) for x in leads]
    assert a == [again.accepted(x, NOW) for x in reversed(leads)][::-1]
    assert a != [other.accepted(x, NOW) for x in leads]


# Verifies: outreach requirements 8.7
def test_acceptance_follows_the_configured_rate_and_window() -> None:
    source = SeededAcceptance(CONFIG.simulation)
    leads = [uuid.UUID(int=i) for i in range(1000)]
    answers = [source.accepted(x, NOW) for x in leads]
    accepted = [x for x in answers if x is not None]

    assert abs(len(accepted) / 1000 - float(CONFIG.simulation.accept_rate)) < 0.08
    assert all(
        NOW <= x <= NOW + timedelta(days=CONFIG.simulation.max_accept_days)
        for x in accepted
    )
    never = CONFIG.simulation.model_copy(update={"accept_rate": 0})
    assert all(SeededAcceptance(never).accepted(x, NOW) is None for x in leads[:50])


# ------------------------------------------------------------- 6.3 dispatch + tick


def _seed_lead(
    session: Session,
    search_id: uuid.UUID,
    *,
    opt_out: bool = False,
    email_status: str = "verified",
) -> uuid.UUID:
    """A stored lead with a selected decision and its invite and email messages."""
    lead_id = uuid.uuid4()
    session.add(m.LeadIdentity(id=lead_id, created_at=NOW))
    session.flush()
    session.add(
        m.CanonicalLeadRow(
            lead_identity_id=lead_id,
            email="pat@acme.example",
            email_status=email_status,
            linkedin_url="https://www.linkedin.com/in/pat",
            full_name="Pat Doe",
            employments=[],
            tech_signals=[],
            intent_signals=[],
            opt_out=opt_out,
            suppressed=False,
            contributing_sources=["one"],
            computed_at=NOW,
            projection_version=1,
            email_is_role_address=False,
            role_contact_emails=[],
        )
    )
    decision = OutreachDecision(
        search_id=search_id,
        lead_id=lead_id,
        status="selected",
        score_milli=800,
        reasons=[{"code": "icp_fit"}],
        decided_at=NOW,
    )
    session.add(decision)
    session.flush()
    for channel, variant, subject in (
        ("linkedin", "invite", None),
        ("email", "email", "Hi"),
    ):
        session.add(
            OutreachMessage(
                decision_id=decision.id,
                channel=channel,
                variant=variant,
                subject=subject,
                body=f"{variant} body",
                generator="offline",
                model="offline",
                prompt_version="v1",
                checks={},
                state="dry_run",
                created_at=NOW,
            )
        )
    session.flush()
    return lead_id


class _Always:
    def __init__(self, days: int | None) -> None:
        self.days = days

    def accepted(self, lead_id: uuid.UUID, invited_at: datetime) -> datetime | None:
        return None if self.days is None else invited_at + timedelta(days=self.days)


def _run(
    engine: sa.Engine,
    search_id: uuid.UUID,
    now: datetime,
    acceptance: object,
    outbox: Path,
    lines: list[str],
) -> list[str]:
    with Session(engine) as session, session.begin():
        fired = tick(
            session,
            search_id,
            now,
            cfg=TRIGGERS,
            acceptance=acceptance,  # type: ignore[arg-type]
            dispatcher=DryRunDispatcher(outbox, lines.append),
        )
    return [f.kind for f in fired]


def _setup(engine: sa.Engine, **lead: object) -> uuid.UUID:
    with Session(engine) as session, session.begin():
        search_id = start_search(session, make_plan(), now=NOW)
        _seed_lead(session, search_id, **lead)  # type: ignore[arg-type]
    return search_id


def _event_kinds(engine: sa.Engine) -> list[str]:
    with Session(engine) as session:
        return list(
            session.scalars(
                sa.select(OutreachTriggerEvent.kind).order_by(OutreachTriggerEvent.at)
            )
        )


# Verifies: outreach requirements 9.1
# Verifies: outreach requirements 8.2
def test_accept_path_fires_invite_then_email_each_in_db_console_and_jsonl(
    engine: sa.Engine, tmp_path: Path
) -> None:
    search = _setup(engine)
    outbox, lines = tmp_path / "out" / "dry.jsonl", list[str]()
    clock = FakeClock(NOW)

    assert _run(engine, search, clock.now(), _Always(1), outbox, lines) == ["invite"]
    clock.advance(days=1)
    assert _run(engine, search, clock.now(), _Always(1), outbox, lines) == []
    clock.advance(days=DELAY.days)
    assert _run(engine, search, clock.now(), _Always(1), outbox, lines) == ["email"]

    assert _event_kinds(engine) == ["invite", "accepted", "email"]
    records = [json.loads(x) for x in outbox.read_text().splitlines()]
    assert [r["kind"] for r in records] == ["invite", "email"]
    assert [r["state"] for r in records] == ["dry_run", "dry_run"]
    assert len(lines) == 2
    assert all("not sent" in line for line in lines)


# Verifies: outreach requirements 8.3
def test_no_answer_falls_back_to_the_email_after_the_timeout(
    engine: sa.Engine, tmp_path: Path
) -> None:
    search = _setup(engine)
    outbox, lines = tmp_path / "o.jsonl", list[str]()

    _run(engine, search, NOW, _Always(None), outbox, lines)
    assert (
        _run(
            engine,
            search,
            NOW + TIMEOUT - timedelta(days=1),
            _Always(None),
            outbox,
            lines,
        )
        == []
    )
    assert _run(engine, search, NOW + TIMEOUT, _Always(None), outbox, lines) == [
        "fallback_email"
    ]
    assert _event_kinds(engine) == ["invite", "fallback_email"]


# Verifies: outreach requirements 8.4
def test_no_answer_and_no_verified_email_stalls_with_no_email_message_fired(
    engine: sa.Engine, tmp_path: Path
) -> None:
    search = _setup(engine, email_status="unverified")
    outbox, lines = tmp_path / "o.jsonl", list[str]()

    _run(engine, search, NOW, _Always(None), outbox, lines)
    assert _run(engine, search, NOW + TIMEOUT, _Always(None), outbox, lines) == [
        "stalled"
    ]

    assert _event_kinds(engine) == ["invite", "stalled"]
    assert [json.loads(x)["kind"] for x in outbox.read_text().splitlines()] == [
        "invite"
    ]


# Verifies: outreach requirements 8.5
def test_a_lead_who_opts_out_mid_sequence_gains_no_message(
    engine: sa.Engine, tmp_path: Path
) -> None:
    search = _setup(engine)
    outbox, lines = tmp_path / "o.jsonl", list[str]()
    _run(engine, search, NOW, _Always(1), outbox, lines)
    with Session(engine) as session, session.begin():
        session.execute(sa.update(m.CanonicalLeadRow).values(opt_out=True))

    fired = _run(engine, search, NOW + timedelta(days=10), _Always(1), outbox, lines)

    assert fired == ["halted"]
    assert (
        _run(engine, search, NOW + timedelta(days=20), _Always(1), outbox, lines) == []
    )
    assert _event_kinds(engine)[-1] == "halted"
    assert len(outbox.read_text().splitlines()) == 1


# Verifies: outreach requirements 8.2
def test_ticking_again_at_the_same_time_changes_nothing(
    engine: sa.Engine, tmp_path: Path
) -> None:
    search = _setup(engine)
    outbox, lines = tmp_path / "o.jsonl", list[str]()

    assert _run(engine, search, NOW, _Always(None), outbox, lines) == ["invite"]
    assert _run(engine, search, NOW, _Always(None), outbox, lines) == []
    assert len(_event_kinds(engine)) == 1


# Verifies: outreach requirements 9.3
def test_a_whole_sequence_opens_no_socket(
    engine: sa.Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard: SocketGuard = guard_for_mode(DataMode.SYNTHETIC)
    if engine.dialect.name == "sqlite":  # a Postgres server is a socket by nature
        guard.install(monkeypatch)
    search = _setup(engine)
    outbox, lines = tmp_path / "o.jsonl", list[str]()

    _run(engine, search, NOW, SeededAcceptance(CONFIG.simulation), outbox, lines)
    _run(
        engine,
        search,
        NOW + TIMEOUT + DELAY,
        SeededAcceptance(CONFIG.simulation),
        outbox,
        lines,
    )

    guard.assert_clean()
    assert len(lines) >= 2


# Verifies: outreach requirements 9.3
def test_the_socket_guard_fails_a_run_that_opens_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)

    with pytest.raises(AssertionError):
        socket.create_connection(("example.invalid", 80))
    with pytest.raises(AssertionError):
        guard.assert_clean()
