"""Sequence state as a pure fold over events (requirement 8, design D1).

``due`` takes a Lead's events, the time, the trigger config and the facts that can
change the plan, and returns what to do now. Nothing is stored as "current status":
the same events and time always give the same actions.

Order of rules, ``halted`` first on every call:

1. a halt event exists: nothing more, ever.
2. the Lead is opted out or suppressed: ``halted``.
3. no invite yet: ``invite`` (LinkedIn is always first).
4. invite accepted: ``email`` once the acceptance time plus the delay has passed.
5. invite not accepted and the timeout has passed: ``fallback_email`` when the Lead
   has a Verified Email that is not a role address, else ``stalled``.
An email, fallback or stall already recorded is final.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from leadforge.outreach.config import TriggerConfig

__all__ = ["Action", "ContactFacts", "Event", "EventKind", "due"]

EventKind = Literal[
    "invite", "accepted", "email", "fallback_email", "stalled", "halted"
]
_FINAL: frozenset[EventKind] = frozenset(
    {"email", "fallback_email", "stalled", "halted"}
)


@dataclass(frozen=True)
class Event:
    kind: EventKind
    at: datetime


@dataclass(frozen=True)
class Action:
    kind: Literal["invite", "email", "fallback_email", "stalled", "halted"]


@dataclass(frozen=True)
class ContactFacts:
    opted_out: bool
    suppressed: bool
    # A Verified Email that is not a role address.
    usable_email: bool


def due(
    events: Sequence[Event], now: datetime, cfg: TriggerConfig, facts: ContactFacts
) -> tuple[Action, ...]:
    first: dict[EventKind, datetime] = {}
    for event in sorted(events, key=lambda e: e.at):
        first.setdefault(event.kind, event.at)
    if "halted" in first:
        return ()
    if facts.opted_out or facts.suppressed:
        return (Action("halted"),)
    if _FINAL & first.keys():
        return ()
    invited = first.get("invite")
    if invited is None:
        return (Action("invite"),)
    accepted = first.get("accepted")
    if accepted is not None:
        return (Action("email"),) if now >= accepted + cfg.accept_delay else ()
    if now >= invited + cfg.invite_timeout:
        return (Action("fallback_email" if facts.usable_email else "stalled"),)
    return ()
