"""Write and read a ``LeadContribution`` with its untrusted classification (task 6.6).

``contribution_field.value`` is a JSON column, and the classification is kept out of it
on purpose: the ``untrusted``, ``truncated`` and ``original_length`` columns carry it,
and ``value`` holds only the text, verbatim. Reading rebuilds an ``UntrustedText`` only
from those columns, so no provider string (even one that looks like a serialized
marker) can make a trusted value read back as classified, and an untrusted one cannot
read back as trusted unless its row was altered out of band, which the append-only
guard (8.12) forbids at the ORM level.

Two adapter values are not JSON: an aware datetime and a tuple of URIs.
They are stored as an ISO-8601 UTC string and a JSON array (an offset is normalised
to UTC; a naive datetime is refused), so they read back as that string and list, not as
the original type. A set, bytes, a date, a non-finite float, a non-string key or any
other type is refused with `ContributionValueError`, which names the canonical path
and the offending type but never the value.

Time: instants are normalised to aware UTC before binding and re-tagged UTC on read,
with no backend branch (the same approach as ``raw_responses``).

`write_contribution` is synchronous, takes the session handed to a
``StoreWriter.write_batch`` callable, and returns a plain ``uuid.UUID``; it does not
commit. Since 0006 it also keeps each field's confidence origin, raw value and scale,
the absences and the ``content_sha``, so ``load_lead_contributions`` rebuilds the
``LeadContribution`` a re-merge needs (follow-up, user option A, 2026-10-06):

* ``contribution_sha`` is the identity of an observation: the sha256 of
  ``clustering.canonical_json`` with every ``fetched_at`` left out, so the same answer
  fetched again is the same contribution and is stored once (UNIQUE ``content_sha``).
  It is an in-memory identity only: the stored ``content_sha`` is its HMAC under the
  store's key (``store_key.store_digest``, follow-up 2026-10-06), never the plain
  hash of personal data.
* Read back, a datetime value is its ISO-8601 text and a tuple a list (as above); both
  serialise to the same canonical JSON, so the identity and every merge comparison
  are unchanged. Absences are a set: they read back sorted by path and kind, and the
  identity sorts them.
* A row written before 0006 has no origin: it reads back as ``none`` when it has no
  confidence and ``heuristic`` when it has one (raw value and scale unknown), with no
  absences. Lossy, and only for those rows.
"""

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import canonical_json, canonical_value_json
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
    SourceAbsence,
    UntrustedText,
)
from leadforge.lead_ingestion.store.models import (
    ContributionAbsence,
    ContributionField,
    SourceContribution,
)
from leadforge.lead_ingestion.store.store_key import store_digest

__all__ = [
    "ContributionValueError",
    "StoredContribution",
    "StoredFieldError",
    "aware_utc",
    "contribution_sha",
    "load_lead_contributions",
    "read_contribution",
    "stored_provenance",
    "stored_value",
    "write_contribution",
]

_MAX_DEPTH = 32


class ContributionValueError(ValueError):
    """A contribution cannot be stored without altering or misclassifying a value."""


class StoredFieldError(ValueError):
    """A stored field row carries an inconsistent classification."""


@dataclass(frozen=True)
class StoredContribution:
    source_name: str
    data_mode: DataMode
    fetched_at: datetime  # aware UTC
    values: dict[str, Any]  # canonical path -> trusted value or ``UntrustedText``
    # None once the raw payload expired and was purged (9.8); the fields stay readable.
    raw_response_id: uuid.UUID | None = None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must carry a timezone (naive datetimes are refused)")
    return value.astimezone(UTC)


def _encode(value: object, path: str, depth: int = 0) -> Any:
    """The JSON form of a trusted value; anything JSON cannot hold is refused."""

    def refuse(why: str) -> ContributionValueError:
        return ContributionValueError(f"value of {path!r} {why}")

    if depth > _MAX_DEPTH:
        raise refuse("is nested too deeply")
    if isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise refuse("is a non-finite float")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise refuse("is a naive datetime")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, list | tuple):
        return [_encode(item, path, depth + 1) for item in value]
    if isinstance(value, dict):
        encoded: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise refuse("has a non-string mapping key")
            encoded[key] = _encode(item, path, depth + 1)
        return encoded
    # None is refused too: the normalizer records an absence, never a null value.
    raise refuse(f"has type {type(value).__name__}, which JSON cannot round-trip")


def contribution_sha(contribution: LeadContribution) -> str:
    """The identity of an observation: its canonical JSON without fetch times."""
    content = json.loads(canonical_json(contribution))
    for record in content["provenance"]:
        record.pop("fetched_at", None)
    # Absences are a set (no column keeps their order): sort them.
    content["absences"] = sorted(content["absences"], key=canonical_value_json)
    return sha256(canonical_value_json(content).encode("utf-8")).hexdigest()


def write_contribution(
    session: Session,
    contribution: LeadContribution,
    *,
    source_run_id: uuid.UUID,
    raw_response_id: uuid.UUID,
    data_mode: DataMode,
    fetched_at: datetime,
    lead_scope: str,
) -> uuid.UUID:
    """Insert one contribution and its fields; returns the contribution id."""
    fetched = _as_utc(fetched_at)
    values = contribution.values
    provenance = {p.canonical_path: p for p in contribution.provenance}
    if set(provenance) != set(values) or len(provenance) != len(
        contribution.provenance
    ):
        raise ContributionValueError(
            "values and provenance must name the same canonical paths"
        )
    for record in provenance.values():
        if record.data_mode != data_mode or _as_utc(record.fetched_at) != fetched:
            raise ValueError(
                f"provenance of {record.canonical_path!r} disagrees with the "
                "contribution's data mode or fetch time"
            )
    encoded: dict[str, Any] = {}
    for path, value in values.items():
        wrapped = isinstance(value, UntrustedText)
        if wrapped != provenance[path].untrusted:
            raise ContributionValueError(
                f"untrusted marking of {path!r} must match whether its value is "
                "UntrustedText"
            )
        if not wrapped:
            encoded[path] = _encode(value, path)

    row = SourceContribution(
        source_run_id=source_run_id,
        raw_response_id=raw_response_id,
        source_name=contribution.source_name,
        data_mode=data_mode.value,
        fetched_at=fetched,
        lead_scope=lead_scope,
        content_sha=store_digest(session, contribution_sha(contribution)),
    )
    session.add(row)
    session.flush()
    for absence in contribution.absences:
        session.add(
            ContributionAbsence(
                contribution_id=row.id,
                canonical_path=absence.canonical_path,
                kind=absence.kind.value,
                raw_field_path=absence.raw_field_path,
            )
        )
    for path, value in values.items():
        stored: Any
        classification: tuple[bool, bool, int | None]
        if isinstance(value, UntrustedText):
            stored = value.value
            classification = (True, value.truncated, value.original_length)
        else:
            stored = encoded[path]
            classification = (False, False, None)
        session.add(
            ContributionField(
                contribution_id=row.id,
                canonical_path=path,
                value=stored,
                raw_field_path=provenance[path].raw_field_path,
                confidence=provenance[path].confidence,
                confidence_origin=provenance[path].confidence_origin.value,
                confidence_raw=provenance[path].confidence_raw,
                confidence_scale=provenance[path].confidence_scale,
                untrusted=classification[0],
                truncated=classification[1],
                original_length=classification[2],
            )
        )
    session.flush()
    return row.id


def stored_value(field: ContributionField) -> Any:
    """A stored field's value; an untrusted one returns as ``UntrustedText``."""

    def corrupt(why: str) -> StoredFieldError:
        return StoredFieldError(f"stored field {field.canonical_path!r}: {why}")

    if not field.untrusted:
        if field.truncated or field.original_length is not None:
            raise corrupt("trusted value carries untrusted-text metadata")
        return field.value
    if not isinstance(field.value, str):
        raise corrupt("untrusted value is not text")
    if field.truncated is None or field.original_length is None:
        raise corrupt("untrusted value lacks truncation metadata")
    try:
        return UntrustedText(
            value=field.value,
            truncated=field.truncated,
            original_length=field.original_length,
        )
    except ValueError:
        # Not chained: the validation error would quote the untrusted text.
        raise corrupt("inconsistent classification") from None


def read_contribution(
    session: Session, contribution_id: uuid.UUID
) -> StoredContribution:
    """Rebuild a stored contribution; untrusted fields return as ``UntrustedText``."""
    row = session.get(SourceContribution, contribution_id)
    if row is None:
        raise LookupError(f"no contribution {contribution_id}")
    fields = session.scalars(
        sa.select(ContributionField)
        .where(ContributionField.contribution_id == contribution_id)
        .order_by(ContributionField.canonical_path)
    )
    return StoredContribution(
        source_name=row.source_name,
        data_mode=DataMode(row.data_mode),
        fetched_at=aware_utc(row.fetched_at),
        values={f.canonical_path: stored_value(f) for f in fields},
        raw_response_id=row.raw_response_id,
    )


def _origin(field: ContributionField) -> ConfidenceOrigin:
    if field.confidence_origin is not None:
        return ConfidenceOrigin(field.confidence_origin)
    # Written before 0006: only the number was kept.
    if field.confidence is None:
        return ConfidenceOrigin.NONE
    return ConfidenceOrigin.HEURISTIC


def aware_utc(value: datetime) -> datetime:
    """A stored instant re-tagged UTC (SQLite returns it naive)."""
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(
        UTC
    )


def stored_provenance(
    field: ContributionField, contribution: SourceContribution
) -> FieldProvenance:
    """The provenance of one stored field of ``contribution``."""
    return FieldProvenance(
        canonical_path=field.canonical_path,
        source_name=contribution.source_name,
        data_mode=DataMode(contribution.data_mode),
        fetched_at=aware_utc(contribution.fetched_at),
        raw_field_path=field.raw_field_path,
        confidence_origin=_origin(field),
        untrusted=field.untrusted,
        confidence=field.confidence,
        confidence_raw=field.confidence_raw,
        confidence_scale=field.confidence_scale,
    )


def load_lead_contributions(session: Session) -> dict[uuid.UUID, LeadContribution]:
    """Every stored contribution rebuilt as a ``LeadContribution``, by row id.

    The input of a re-merge (8.12): the merge is a projection of the whole log.
    """
    fields: dict[uuid.UUID, list[ContributionField]] = defaultdict(list)
    for field in session.scalars(
        sa.select(ContributionField).order_by(ContributionField.canonical_path)
    ):
        fields[field.contribution_id].append(field)
    absences: dict[uuid.UUID, list[ContributionAbsence]] = defaultdict(list)
    for absence in session.scalars(
        sa.select(ContributionAbsence).order_by(
            ContributionAbsence.canonical_path, ContributionAbsence.kind
        )
    ):
        absences[absence.contribution_id].append(absence)

    out: dict[uuid.UUID, LeadContribution] = {}
    for row in session.scalars(sa.select(SourceContribution)):
        own = fields[row.id]
        out[row.id] = LeadContribution(
            source_name=row.source_name,
            values={f.canonical_path: stored_value(f) for f in own},
            provenance=tuple(stored_provenance(f, row) for f in own),
            absences=tuple(
                SourceAbsence(
                    canonical_path=a.canonical_path,
                    source_name=row.source_name,
                    kind=AbsenceKind(a.kind),
                    raw_field_path=a.raw_field_path,
                )
                for a in absences[row.id]
            ),
        )
    return out
