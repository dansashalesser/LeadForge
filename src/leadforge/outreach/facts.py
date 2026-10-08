"""The facts a Message may state about a Lead (requirements 7.3, 7.5).

``build_facts`` reads the Lead record, its current Employment, its Signals, the web
evidence attached to its company and the usage Verdict that selected it, and nothing
else. Each fact has an id a writer cites and the Lead field it came from, so a check can
map every claim in a Message back to a field. Every value is provider or web text, so it
is untrusted: a writer receives it only inside an escaped block (``render_facts``).
"""

import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from leadforge.lead_ingestion.models import Signal, UntrustedText
from leadforge.lead_ingestion.store.lead_reader import StoredLead
from leadforge.outreach.config import MessageConfig
from leadforge.outreach.prompts import delimit
from leadforge.outreach.usage.records import EvidenceClass, EvidenceRecord, Relationship
from leadforge.outreach.usage.stage import current_employer

__all__ = [
    "Fact",
    "FactKind",
    "LeadFacts",
    "UsageContext",
    "build_facts",
    "render_facts",
]

FactKind = Literal[
    "name", "title", "company", "tech", "intent", "evidence", "product", "usage"
]

# Relationships a Message may cite, the most present first; the rest (a passing mention,
# a vendor's own staff, nothing to do with the product) say nothing about use.
_CITABLE: tuple[Relationship, ...] = (
    Relationship.USES_NOW,
    Relationship.EVALUATING,
    Relationship.USED_PAST,
)
# A technographic record's quote is machine-made ("technology <uid>"), not words a
# person wrote, so there is nothing in it to quote back.
_UNQUOTABLE = frozenset({EvidenceClass.TECHNOGRAPHIC})
_SPACES = re.compile(r"\s+")


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Annotated[str, Field(min_length=1)]
    kind: FactKind
    value: Annotated[str, Field(min_length=1)]
    # The Lead field the value was read from, for example ``employments[0].title``.
    field: Annotated[str, Field(min_length=1)]
    # What the value proves, shown to a writer beside it but never part of it: a claim
    # states the value, so a check never has to match these words.
    context: str | None = None
    # Which wording this fact asks for, when one fact can be said more than one way:
    # a product in use reads differently from one a company has left behind.
    variant: str | None = None


class UsageContext(BaseModel):
    """The usage evidence behind a Lead, with the names its product keys stand for.

    The two travel together because neither is usable alone: a record names a product by
    its catalog key, and only the catalog knows the name that key stands for. A key is
    an identifier, never something to put in front of a person.

    This holds the Evidence Records rather than the Verdict they came from, because a
    Verdict is not kept per Lead but its records are: a preview written later can be
    built from the same evidence the run used.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: tuple[EvidenceRecord, ...]
    # Catalog key -> the name a person would recognise.
    product_names: Mapping[str, str]


class LeadFacts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    facts: tuple[Fact, ...]

    def get(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def of_kind(self, kind: FactKind) -> tuple[Fact, ...]:
        return tuple(f for f in self.facts if f.kind == kind)


def build_facts(
    stored: StoredLead, cfg: MessageConfig, usage: UsageContext | None = None
) -> LeadFacts:
    lead = stored.lead
    facts: list[Fact] = []
    if lead.full_name:
        facts.append(
            Fact(id="name", kind="name", value=lead.full_name, field="full_name")
        )
    # The employer the usage stage filed evidence under; a past one is not a fact.
    employment = current_employer(lead)
    if employment is not None and employment.is_current is not False:
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
    facts += _usage(usage, cfg)
    facts += _evidence(stored, cfg)
    return LeadFacts(facts=_unique(facts))


def render_facts(facts: LeadFacts) -> str:
    """The facts as one escaped ``<lead_facts>`` block, one ``[id] value`` per line.

    A fact that carries context states it in parentheses before the value. That is what
    the value proves, not words the Message may claim.
    """
    lines = [
        f"[{f.id}] " + (f"({f.context}) " if f.context else "") + f.value
        for f in facts.facts
    ]
    return delimit("lead_facts", "\n".join(lines))


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


def _usage(usage: UsageContext | None, cfg: MessageConfig) -> list[Fact]:
    """What the usage Verdict proves: the products in use, then the evidence for them.

    This evidence is what selected the Lead, so it holds the most specific true thing a
    Message can say. Each record yields two facts of different shapes, because the two
    writers can use different things. A ``product`` fact is the name alone, which a
    template can put in a sentence it knows how to finish. A ``usage`` fact is the
    sentence somebody published, which only a model can read and retell. Ids and fields
    name a record's place in the evidence, so the order a writer is shown them does not
    move them.
    """
    if usage is None:
        return []
    cited: list[tuple[int, EvidenceRecord, str]] = []
    for index, record in enumerate(usage.evidence):
        if record.relationship not in _CITABLE:
            continue
        quote = _quote(record.quote, cfg.max_usage_quote_chars)
        cited.append((index, record, quote))
    cited.sort(
        key=lambda row: (_CITABLE.index(row[1].relationship), -row[1].confidence)
    )
    return _products(cited, usage.product_names, cfg) + _quotes(cited, cfg)


def _products(
    cited: Sequence[tuple[int, EvidenceRecord, str]],
    names: Mapping[str, str],
    cfg: MessageConfig,
) -> list[Fact]:
    """One fact per product the records show in use, strongest relationship first.

    A record whose product the catalog cannot name is passed over rather than named by
    its key: ``astra`` is an identifier, not something to put in front of a person.
    """
    facts: list[Fact] = []
    seen: set[str] = set()
    for index, record, _ in cited:
        name = names.get(record.product_key)
        if not name or record.product_key in seen:
            continue
        seen.add(record.product_key)
        facts.append(
            Fact(
                id=f"product:{record.product_key}",
                kind="product",
                value=name,
                field=f"usage_evidence[{index}].product_key",
                context=record.relationship.value,
                variant=record.relationship.value,
            )
        )
    return facts[: cfg.max_hook_facts]


def _quotes(
    cited: Sequence[tuple[int, EvidenceRecord, str]], cfg: MessageConfig
) -> list[Fact]:
    """The published sentence behind each record, for a writer that can retell it.

    A technographic record's quote is machine-made (``technology <uid>``), so it carries
    nothing to retell; its product still counted above.
    """
    facts = [
        Fact(
            id=f"usage:{index}",
            kind="usage",
            value=quote,
            field=f"usage_evidence[{index}].quote",
            context=f"{record.evidence_class.value}, {record.relationship.value}, "
            + (record.observed_on.isoformat() if record.observed_on else "undated"),
        )
        for index, record, quote in cited
        if quote and record.evidence_class not in _UNQUOTABLE
    ]
    return facts[: cfg.max_hook_facts]


def _quote(text: str, limit: int) -> str:
    """One line of quotable words: whole words up to ``limit``, and no marker.

    A writer copies a run of this value and a check matches that run back against it, so
    what is stored here is what can be quoted. Line breaks go (a Message is one line of
    prose) and so do braces, which the schema check reads as an unfilled marker.
    """
    flat = _SPACES.sub(" ", text.replace("{", "").replace("}", "")).strip()
    if len(flat) <= limit:
        return flat
    whole, _, _ = flat[:limit].rpartition(" ")
    return whole or flat[:limit]


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
