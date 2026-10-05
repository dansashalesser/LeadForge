"""Declarative field rules and mechanical provenance emission (task 5.1).

A provider's mapping is data: a sequence of ``FieldRule``. ``Normalizer.apply`` walks it
and emits provenance, so "one record per populated field, none for an empty one" is
true by construction rather than something each adapter author must remember.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.errors import NormalizationError
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
    SourceAbsence,
    UntrustedText,
)

__all__ = [
    "DEFAULT_UNTRUSTED_MAX_LENGTH",
    "FieldRule",
    "NormalizationContext",
    "Normalizer",
    "unmapped_raw_paths",
    "validate_raw_payload",
]

# Characters (code points, the unit UntrustedText.original_length uses), not bytes.
DEFAULT_UNTRUSTED_MAX_LENGTH = 4000


@dataclass(frozen=True)
class FieldRule:
    """Where one canonical field comes from; raw paths are dotted ('person.email')."""

    canonical_path: str
    raw_field_path: str
    untrusted: bool = False
    transform: Callable[[object], object] | None = None

    def __post_init__(self) -> None:
        for field in ("canonical_path", "raw_field_path"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"FieldRule.{field} must be a non-blank str")


@dataclass(frozen=True)
class NormalizationContext:
    """What the normalizer needs to know about this fetch, not about the payload.

    ``answerable_surfaces`` is the adapter's declaration (3.1). ``queried_paths`` are
    the canonical paths the source was actually asked about in this fetch; a rule
    whose path is absent from it yields no ``SourceAbsence`` at all.
    """

    source_name: str
    data_mode: DataMode
    fetched_at: datetime
    answerable_surfaces: Mapping[str, frozenset[str]]
    queried_paths: frozenset[str] = frozenset()
    untrusted_max_length: int = DEFAULT_UNTRUSTED_MAX_LENGTH

    def __post_init__(self) -> None:
        bound = self.untrusted_max_length
        if type(bound) is not int or bound < 1:
            raise ValueError("untrusted_max_length must be a positive int")


def _refusal(rule: FieldRule, context: NormalizationContext) -> NormalizationError:
    # Names only the rule's declared paths, never a payload value or key.
    return NormalizationError(
        context.source_name,
        raw_field_path=rule.raw_field_path,
        canonical_path=rule.canonical_path,
    )


def _resolve(
    raw: Mapping[str, object], rule: FieldRule, context: NormalizationContext
) -> object | None:
    """Value at the rule's raw path; None when absent or a parent is null.

    A parent that is present but not a mapping is a wrong shape, not an absence, and
    raises: treating it as empty would record Negative Evidence for a payload that
    never answered the question.
    """
    node: object = raw
    for key in rule.raw_field_path.split("."):
        if node is None:
            return None
        if not isinstance(node, Mapping):
            raise _refusal(rule, context)
        if key not in node:
            return None
        node = node[key]
    return node


class Normalizer:
    def apply(
        self,
        raw: Mapping[str, object],
        rules: Sequence[FieldRule],
        context: NormalizationContext,
    ) -> LeadContribution:
        canonical = [r.canonical_path for r in rules]
        for path in set(canonical):
            if canonical.count(path) > 1:
                raise ValueError(f"two field rules fill canonical path {path!r}")
        values: dict[str, Any] = {}
        provenance: list[FieldProvenance] = []
        absences: list[SourceAbsence] = []
        for rule in rules:
            value = _resolve(raw, rule, context)
            if value is not None and rule.transform is not None:
                try:
                    value = rule.transform(value)
                except Exception:  # noqa: BLE001 - any transform failure is named
                    # The transform's message may quote untrusted provider text.
                    raise _refusal(rule, context) from None
            if value is None:
                absence = self._absence(rule, context)
                if absence is not None:
                    absences.append(absence)
                continue
            values[rule.canonical_path] = self._store(rule, value, context)
            provenance.append(self._provenance(rule, context))
        return LeadContribution(
            source_name=context.source_name,
            absences=tuple(absences),
            values=values,
            provenance=tuple(provenance),
        )

    @staticmethod
    def _store(rule: FieldRule, value: object, context: NormalizationContext) -> object:
        def refuse() -> NormalizationError:
            return _refusal(rule, context)

        if isinstance(value, UntrustedText):
            # Only the rule's own untrusted flag may produce one; a transform cannot.
            raise refuse()
        if not rule.untrusted:
            return value
        if not isinstance(value, str):
            raise refuse()  # never coerce provider data into text
        # Verbatim prefix: no marker, no trimming, no normalization of the text.
        kept = value[: context.untrusted_max_length]
        return UntrustedText(
            value=kept,
            truncated=len(kept) < len(value),
            original_length=len(value),
        )

    @staticmethod
    def _provenance(rule: FieldRule, context: NormalizationContext) -> FieldProvenance:
        return FieldProvenance(
            canonical_path=rule.canonical_path,
            source_name=context.source_name,
            data_mode=context.data_mode,
            fetched_at=context.fetched_at,
            raw_field_path=rule.raw_field_path,
            confidence_origin=ConfidenceOrigin.NONE,
            untrusted=rule.untrusted,
        )

    @staticmethod
    def _absence(
        rule: FieldRule, context: NormalizationContext
    ) -> SourceAbsence | None:
        if rule.canonical_path not in context.queried_paths:
            return None
        if rule.canonical_path in context.answerable_surfaces:
            return SourceAbsence(
                canonical_path=rule.canonical_path,
                source_name=context.source_name,
                kind=AbsenceKind.NEGATIVE_EVIDENCE,
                raw_field_path=rule.raw_field_path,
            )
        return SourceAbsence(
            canonical_path=rule.canonical_path,
            source_name=context.source_name,
            kind=AbsenceKind.NOT_APPLICABLE,
        )


def _leaf_paths(node: Mapping[str, object], prefix: str = "") -> Iterator[str]:
    for key, child in node.items():
        path = f"{prefix}{key}"
        if isinstance(child, Mapping) and child:
            yield from _leaf_paths(child, f"{path}.")
        else:
            yield path


def _covers(declared: str, path: str) -> bool:
    return path == declared or path.startswith(f"{declared}.")


def unmapped_raw_paths(
    raw: Mapping[str, object],
    rules: Sequence[FieldRule],
    ignored: frozenset[str],
) -> list[str]:
    """Leaf raw paths present in ``raw`` that no rule maps and no ignore entry covers.

    Meant for the per-provider fixture test: a field the provider sends that is
    neither mapped nor listed as intentionally ignored is a silent drop, so the test
    asserts this list is empty. A mapped or ignored path covers its whole subtree. A
    null-valued field is still present; lists and empty mappings are leaves.
    """
    declared = {r.raw_field_path for r in rules} | set(ignored)
    return sorted(
        p for p in _leaf_paths(raw) if not any(_covers(d, p) for d in declared)
    )


_UNMAPPED = "<unmapped>"
_PATH_LIMIT = 120


def _safe_path(loc: Sequence[int | str]) -> str:
    # loc can carry a provider-chosen key (extra="forbid"), so escape and bound it.
    text = ".".join(str(part) for part in loc)
    return ascii(text)[1:-1][:_PATH_LIMIT]


def validate_raw_payload(
    provider: str,
    model: type[BaseModel],
    raw: Mapping[str, object],
    rules: Sequence[FieldRule],
) -> Mapping[str, object]:
    """Check ``raw`` against the adapter's declared raw model before mapping it.

    Returns ``raw`` unchanged (the validated copy is discarded, no coercion) or raises
    ``NormalizationError`` for the first violation, naming the provider, the raw path
    and the canonical path of the rule covering it (``<unmapped>`` if none does).
    Pydantic's message and input value are dropped: they can echo provider text.
    """
    try:
        model.model_validate(raw)
    except ValidationError as exc:
        loc = exc.errors(include_input=False, include_url=False)[0]["loc"]
        raw_path = _safe_path(loc)
        canonical = next(
            (r.canonical_path for r in rules if _covers(r.raw_field_path, raw_path)),
            _UNMAPPED,
        )
        raise NormalizationError(
            provider, raw_field_path=raw_path, canonical_path=canonical
        ) from None
    return raw
