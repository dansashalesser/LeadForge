"""Persist and read primary-domain tie resolutions (task 16.11; Requirement 8.18).

``TieResolutionRepository`` satisfies ``tie_resolution.TieResolutionStore``. It takes
the caller's session, does not commit, and has no backend branch. The table is
append-only (the ORM guard of ``models`` refuses any update or delete), and ``put``
is first write wins: a key already stored returns the stored record untouched, so a
retry, or a writer that lost a race (the unique ``tie_key`` makes the database the
arbiter, checked inside a savepoint), adopts the answer that stands. Instants are
normalised to aware UTC before binding and re-tagged UTC on read, as ``raw_responses``
does.
"""

from datetime import UTC

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.store.models import PrimaryDomainTieResolution
from leadforge.lead_ingestion.tie_resolution import TieResolutionRecord

__all__ = ["TieResolutionRepository"]


class TieResolutionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str) -> TieResolutionRecord | None:
        row = self._session.scalar(
            sa.select(PrimaryDomainTieResolution).where(
                PrimaryDomainTieResolution.tie_key == key
            )
        )
        if row is None:
            return None
        return TieResolutionRecord(
            chosen_domain=row.chosen_domain,
            candidates=tuple(row.candidates),
            model=row.model,
            prompt_version=row.prompt_version,
            resolved_at=row.resolved_at.replace(tzinfo=UTC)
            if row.resolved_at.tzinfo is None
            else row.resolved_at.astimezone(UTC),
        )

    def put(self, key: str, record: TieResolutionRecord) -> TieResolutionRecord:
        """Store ``record`` unless ``key`` has one; return the record now stored."""
        existing = self.get(key)
        if existing is not None:
            return existing
        row = PrimaryDomainTieResolution(
            tie_key=key,
            chosen_domain=record.chosen_domain,
            candidates=list(record.candidates),
            model=record.model,
            prompt_version=record.prompt_version,
            resolved_at=record.resolved_at.astimezone(UTC),
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError:
            self._session.expire_all()
            stored = self.get(key)
            if stored is None:
                raise
            return stored
        return record
