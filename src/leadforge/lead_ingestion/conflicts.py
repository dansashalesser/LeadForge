"""Field-conflict resolution under a total order (task 16.3; Requirements 8.4, 8.8).

A pure function from one ``IdentityCluster`` and a Source Trust Rank mapping to, per
canonical path, a winner and the ordered losers. No I/O, no globals, input not
mutated. Provisional decisions (choices.md, 16.3):

* Order, best first: higher Source Trust Rank; Confidence Origin (provider-stated,
  then heuristic, then none); higher Field Confidence; more recent ``fetched_at``;
  then the design's additions: source name, sha256 of the canonical value, and last
  the raw field path and canonical provenance, so two candidates compare equal only
  when they are byte-identical and therefore interchangeable (8.8).
* Origin is compared before the number, so a heuristic confidence never outranks a
  provider-stated one. Origin ``none`` ranks below heuristic: it states nothing, and
  no confidence is fabricated for it; the number is not read for it.
* Source Trust Rank is a mapping passed in (the caller builds it from the settings
  loaded from ``config/``, 8.10); a source missing from it takes ``LOWEST_TRUST_RANK``,
  as the registry does for a source with no config entry.
* Candidates holding the same value as the winner are ``agreeing`` (task 16.4 counts
  them); the rest are ``superseded``. Provenance is passed through unmarked: retaining
  it as superseded is task 16.4.
* Signal Strength is not an input: nothing here reads it (Requirement 24.4).
* ``SourceAbsence`` records never compete: they are returned apart, by kind, so
  Negative Evidence and Not Applicable stay distinguishable from each other and from
  a plain absence (a path with no value and no absence).
* ``FieldResolution.decided_by`` names the rule that separated the winner from the
  closest losing value (the first losing candidate in the total order): the first
  component of the order on which the two differ (task 16.12, Requirement 21.4). It is
  derived from the same order key, so it cannot disagree with the winner; ``None`` when
  nobody lost. Names a rule, never a value.
* A request echo (raw path under ``REQUEST_ECHO_PREFIX``: the identity an enrichment
  source was asked about, not something it observed) competes for a path only when no
  observed candidate holds it. Otherwise it is left out, so it never counts as an
  agreeing source, never creates a conflict and never outranks the requester's own
  value; alone, it still fills the field.
* ``person.email`` only (user decision 2026-10-06, Requirement 8.4 amendment): two
  rules come before the order above. A candidate whose own contribution states
  ``person.email_status`` VERIFIED beats any other status; then a personal address
  beats a role address (one of ``IdentityCluster.role_addresses``: the 8.14 pass's
  shared or role-word addresses, such as ``info@``). Both are read with
  ``match_keys.personal_email``, as clustering reads them. For every other path the two
  components are equal, so they never decide and 8.4 stands unchanged.
* Values and sources are personal data: errors name types and source names only.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from hashlib import sha256
from typing import Any

from leadforge.lead_ingestion.clustering import (
    IdentityCluster,
    canonical_value_json,
)
from leadforge.lead_ingestion.match_keys import personal_email
from leadforge.lead_ingestion.models import (
    REQUEST_ECHO_PREFIX,
    AbsenceKind,
    ConfidenceOrigin,
    FieldProvenance,
    Signal,
    SourceAbsence,
)
from leadforge.lead_ingestion.registry import LOWEST_TRUST_RANK

__all__ = [
    "ClusterResolution",
    "ConflictRule",
    "FieldCandidate",
    "FieldResolution",
    "resolve_conflicts",
    "validate_trust_ranks",
]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ORIGIN_TIER = {
    ConfidenceOrigin.PROVIDER_STATED: 2,
    ConfidenceOrigin.HEURISTIC: 1,
    ConfidenceOrigin.NONE: 0,
}
_EMAIL = "person.email"
# Any other path: both email preference components equal, so they never decide.
_NO_PREFERENCE = (0, 0)


class ConflictRule(Enum):
    """The rule of 8.4 that decided a conflict; ``TIE_BREAK`` is the design extras.

    ``VERIFIED_EMAIL`` and ``PERSONAL_EMAIL`` decide ``person.email`` only.
    """

    VERIFIED_EMAIL = "verified_email"
    PERSONAL_EMAIL = "personal_email"
    TRUST_RANK = "trust_rank"
    CONFIDENCE_ORIGIN = "confidence_origin"
    CONFIDENCE = "confidence"
    RECENCY = "recency"
    TIE_BREAK = "tie_break"


# Positions of the order key (see ``_order_key``) that name a rule of 8.4.
_RULES = (
    ConflictRule.VERIFIED_EMAIL,
    ConflictRule.PERSONAL_EMAIL,
    ConflictRule.TRUST_RANK,
    ConflictRule.CONFIDENCE_ORIGIN,
    ConflictRule.CONFIDENCE,
    ConflictRule.RECENCY,
)


@dataclass(frozen=True)
class FieldCandidate:
    """One contribution's value for a path, with the provenance that came with it."""

    source_name: str
    value: Any = field(repr=False)
    provenance: FieldProvenance = field(repr=False)


@dataclass(frozen=True)
class FieldResolution:
    """The winner for one path; ``agreeing`` and ``superseded`` are in total order."""

    canonical_path: str
    winner: FieldCandidate
    agreeing: tuple[FieldCandidate, ...] = ()
    superseded: tuple[FieldCandidate, ...] = ()
    decided_by: ConflictRule | None = None


@dataclass(frozen=True)
class ClusterResolution:
    """Resolutions by path (sorted), plus the absences, which never compete."""

    fields: tuple[FieldResolution, ...]
    negative_evidence: tuple[SourceAbsence, ...] = ()
    not_applicable: tuple[SourceAbsence, ...] = ()

    def field(self, canonical_path: str) -> FieldResolution:
        for resolution in self.fields:
            if resolution.canonical_path == canonical_path:
                return resolution
        raise KeyError(canonical_path)


def validate_trust_ranks(trust_ranks: Mapping[str, int]) -> None:
    """Reject a trust rank that is not an int (bool included) or below the lowest."""
    for name, rank in trust_ranks.items():
        if not isinstance(rank, int) or isinstance(rank, bool):
            raise TypeError(f"trust rank of source {name!r} must be an int")
        if rank < LOWEST_TRUST_RANK:
            raise ValueError(
                f"trust rank of source {name!r} must be >= {LOWEST_TRUST_RANK}"
            )


def resolve_conflicts(
    cluster: IdentityCluster, trust_ranks: Mapping[str, int]
) -> ClusterResolution:
    """Resolve every path of ``cluster``; the result ignores contribution order."""
    validate_trust_ranks(trust_ranks)

    by_path: dict[str, list[tuple[tuple[Any, ...], str, FieldCandidate]]] = {}
    absences: list[SourceAbsence] = []
    for contribution in cluster.contributions:
        absences.extend(contribution.absences)
        for record in contribution.provenance:
            value = contribution.values[record.canonical_path]
            value_json = canonical_value_json(_without_strength(value))
            rank = trust_ranks.get(record.source_name, LOWEST_TRUST_RANK)
            preference = (
                _email_preference(contribution.values, cluster.role_addresses)
                if record.canonical_path == _EMAIL
                else _NO_PREFERENCE
            )
            by_path.setdefault(record.canonical_path, []).append(
                (
                    _order_key(record, rank, value_json, preference),
                    value_json,
                    FieldCandidate(record.source_name, value, record),
                )
            )

    fields = tuple(
        _resolve_path(path, sorted(_observed_first(entries), key=lambda e: e[0]))
        for path, entries in sorted(by_path.items())
    )
    ordered = sorted(
        absences,
        key=lambda a: (a.canonical_path, a.source_name, a.kind, a.raw_field_path or ""),
    )
    return ClusterResolution(
        fields,
        tuple(a for a in ordered if a.kind is AbsenceKind.NEGATIVE_EVIDENCE),
        tuple(a for a in ordered if a.kind is AbsenceKind.NOT_APPLICABLE),
    )


def _observed_first(
    entries: list[tuple[tuple[Any, ...], str, FieldCandidate]],
) -> list[tuple[tuple[Any, ...], str, FieldCandidate]]:
    """The observed candidates of a path; its request echoes only if there are none."""
    observed = [
        entry
        for entry in entries
        if not entry[2].provenance.raw_field_path.startswith(REQUEST_ECHO_PREFIX)
    ]
    return observed or entries


def _without_strength(value: object) -> object:
    """The value as compared: a Signal minus its Signal Strength (24.4).

    Strength is recorded at ingestion and never decides a merge, so two Signals that
    differ only in strength are the same value for agreement and for every tie-break.
    The candidate itself keeps its original value, strength included.
    """
    if isinstance(value, Signal):
        return {
            "signal": type(value).__name__,
            **value.model_dump(mode="json", exclude={"strength"}),
        }
    if isinstance(value, tuple | list):
        return [_without_strength(item) for item in value]
    return value


def _email_preference(
    values: Mapping[str, object], role_addresses: frozenset[str]
) -> tuple[int, int]:
    """``(not verified, role)``: ascending puts verified, then personal, first."""
    stated = personal_email(values)
    if stated is None:
        return (1, 0)
    return (0 if stated.verified else 1, int(stated.address in role_addresses))


def _order_key(
    record: FieldProvenance,
    rank: int,
    value_json: str,
    preference: tuple[int, int],
) -> tuple[Any, ...]:
    origin = record.confidence_origin
    # Origin none carries no confidence; it must not be read as 0.0 against a number.
    confidence = 0.0 if record.confidence is None else record.confidence
    return (
        *preference,
        -rank,
        -_ORIGIN_TIER[origin],
        -confidence,
        -((record.fetched_at - _EPOCH) // timedelta(microseconds=1)),
        record.source_name,
        sha256(value_json.encode("utf-8")).hexdigest(),
        record.raw_field_path,
        record.model_dump_json(),
    )


def _resolve_path(
    path: str, entries: list[tuple[tuple[Any, ...], str, FieldCandidate]]
) -> FieldResolution:
    winning_key, winning_json, winner = entries[0]
    rest = entries[1:]
    losers = [(k, c) for k, v, c in rest if v != winning_json]
    return FieldResolution(
        path,
        winner,
        tuple(c for _, v, c in rest if v == winning_json),
        tuple(c for _, c in losers),
        _deciding_rule(winning_key, losers[0][0]) if losers else None,
    )


def _deciding_rule(winning: tuple[Any, ...], closest: tuple[Any, ...]) -> ConflictRule:
    for rule, won, lost in zip(_RULES, winning, closest, strict=False):
        if won != lost:
            return rule
    return ConflictRule.TIE_BREAK
