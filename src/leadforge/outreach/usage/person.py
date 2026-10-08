"""Person Fit grading (Req 7.1, 7.2): word-boundary token match on role vocabulary."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

PersonGrade = Literal["core", "adjacent", "irrelevant"]

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class RoleVocabulary:
    """Phrases per grade; a phrase matches only as whole consecutive words."""

    core: tuple[str, ...] = ()
    adjacent: tuple[str, ...] = ()
    irrelevant: tuple[str, ...] = ()


@dataclass(frozen=True)
class PersonFit:
    grade: PersonGrade
    boosted: bool = False


def _words(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(text.lower()))


def _contains(words: tuple[str, ...], phrase: str) -> bool:
    want = _words(phrase)
    if not want:
        return False
    n = len(want)
    return any(words[i : i + n] == want for i in range(len(words) - n + 1))


def _hits(words: tuple[str, ...], phrases: Iterable[str]) -> bool:
    return any(_contains(words, p) for p in phrases)


def grade_person_fit(
    fields: Iterable[str],
    roles: RoleVocabulary,
    *,
    self_stated_confirmed: bool = False,
    self_stated: bool = False,
) -> PersonFit:
    """Grade from title/departments/functions/seniority headline strings.

    Irrelevant wins over core (a "Sales Engineer" is not a user), unknown is
    irrelevant. A self-stated product mention lifts adjacent to core only when
    a confirmed record backs it; an unconfirmed claim never changes the grade.
    """
    words = _words(" ; ".join(f for f in fields if f))
    if _hits(words, roles.irrelevant):
        return PersonFit("irrelevant")
    if _hits(words, roles.core):
        return PersonFit("core")
    if _hits(words, roles.adjacent):
        if self_stated and self_stated_confirmed:
            return PersonFit("core", boosted=True)
        return PersonFit("adjacent")
    return PersonFit("irrelevant")
