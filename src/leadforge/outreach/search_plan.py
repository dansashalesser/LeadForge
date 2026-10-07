"""The Search Plan: what one search looks for, in one of three modes (1.1, 1.3).

A plan is in memory only; ``plan_to_profile`` turns it into the Target Profile the
ingestion run takes. A mode outside the three, or a technology term the provider
vocabulary does not know, is a named error raised before any provider call.
"""

import re
from collections.abc import Collection
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from leadforge.outreach.errors import UnknownModeError, UnknownTermError

__all__ = [
    "MODES",
    "Compiler",
    "Mode",
    "SearchPlan",
    "SearchRequest",
    "check_terms",
    "parse_mode",
    "parse_request",
]

Mode = Literal["free_text", "workers", "users"]
Compiler = Literal["llm", "offline"]
MODES: tuple[str, ...] = get_args(Mode)
_MODE_BY_NAME: dict[str, Mode] = {m: m for m in get_args(Mode)}

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN = re.compile(rf"{_LABEL}(?:\.{_LABEL})+")

Text = Annotated[str, Field(min_length=1, max_length=2048)]
Domain = Annotated[str, Field(pattern=f"^{_DOMAIN.pattern}$", max_length=253)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchRequest(_Frozen):
    """What the operator asked: a mode, the words, and domains they know."""

    mode: Mode
    query: Text
    domains: tuple[Domain, ...] = ()


class SearchPlan(_Frozen):
    mode: Mode
    query: Text
    company: str | None = None
    domains: tuple[Domain, ...] = ()
    terms: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    seniorities: tuple[str, ...] = ()
    compiler: Compiler
    # True when a users-mode company had no entry in the company-terms file.
    unmapped: bool = False


def parse_mode(value: str) -> Mode:
    """The mode named by ``value``, or ``UnknownModeError``."""
    mode = _MODE_BY_NAME.get(value)
    if mode is None:
        raise UnknownModeError(value, accepted=MODES)
    return mode


def parse_request(
    mode: str, query: str, domains: Collection[str] = ()
) -> SearchRequest:
    """A validated request; the mode is checked first so its error is the named one."""
    return SearchRequest(
        mode=parse_mode(mode),
        query=query.strip(),
        domains=tuple(d.strip().lower() for d in domains),
    )


def check_terms(plan: SearchPlan, known: Collection[str]) -> None:
    """Raise ``UnknownTermError`` for the first term of the plan not in ``known``.

    ``known`` is every term of the provider vocabulary (the base Target Profile). A
    users-mode plan flagged ``unmapped`` carries no term: its company name is the
    search phrase, which no provider vocabulary has to know.
    """
    for term in plan.terms:
        if term not in known:
            raise UnknownTermError(term)
