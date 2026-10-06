"""Scripted sources and store probes shared by the run-level store tests.

A scripted source answers with the people it was given (one contribution each, keyed
by the canonical paths it names), may block on a ``threading.Event`` while "calling
the provider" so a second run can be started meanwhile, and records every call it
makes, so a test can prove a refused run spent nothing.
"""

import asyncio
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    LeadContribution,
    RateBucket,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.models import ConfidenceOrigin, DataMode, FieldProvenance
from leadforge.lead_ingestion.registry import SourceRegistry, SourceSettings
from leadforge.lead_ingestion.store import models as m

FETCHED = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


@dataclass
class Script:
    """What a scripted source answers, and what it did."""

    people: Sequence[Mapping[str, Any]]
    gate: threading.Event | None = None  # the call waits for it, when given
    entered: threading.Event = field(default_factory=threading.Event)
    calls: list[str] = field(default_factory=list)


def contribution_of(source: str, values: Mapping[str, Any]) -> LeadContribution:
    return LeadContribution(
        source_name=source,
        values=dict(values),
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name=source,
                data_mode=DataMode.SYNTHETIC,
                fetched_at=FETCHED,
                raw_field_path=f"people[].{path}",
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            )
            for path in values
        ),
    )


def scripted_source(source_name: str, script: Script) -> type[BaseLeadSource]:
    class Scripted(BaseLeadSource):
        name: ClassVar[str] = source_name
        capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
        rate_limit: ClassVar[Mapping[str, RateBucket]] = {}
        answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {}
        cost_class: ClassVar[CostClass] = CostClass.FREE
        charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
        yields_suppression: ClassVar[bool] = False
        target_vocabulary: ClassVar[Mapping[str, object]] = {}
        endpoints: ClassVar[Mapping[str, Endpoint]] = {}
        required_env: ClassVar[tuple[str, ...]] = ()

        async def fetch_raw(self, request: SourceRequest) -> RawBatch:
            script.calls.append(request.kind)
            script.entered.set()
            if script.gate is not None:
                await asyncio.to_thread(script.gate.wait, 30)
            return RawBatch(
                source_name=self.name,
                payload={"people": [dict(p) for p in script.people]},
            )

        def normalize(self, raw: RawBatch) -> list[LeadContribution]:
            return [contribution_of(self.name, p) for p in raw.payload["people"]]

    return Scripted


def registry_of(*sources: type[BaseLeadSource]) -> SourceRegistry:
    return SourceRegistry(
        list(sources),
        {s.name: SourceSettings(mode=DataMode.SYNTHETIC) for s in sources},
    )


def active_leads(engine: Engine) -> list[m.CanonicalLeadRow]:
    with Session(engine) as session:
        return list(
            session.scalars(
                sa.select(m.CanonicalLeadRow)
                .join(
                    m.LeadIdentity,
                    m.LeadIdentity.id == m.CanonicalLeadRow.lead_identity_id,
                )
                .where(m.LeadIdentity.retired_at.is_(None))
            )
        )


def count(engine: Engine, model: Any) -> int:
    with Session(engine) as session:
        return session.scalar(sa.select(sa.func.count()).select_from(model)) or 0


def person(email: str, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "person.email": email,
        "person.email_status": "verified",
        "person.full_name": name,
        **{k.replace("__", "."): v for k, v in extra.items()},
    }
