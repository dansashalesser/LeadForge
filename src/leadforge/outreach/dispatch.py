"""Dispatch as a dry run: record, print, append (requirements 9.1, 9.3, 9.4).

Firing a Message writes one trigger event row (the database), one console line and one
JSONL line, all from the same values. Nothing leaves the machine: the Message row is
already ``dry_run`` and this module imports no transport.
"""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from leadforge.outreach.tables import OutreachMessage, OutreachTriggerEvent

__all__ = ["DryRunDispatcher"]

FiredKind = Literal["invite", "email", "fallback_email"]


class DryRunDispatcher:
    def __init__(self, outbox: Path, echo: Callable[[str], None] = print) -> None:
        self._outbox = outbox
        self._echo = echo

    def dispatch(
        self, session: Session, message: OutreachMessage, kind: FiredKind, at: datetime
    ) -> None:
        """Record that ``message`` fired as ``kind`` at ``at``; the caller commits."""
        record = {
            "at": at.isoformat(),
            "decision_id": str(message.decision_id),
            "message_id": str(message.id),
            "kind": kind,
            "channel": message.channel,
            "subject": message.subject,
            "body": message.body,
            "state": message.state,
        }
        session.add(
            OutreachTriggerEvent(
                decision_id=message.decision_id,
                kind=kind,
                at=at,
                detail={"message_id": str(message.id), "channel": message.channel},
            )
        )
        session.flush()
        self._outbox.parent.mkdir(parents=True, exist_ok=True)
        with self._outbox.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._echo(
            f"[dry-run] {at.isoformat()} {kind} via {message.channel} "
            f"decision={message.decision_id} ({len(message.body)} chars, not sent)"
        )
