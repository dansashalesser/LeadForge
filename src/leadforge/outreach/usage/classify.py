"""Offline usage classifier: alias + co-term gating and polarity cue phrases.

Page text is untrusted: it only ever selects among fixed outcomes, and
instruction-like text is dropped before any cue is read.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from leadforge.lead_ingestion.catalog import CatalogProduct
from leadforge.outreach.usage.fetch import Passages
from leadforge.outreach.usage.records import ClassifierStamp, Relationship

OFFLINE_VERSION = "offline_v1"


class Judgement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_is_target_company: bool
    product_key: str
    relationship: Relationship
    quotes: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)


class Classifier(Protocol):
    def classify(
        self, target: str, product: CatalogProduct, passages: Passages
    ) -> Judgement | None: ...


class UsageCues(BaseModel):
    """Polarity cue phrases (design defaults for `usage.cues`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vendor_or_partner: tuple[str, ...] = (
        "partner of", "our partner", "reseller", "we are a vendor", "sponsored by",
    )  # fmt: skip
    used_past: tuple[str, ...] = (
        "migrated off", "moved off", "moved away from", "no longer use",
        "used to use", "switched from", "replaced", "sunset",
    )  # fmt: skip
    evaluating: tuple[str, ...] = (
        " vs ", "versus", "compared to", "comparison", "evaluating", "considering",
        "alternative", "proof of concept",
    )  # fmt: skip
    uses_now: tuple[str, ...] = (
        "we use", "we run", "we rely on", "powered by", "built on", "migrated to",
        "moved to", "switched to", "runs on", "in production", "our stack",
    )  # fmt: skip
    injection: tuple[str, ...] = (
        "ignore previous", "ignore all", "ignore the above", "disregard",
        "system prompt", "mark as", "mark acme", "classify", "as customer",
    )  # fmt: skip


_ORDER = (
    Relationship.VENDOR_OR_PARTNER,
    Relationship.USED_PAST,
    Relationship.EVALUATING,
    Relationship.USES_NOW,
)
_CONFIDENCE = {Relationship.MENTIONS_ONLY: 0.4}
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


class OfflineClassifier:
    def __init__(self, cues: UsageCues) -> None:
        self._cues = cues

    def stamp(
        self, target: str, product: CatalogProduct, passages: Passages
    ) -> ClassifierStamp:
        payload = json.dumps(
            {
                "v": OFFLINE_VERSION,
                "target": target,
                "product": product.key,
                "aliases": [a.model_dump(mode="json") for a in product.aliases],
                "cues": self._cues.model_dump(mode="json"),
                "passages": list(passages.passages),
            },
            sort_keys=True,
        )
        return ClassifierStamp(
            kind="offline",
            model=None,
            prompt_version=OFFLINE_VERSION,
            input_hash=hashlib.sha256(payload.encode()).hexdigest(),
        )

    def classify(
        self, target: str, product: CatalogProduct, passages: Passages
    ) -> Judgement | None:
        best: tuple[Relationship, str] | None = None
        for passage in passages.passages:
            hit = self._scan(passage, product)
            if hit and (best is None or _rank(hit[0]) < _rank(best[0])):
                best = hit
        if best is None:
            return None
        rel, quote = best
        return Judgement(
            subject_is_target_company=True,
            product_key=product.key,
            relationship=rel,
            quotes=(quote,),
            confidence=_CONFIDENCE.get(rel, 0.7),
        )

    def _scan(
        self, passage: str, product: CatalogProduct
    ) -> tuple[Relationship, str] | None:
        low = passage.lower()
        if any(m in low for m in self._cues.injection):
            return None
        best: tuple[Relationship, str] | None = None
        for alias in product.aliases:
            pat = re.compile(rf"(?<!\w){re.escape(alias.text)}(?!\w)", re.IGNORECASE)
            if alias.co_terms and not any(_has_word(low, c) for c in alias.co_terms):
                continue
            for m in _SENTENCE.finditer(passage):
                sentence = m.group().strip()
                if not sentence or not pat.search(sentence):
                    continue
                hit = (self._polarity(sentence), sentence)
                if best is None or _rank(hit[0]) < _rank(best[0]):
                    best = hit
        return best

    def _polarity(self, sentence: str) -> Relationship:
        low = f" {sentence.lower()} "
        for rel in _ORDER:
            if any(cue in low for cue in getattr(self._cues, rel.value)):
                return rel
        return Relationship.MENTIONS_ONLY


def _has_word(low: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", low) is not None


def _rank(rel: Relationship) -> int:
    return _ORDER.index(rel) if rel in _ORDER else len(_ORDER)


__all__: Sequence[str] = [
    "OFFLINE_VERSION",
    "Classifier",
    "Judgement",
    "OfflineClassifier",
    "UsageCues",
]
