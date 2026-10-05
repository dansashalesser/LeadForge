"""Write and read a ``LeadContribution`` with its untrusted classification (task 6.6).

``contribution_field.value`` is a JSON column, and the classification is kept out of it
on purpose: the ``untrusted``, ``truncated`` and ``original_length`` columns carry it,
and ``value`` holds only the text, verbatim. Reading rebuilds an ``UntrustedText`` only
from those columns, so no provider string (even one that looks like a serialized
marker) can make a trusted value read back as classified, and an untrusted one cannot
read back as trusted unless its row was altered out of band, which the append-only
guard (8.12) forbids at the ORM level.

JSON cannot round-trip a tuple (it returns a list), a datetime, a set, bytes, a
non-finite float or a non-string key. Rather than change a value silently, the write
path refuses them with `ContributionValueError`, which names the canonical path and
the offending type but never the value.

Time: instants are normalised to aware UTC before binding and re-tagged UTC on read,
with no backend branch (the same approach as ``raw_responses``).

`write_contribution` is synchronous, takes the session handed to a
``StoreWriter.write_batch`` callable, and returns a plain ``uuid.UUID``; it does not
commit. Provenance beyond ``confidence`` (origin, raw confidence, scale, superseded)
and absences have no columns in the 0001 schema and are not persisted here.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.models import DataMode, UntrustedText
from leadforge.lead_ingestion.store.models import ContributionField, SourceContribution

__all__ = [
    "ContributionValueError",
    "StoredContribution",
    "StoredFieldError",
    "read_contribution",
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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must carry a timezone (naive datetimes are refused)")
    return value.astimezone(UTC)


def _check_json_faithful(value: object, path: str, depth: int = 0) -> None:
    def refuse(why: str) -> ContributionValueError:
        return ContributionValueError(f"value of {path!r} {why}")

    if depth > _MAX_DEPTH:
        raise refuse("is nested too deeply")
    if isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise refuse("is a non-finite float")
        return
    if isinstance(value, list):
        for item in value:
            _check_json_faithful(item, path, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise refuse("has a non-string mapping key")
            _check_json_faithful(item, path, depth + 1)
        return
    # None is refused too: the normalizer records an absence, never a null value.
    raise refuse(f"has type {type(value).__name__}, which JSON cannot round-trip")


def write_contribution(
    session: Session,
    contribution: LeadContribution,
    *,
    source_run_id: uuid.UUID,
    raw_response_id: uuid.UUID,
    data_mode: DataMode,
    fetched_at: datetime,
    lead_scope: str,
    lead_identity_id: uuid.UUID | None = None,
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
    for path, value in values.items():
        wrapped = isinstance(value, UntrustedText)
        if wrapped != provenance[path].untrusted:
            raise ContributionValueError(
                f"untrusted marking of {path!r} must match whether its value is "
                "UntrustedText"
            )
        if not wrapped:
            _check_json_faithful(value, path)

    row = SourceContribution(
        source_run_id=source_run_id,
        lead_identity_id=lead_identity_id,
        raw_response_id=raw_response_id,
        source_name=contribution.source_name,
        data_mode=data_mode.value,
        fetched_at=fetched,
        lead_scope=lead_scope,
    )
    session.add(row)
    session.flush()
    for path, value in values.items():
        stored: Any
        classification: tuple[bool, bool, int | None]
        if isinstance(value, UntrustedText):
            stored = value.value
            classification = (True, value.truncated, value.original_length)
        else:
            stored = value
            classification = (False, False, None)
        session.add(
            ContributionField(
                contribution_id=row.id,
                canonical_path=path,
                value=stored,
                raw_field_path=provenance[path].raw_field_path,
                confidence=provenance[path].confidence,
                untrusted=classification[0],
                truncated=classification[1],
                original_length=classification[2],
            )
        )
    session.flush()
    return row.id


def _rebuild(field: ContributionField) -> Any:
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
    fetched = row.fetched_at
    fetched = fetched.replace(tzinfo=UTC) if fetched.tzinfo is None else fetched
    return StoredContribution(
        source_name=row.source_name,
        data_mode=DataMode(row.data_mode),
        fetched_at=fetched.astimezone(UTC),
        values={f.canonical_path: _rebuild(f) for f in fields},
    )
