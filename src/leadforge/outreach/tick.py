"""Advance every selected Lead's sequence to ``now`` (requirement 8).

For each selected Decision of a search: read its events, record an acceptance the
``AcceptanceSource`` reports, ask ``due`` what is owed, and carry it out. An invite or
email fires through the dispatcher; a stall or halt is only recorded. Running it
again at the same time does nothing new: what is owed is derived from what is
recorded.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.models import EmailStatus
from leadforge.lead_ingestion.store.lead_reader import load_lead
from leadforge.outreach.acceptance import AcceptanceSource
from leadforge.outreach.config import TriggerConfig
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.tables import (
    OutreachDecision,
    OutreachMessage,
    OutreachTriggerEvent,
)
from leadforge.outreach.triggers import ContactFacts, Event, EventKind, due

__all__ = ["Fired", "tick"]


@dataclass(frozen=True)
class Fired:
    decision_id: uuid.UUID
    kind: str


def tick(
    session: Session,
    search_id: uuid.UUID,
    now: datetime,
    *,
    cfg: TriggerConfig,
    acceptance: AcceptanceSource,
    dispatcher: DryRunDispatcher,
) -> tuple[Fired, ...]:
    """Everything that became due for the search at ``now``; the caller commits."""
    fired: list[Fired] = []
    decisions = session.scalars(
        select(OutreachDecision)
        .where(
            OutreachDecision.search_id == search_id,
            OutreachDecision.status == "selected",
        )
        .order_by(OutreachDecision.lead_id)
    )
    for decision in decisions:
        events = _events(session, decision.id)
        invited = next((e for e in events if e.kind == "invite"), None)
        if invited is not None and not any(
            e.kind in ("accepted", "halted") for e in events
        ):
            at = acceptance.accepted(decision.lead_id, invited.at)
            if at is not None and at <= now:
                _record(session, decision.id, "accepted", at)
                events.append(Event("accepted", at))
        for action in due(events, now, cfg, _facts(session, decision.lead_id)):
            if action.kind in ("invite", "email", "fallback_email"):
                message = _message(session, decision.id, action.kind)
                dispatcher.dispatch(session, message, action.kind, now)
            else:
                _record(session, decision.id, action.kind, now)
            fired.append(Fired(decision.id, action.kind))
    return tuple(fired)


def _events(session: Session, decision_id: uuid.UUID) -> list[Event]:
    rows = session.scalars(
        select(OutreachTriggerEvent)
        .where(OutreachTriggerEvent.decision_id == decision_id)
        .order_by(OutreachTriggerEvent.at)
    )
    return [Event(cast(EventKind, r.kind), _aware(r.at)) for r in rows]


def _record(session: Session, decision_id: uuid.UUID, kind: str, at: datetime) -> None:
    session.add(
        OutreachTriggerEvent(decision_id=decision_id, kind=kind, at=at, detail={})
    )
    session.flush()


def _message(session: Session, decision_id: uuid.UUID, kind: str) -> OutreachMessage:
    variant = "invite" if kind == "invite" else "email"
    return session.scalars(
        select(OutreachMessage).where(
            OutreachMessage.decision_id == decision_id,
            OutreachMessage.variant == variant,
        )
    ).one()


def _facts(session: Session, lead_id: uuid.UUID) -> ContactFacts:
    stored = load_lead(session, lead_id)
    if stored is None or stored.retired:
        return ContactFacts(opted_out=False, suppressed=True, usable_email=False)
    lead = stored.lead
    return ContactFacts(
        opted_out=lead.opt_out,
        suppressed=lead.suppressed,
        usable_email=lead.email is not None
        and not lead.email_is_role_address
        and lead.email_status is EmailStatus.VERIFIED,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
