"""Simulated invite acceptance from a seeded key (requirement 8.7).

``accepted(lead_id, invited_at)`` answers when the Lead accepted, or ``None`` for not
(yet). The seeded source hashes the seed with the Lead id, so the same seed always gives
the same acceptances, in any order and in any process.
"""

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Protocol

from leadforge.outreach.config import SimulationConfig

__all__ = ["AcceptanceSource", "SeededAcceptance"]

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
