"""Check every Message before it is kept (requirements 7.2, 7.3, 15.3).

Four deterministic checks, the same for a model's draft and an offline template:

* ``schema``: the shape of the kind (an invite has no subject, an email has one), a body
  with text, and no template marker left unfilled.
* ``length``: the configured character limits.
* ``banned_phrases``: none of the configured phrases, ignoring case.
* ``grounding``: at least one claim; every claim names a fact of the Lead, quotes words
  of the Message, and those words state the fact (hold its value, or are part of it);
  and no capitalised name or number in the Message is absent from every fact (a figure
  or proper name the Lead record never gave).

Checks only read the Message and the Lead's facts; they never call a model.
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from leadforge.outreach.config import MessageConfig
from leadforge.outreach.facts import LeadFacts

__all__ = ["Check", "Claim", "Draft", "all_passed", "check_message"]

# Words a Message may use that are names of things, not claims about the Lead.
_PLATFORM_WORDS = frozenset({"linkedin"})
# The pronoun and its contractions are capitalised wherever they stand.
_PRONOUN = frozenset({"i", "i'd", "i'm", "i've", "i'll"})
_ENTITY = re.compile(r"(?<![\w'-])(?:[A-Z][\w'-]*|\d[\d.,]*)")
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+|\n\s*)$")
_WORD = re.compile(r"[\w'-]+")
_MARKER = re.compile(r"\{[^{}]*\}")


class Claim(BaseModel):
    """One statement about the Lead: the words in the Message and the fact behind it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: Annotated[str, Field(min_length=1)]
    text: Annotated[str, Field(min_length=2)]


class Draft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["invite", "email"]
    subject: str | None
    body: str
    claims: tuple[Claim, ...]
    generator: Literal["llm", "offline"]
    model: Annotated[str, Field(min_length=1)]
    prompt_version: Annotated[str, Field(min_length=1)]


class Check(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["schema", "length", "banned_phrases", "grounding"]
    passed: bool
    detail: str | None = None


def all_passed(checks: tuple[Check, ...]) -> bool:
    return all(c.passed for c in checks)


def check_message(
    draft: Draft, facts: LeadFacts, cfg: MessageConfig
) -> tuple[Check, ...]:
    return (
        _schema(draft),
        _length(draft, cfg),
        _banned(draft, cfg),
        _grounding(draft, facts),
    )


def _schema(draft: Draft) -> Check:
    problems: list[str] = []
    if not draft.body.strip():
        problems.append("empty body")
    if draft.kind == "invite" and draft.subject is not None:
        problems.append("an invite has no subject")
    if draft.kind == "email" and not (draft.subject or "").strip():
        problems.append("an email needs a subject")
    if _MARKER.search(draft.body) or _MARKER.search(draft.subject or ""):
        problems.append("unfilled template marker")
    return Check(name="schema", passed=not problems, detail="; ".join(problems) or None)


def _length(draft: Draft, cfg: MessageConfig) -> Check:
    problems: list[str] = []
    if draft.kind == "invite":
        if len(draft.body) > cfg.invite_max_chars:
            problems.append(
                f"body is {len(draft.body)} of {cfg.invite_max_chars} chars"
            )
    else:
        if len(draft.body) > cfg.email_max_chars:
            problems.append(f"body is {len(draft.body)} of {cfg.email_max_chars} chars")
        if len(draft.subject or "") > cfg.email_subject_max_chars:
            size = len(draft.subject or "")
            problems.append(f"subject is {size} of {cfg.email_subject_max_chars} chars")
    return Check(name="length", passed=not problems, detail="; ".join(problems) or None)


def _banned(draft: Draft, cfg: MessageConfig) -> Check:
    text = f"{draft.subject or ''}\n{draft.body}".casefold()
    hits = [p for p in cfg.banned_phrases if p.casefold() in text]
    return Check(
        name="banned_phrases",
        passed=not hits,
        detail=f"contains {hits}" if hits else None,
    )


def _grounding(draft: Draft, facts: LeadFacts) -> Check:
    problems: list[str] = []
    if not draft.claims:
        problems.append("no claim ties the Message to the Lead")
    body = draft.body.casefold()
    for claim in draft.claims:
        fact = facts.get(claim.fact_id)
        if fact is None:
            problems.append(f"claim names no fact: {claim.fact_id}")
        elif claim.text.casefold() not in body:
            problems.append(f"claim text is not in the Message: {claim.fact_id}")
        elif not _states(claim.text, fact.value):
            problems.append(f"claim does not state the fact: {claim.fact_id}")
    ungrounded = _ungrounded(draft, facts)
    if ungrounded:
        problems.append(f"not in the Lead record: {ungrounded}")
    return Check(
        name="grounding", passed=not problems, detail="; ".join(problems) or None
    )


def _states(claim_text: str, fact_value: str) -> bool:
    """The claim holds the fact's value, or is part of it (a first name of a name)."""
    text, value = claim_text.casefold(), fact_value.casefold()
    return value in text or text in value


def _ungrounded(draft: Draft, facts: LeadFacts) -> list[str]:
    known = {w.casefold() for f in facts.facts for w in _WORD.findall(f.value)}
    out: list[str] = []
    for text in (draft.subject or "", draft.body):
        for match in _ENTITY.finditer(text):
            token = match.group(0).rstrip(".,")
            first = _SENTENCE_START.search(text[: match.start()]) is not None
            if token[0].isalpha() and first:
                continue  # a capital that only begins a sentence
            if token.casefold() in known | _PLATFORM_WORDS | _PRONOUN:
                continue
            out.append(token)
    return list(dict.fromkeys(out))
