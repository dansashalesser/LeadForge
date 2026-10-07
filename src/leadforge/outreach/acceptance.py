"""Simulated invite acceptance from a seeded key (requirement 8.7).

``accepted(lead_id, invited_at)`` answers when the Lead accepted, or ``None`` for not
(yet). The seeded source hashes the seed with the Lead id, so the same seed always gives
the same acceptances, in any order and in any process.
"""

import hashlib
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Session

from leadforge.lead_ingestion.store.lead_reader import load_lead
from leadforge.outreach.config import SimulationConfig

__all__ = ["AcceptanceSource", "AnswerKeyAcceptance", "SeededAcceptance"]

_SCALE = 2**64


class AcceptanceSource(Protocol):
    def accepted(self, lead_id: uuid.UUID, invited_at: datetime) -> datetime | None: ...


class SeededAcceptance:
    def __init__(self, cfg: SimulationConfig) -> None:
        self._cfg = cfg

    def accepted(self, lead_id: uuid.UUID, invited_at: datetime) -> datetime | None:
        if self._unit(lead_id, "accept") >= float(self._cfg.accept_rate):
            return None
        days = int(self._unit(lead_id, "delay") * (self._cfg.max_accept_days + 1))
        return invited_at + timedelta(days=min(days, self._cfg.max_accept_days))

    def _unit(self, lead_id: uuid.UUID, purpose: str) -> float:
        digest = hashlib.sha256(
            f"{self._cfg.seed}:{lead_id}:{purpose}".encode()
        ).digest()
        return int.from_bytes(digest[:8], "big") / _SCALE


class AnswerKeyAcceptance:
    """Acceptance read from the demo answer key, falling back to another source.

    Each person in the key says whether their invite is accepted and after how many
    days (``outreach.accepts_after_days``; null means ignored). A Lead is recognised by
    the email and LinkedIn profile the key states for the person. A Lead the key does
    not know is answered by ``fallback``, or never accepts.
    """

    def __init__(
        self,
        key: Mapping[str, Any],
        session_factory: Callable[[], Session],
        fallback: AcceptanceSource | None = None,
    ) -> None:
        self._days: dict[str, int | None] = {}
        for person in key["people"]:
            expect, outreach = person["expect"], person.get("outreach", {})
            days = outreach.get("accepts_after_days")
            for ident in _identifiers(expect.get("email"), expect.get("linkedin_url")):
                self._days[ident] = days
        self._session = session_factory
        self._fallback = fallback

    def accepted(self, lead_id: uuid.UUID, invited_at: datetime) -> datetime | None:
        with self._session() as session:
            stored = load_lead(session, lead_id)
        if stored is not None:
            lead = stored.lead
            for ident in _identifiers(lead.email, lead.linkedin_url):
                if ident in self._days:
                    days = self._days[ident]
                    return None if days is None else invited_at + timedelta(days=days)
        if self._fallback is not None:
            return self._fallback.accepted(lead_id, invited_at)
        return None


def _identifiers(email: object, linkedin: object) -> list[str]:
    out: list[str] = []
    if linkedin:
        slug = str(linkedin).split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()
        out.append(f"linkedin:{slug}")
    if email:
        out.append(f"email:{str(email).lower()}")
    return out
