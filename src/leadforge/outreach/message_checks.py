"""Check every Message before it is kept (requirements 7.2, 7.3, 15.3).

Four deterministic checks, the same for a model's draft and an offline template:

* ``schema``: the shape of the kind (an invite has no subject, an email has one), a body
  with text, and no template marker left unfilled.
* ``length``: the configured character limits.
* ``banned_phrases``: none of the configured phrases, ignoring case.
* ``grounding``: at least one claim; every claim names a fact of the Lead, quotes words
  of the Message, and those words state the fact; and no capitalised name or number in
  the Message is absent from every fact (a figure or proper name the Lead record never
  gave).

A short fact (a name, a title, a signal) is stated by holding its value or being part of
it. A long one (a published quote) is stated by using only its words, so a Message can
retell it in its own voice instead of pasting it: see ``_within``.

Checks only read the Message and the Lead's facts; they never call a model.
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from leadforge.outreach.config import MessageConfig
from leadforge.outreach.facts import Fact, FactKind, LeadFacts

__all__ = ["Check", "Claim", "Draft", "all_passed", "check_message"]

# Words a Message may use that are names of things, not claims about the Lead.
_PLATFORM_WORDS = frozenset({"linkedin"})
# Facts whose value is published prose rather than a single thing, so a claim may retell
# the value instead of repeating it word for word.
_RETOLD: frozenset[FactKind] = frozenset({"usage", "evidence"})
# Words a retelling may bring in that assert nothing: grammar, and the pronouns that let
# a company's own "our ledger" be addressed back to a person as "your ledger".
_FREE_WORDS = (
    # articles, demonstratives, conjunctions, prepositions
    "a an the this that these those there here and or but so as if then than "
    "of to in on at by for from with about into over under "
    # the verbs that only carry grammar
    "is are was were be been being has have had do does did "
    # pronouns: whose side a sentence speaks from is not a claim
    "i me my mine we us our ours you your yours they them their theirs "
    "he him his she her hers it its who whose which what when where how "
    # intensifiers, and the tails of split contractions
    "too very just also still yet s re ve ll d m"
)
_FREE = frozenset(_FREE_WORDS.split())
# Words that turn a statement into its opposite. A retelling keeps the quote's
# polarity: it may not negate what the quote affirms, nor drop a negation it holds.
# "t" is the tail of a contraction split at a typographic apostrophe (U+2019).
_NEGATIONS = frozenset({"not", "no", "nor", "never", "t"})
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
        elif not _states(claim.text, fact):
            problems.append(f"claim does not state the fact: {claim.fact_id}")
    ungrounded = _ungrounded(draft, facts)
    if ungrounded:
        problems.append(f"not in the Lead record: {ungrounded}")
    return Check(
        name="grounding", passed=not problems, detail="; ".join(problems) or None
    )


def _states(claim_text: str, fact: Fact) -> bool:
    """The claim states the fact: word for word, or retold for a published quote."""
    if fact.kind in _RETOLD:
        return _within(claim_text, fact.value)
    text, value = claim_text.casefold(), fact.value.casefold()
    return value in text or text in value


def _within(claim_text: str, fact_value: str) -> bool:
    """Every word the claim asserts is a word of the quote.

    A quote is prose someone else published, usually about the company rather than to
    the person, so a Message that reads as one person writing to another has to retell
    it. A retelling may drop words, reorder them, change their number and speak from the
    other side; it may not bring in a word the quote does not hold. That keeps the claim
    answerable to the quote without demanding it be pasted in. It must assert at
    least one word of the quote, and say yes where the quote says yes.
    """
    known = set(_WORD.findall(fact_value.casefold()))
    said = set(_WORD.findall(claim_text.casefold()))
    asserted = {w for w in said if w not in _FREE}
    return (
        bool(asserted)
        and all(_known(w, known) for w in asserted)
        and _negated(said) == _negated(known)
    )


def _negated(words: set[str]) -> bool:
    return any(w in _NEGATIONS or w.endswith("n't") for w in words)


def _known(word: str, known: set[str]) -> bool:
    """The word is in the quote, give or take a plural."""
    return word in known or word.rstrip("s") in known or f"{word}s" in known


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
