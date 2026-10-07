"""The facts a Message may state about a Lead (requirements 7.3, 7.5).

``build_facts`` reads the Lead record, its current Employment, its Signals and the web
evidence attached to its company, and nothing else. Each fact has an id a writer cites
and the Lead field it came from, so a check can map every claim in a Message back to a
field. Every value is provider or web text, so it is untrusted: a writer receives it
only inside an escaped block (``render_facts``).
"""

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from leadforge.lead_ingestion.models import Signal, UntrustedText
from leadforge.lead_ingestion.store.lead_reader import StoredLead
from leadforge.outreach.config import MessageConfig
from leadforge.outreach.prompts import delimit

__all__ = ["Fact", "FactKind", "LeadFacts", "build_facts", "render_facts"]

FactKind = Literal["name", "title", "company", "tech", "intent", "evidence"]


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Annotated[str, Field(min_length=1)]
    kind: FactKind
    value: Annotated[str, Field(min_length=1)]
    # The Lead field the value was read from, for example ``employments[0].title``.
    field: Annotated[str, Field(min_length=1)]


class LeadFacts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    facts: tuple[Fact, ...]

    def get(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def of_kind(self, kind: FactKind) -> tuple[Fact, ...]:
        return tuple(f for f in self.facts if f.kind == kind)


def build_facts(stored: StoredLead, cfg: MessageConfig) -> LeadFacts:
    lead = stored.lead
    facts: list[Fact] = []
    if lead.full_name:
        facts.append(
            Fact(id="name", kind="name", value=lead.full_name, field="full_name")
        )
    current = tuple(e for e in lead.employments if e.is_current is not False)
    if current:
        employment = current[0]
        where = f"employments[{lead.employments.index(employment)}]"
        if employment.title:
            facts.append(
                Fact(
                    id="title",
                    kind="title",
                    value=employment.title,
                    field=f"{where}.title",
                )
            )
        if employment.company.name:
            facts.append(
                Fact(
                    id="company",
                    kind="company",
                    value=employment.company.name,
                    field=f"{where}.company.name",
                )
            )
        facts += _signals(
            "tech",
            f"{where}.company.tech_signals",
            employment.company.tech_signals,
            cfg,
        )
        facts += _signals(
            "intent",
            f"{where}.company.intent_signals",
            employment.company.intent_signals,
            cfg,
        )
    facts += _signals("tech", "tech_signals", lead.tech_signals, cfg)
    facts += _signals("intent", "intent_signals", lead.intent_signals, cfg)
    facts += _evidence(stored, cfg)
    return LeadFacts(facts=_unique(facts))


def render_facts(facts: LeadFacts) -> str:
    """The facts as one escaped ``<lead_facts>`` block, one ``[id] value`` per line."""
    return delimit("lead_facts", "\n".join(f"[{f.id}] {f.value}" for f in facts.facts))


def _signals(
    kind: Literal["tech", "intent"],
    field: str,
    signals: Sequence[Signal],
    cfg: MessageConfig,
) -> list[Fact]:
    ranked = sorted(signals, key=lambda s: -s.strength)
    return [
        Fact(id=f"{kind}:{s.label}", kind=kind, value=s.label, field=f"{field}[{i}]")
        for i, s in enumerate(ranked[: cfg.max_hook_facts])
    ]


def _evidence(stored: StoredLead, cfg: MessageConfig) -> list[Fact]:
    facts: list[Fact] = []
    for i, record in enumerate(stored.web_evidence):
        for key in ("title", "snippet"):
            text = record.values.get(key)
            if isinstance(text, UntrustedText) and text.value.strip():
                facts.append(
                    Fact(
                        id=f"evidence:{i}:{key}",
                        kind="evidence",
                        value=text.value.strip(),
                        field=f"web_evidence[{i}].{key}",
                    )
                )
    return facts[: cfg.max_hook_facts]


def _unique(facts: list[Fact]) -> tuple[Fact, ...]:
    seen: dict[str, Fact] = {}
    for fact in facts:
        seen.setdefault(fact.id, fact)
    return tuple(seen.values())
