"""Compile free text into a Search Plan without a model (requirement 2.2).

Rule based and network free: a term of the base Target Profile is named when the query
holds the term, or any plain phrase or identifier the profile gives for it, as whole
words. Titles and seniorities are read with a few fixed patterns. A query that names no
known term cannot be searched, so it is a ``PlanCompileError``, not an empty plan.
"""

import re
from collections.abc import Iterator, Mapping

from leadforge.lead_ingestion.target_profile import TargetProfile
from leadforge.outreach.errors import PlanCompileError
from leadforge.outreach.search_plan import SearchPlan, SearchRequest

__all__ = ["OfflineCompiler", "phrases_of"]

_ACRONYM_TITLES = re.compile(r"\b(cto|cio|ceo|coo|cfo|ciso|cdo)\b")
_STOP = r"(?:at|in|for|who|with|and|or|on|using|running|that|of|the)"
_OF_TITLES = re.compile(
    rf"\b(?:head|vp|vice president|director|chief|manager|lead) of [a-z]+"
    rf"(?: (?!{_STOP}\b)[a-z]+)?"
)
_ROLE_TITLES = re.compile(r"\b(?:[a-z]+ )?(?:engineer|architect|developer|analyst)s?\b")
_SENIORITIES = ("founder", "owner", "vp", "head", "director", "manager", "senior")


class OfflineCompiler:
    """``SearchCompiler`` that reads a query with rules and the base profile."""

    def __init__(self, base: TargetProfile) -> None:
        self._phrases = {term: phrases_of(base, term) for term in base.terms()}

    def compile(self, request: SearchRequest) -> SearchPlan:
        text = _words(request.query)
        terms = tuple(
            term
            for term, phrases in self._phrases.items()
            if any(_holds(text, phrase) for phrase in phrases)
        )
        if not terms:
            raise PlanCompileError("the query names no technology the profile knows")
        return SearchPlan(
            mode="free_text",
            query=request.query,
            terms=terms,
            titles=_titles(text),
            seniorities=tuple(
                s for s in _SENIORITIES if _holds(text, s) or _holds(text, f"{s}s")
            ),
            compiler="offline",
        )


def _words(text: str) -> str:
    return re.sub(r"[^a-z0-9+#.]+", " ", text.casefold()).strip()


def _holds(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def _titles(text: str) -> tuple[str, ...]:
    found = [
        m.group(0).strip()
        for p in (_ACRONYM_TITLES, _OF_TITLES)
        for m in p.finditer(text)
    ]
    found += [m.group(0).strip() for m in _ROLE_TITLES.finditer(text)]
    return tuple(dict.fromkeys(found))


def phrases_of(base: TargetProfile, term: str) -> tuple[str, ...]:
    phrases = [_words(term.replace("_", " "))]
    columns = {**base.competitors, **base.technologies}.get(term, {})
    for vocabulary in columns.values():
        phrases.extend(_words(p.replace("_", " ")) for p in _strings(vocabulary))
    return tuple(dict.fromkeys(p for p in phrases if p))


def _strings(vocabulary: object) -> Iterator[str]:
    """Every plain string in a vocabulary value (a string, or a list of them)."""
    if isinstance(vocabulary, str):
        yield vocabulary
    elif isinstance(vocabulary, list | tuple):
        for item in vocabulary:
            yield from _strings(item)
    elif isinstance(vocabulary, Mapping):
        return
