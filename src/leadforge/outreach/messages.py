"""Store the Messages written for a Decision (requirements 7.1, 7.7, 9.4, 10.4).

A Message row keeps its text, the generator, the model (or ``offline``), the prompt
version, the checks it passed and, when a judge ran, its rubric scores. Its state is
``dry_run`` and can be nothing else. A Draft whose checks did not all pass is refused:
``manual_review`` Leads have no Message at all.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadforge.outreach.errors import MessageValidationError
from leadforge.outreach.message_checks import Check, Draft, all_passed
from leadforge.outreach.tables import OutreachMessage

__all__ = ["record_messages", "stored_messages"]

_CHANNEL = {"invite": "linkedin", "email": "email"}


def record_messages(
    session: Session,
    decision_id: uuid.UUID,
    written: tuple[tuple[Draft, tuple[Check, ...], dict[str, int] | None], ...],
    *,
    now: datetime,
) -> tuple[OutreachMessage, ...]:
    """One row per ``(draft, checks, judge scores)``; the caller commits."""
    for draft, checks, _ in written:
        if not all_passed(checks):
            raise MessageValidationError(
                f"a {draft.kind} that failed its checks cannot be stored"
            )
    rows = tuple(
        OutreachMessage(
            decision_id=decision_id,
            channel=_CHANNEL[draft.kind],
            variant=draft.kind,
            subject=draft.subject,
            body=draft.body,
            generator=draft.generator,
            model=draft.model,
            prompt_version=draft.prompt_version,
            checks={"results": [c.model_dump(mode="json") for c in checks]},
            judge=judge,
            state="dry_run",
            created_at=now,
        )
        for draft, checks, judge in written
    )
    session.add_all(rows)
    session.flush()
    return rows


def stored_messages(
    session: Session, decision_id: uuid.UUID
) -> tuple[OutreachMessage, ...]:
    """A Decision's Messages in the order they were written (invite first)."""
    return tuple(
        session.scalars(
            select(OutreachMessage)
            .where(OutreachMessage.decision_id == decision_id)
            .order_by(OutreachMessage.created_at, OutreachMessage.channel.desc())
        )
    )
