"""Builders for the Lead records the outreach tests judge and write about."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from leadforge.lead_ingestion.models import (
    CanonicalLead,
    CompanySignal,
    Employment,
    IntentSignal,
    TechSignal,
)
from leadforge.lead_ingestion.store.lead_reader import StoredLead
from leadforge.outreach.search_plan import SearchPlan

NOW = datetime(2026, 10, 7, tzinfo=UTC)
LINKEDIN = "https://www.linkedin.com/in/someone"


def make_plan(**over: object) -> SearchPlan:
    fields: dict[str, object] = {
        "mode": "users",
        "query": "q",
        "terms": ("term_a",),
        "compiler": "offline",
        **over,
    }
    return SearchPlan.model_validate(fields)


def make_employment(
    *,
    tech: float | None = 0.9,
    intent: float | None = 0.8,
    title: str | None = "Head of Data",
) -> Employment:
    return Employment(
        company=CompanySignal(
            company_id="c1",
            name="Acme",
            domains=("acme.example",),
            tech_signals=(TechSignal(label="Term A", strength=tech),) if tech else (),
            intent_signals=(IntentSignal(label="hiring", strength=intent),)
            if intent
            else (),
        ),
        title=title,
        is_current=True,
    )


def make_lead(**over: object) -> CanonicalLead:
    fields: dict[str, object] = {
        "email": "pat@acme.example",
        "email_status": "verified",
        "linkedin_url": LINKEDIN,
        "full_name": "Pat Doe",
        "employments": (make_employment(),),
        **over,
    }
    return CanonicalLead.model_validate(fields)


def make_stored(
    lead: CanonicalLead | None = None,
    *,
    retired_at: datetime | None = None,
    successor_ids: tuple[uuid.UUID, ...] = (),
    agreement: tuple[tuple[str, int], ...] = (("person.email", 2), ("person.name", 2)),
    contributing_sources: tuple[str, ...] = ("one", "two"),
) -> StoredLead:
    return StoredLead(
        lead_id=uuid.uuid4(),
        lead=lead or make_lead(),
        provenance=(),
        agreement=agreement,
        contributing_sources=contributing_sources,
        primary_domain=None,
        primary_domain_source=None,
        projection_version=1,
        projection_fingerprint=None,
        computed_at=NOW,
        stale=False,
        retired_at=retired_at,
        successor_ids=successor_ids,
        web_evidence=(),
    )


class ScriptedModel:
    """A ``ModelInvoker`` answering from a script and recording what it was asked."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.asked: list[Sequence[tuple[str, str]]] = []

    def invoke(self, messages: Sequence[tuple[str, str]]) -> object:
        self.asked.append(messages)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer
