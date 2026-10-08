"""Usage evidence, classification cache and company grades (Req 5.8).

Tables of migration ``0013`` on the Lead Store's ``Base``. ``usage_evidence`` is
append-only (an ORM update or delete raises ``AppendOnlyViolationError``, as for the
outreach Messages) and the repository exposes insert and read only. The cache is
keyed by the classifier's input hash: the same hash is answered from here, with no LLM call.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    event,
    select,
)
from sqlalchemy.orm import Mapped, Mapper, ORMExecuteState, Session, mapped_column

from leadforge.lead_ingestion.store.models import AppendOnlyViolationError, Base
from leadforge.outreach.usage.records import (
    ClassifierStamp,
    EvidenceClass,
    EvidenceRecord,
    Relationship,
)

__all__ = [
    "USAGE_TABLES",
    "StoredEvidence",
    "StoredGrade",
    "UsageClassificationCache",
    "UsageCompanyGrade",
    "UsageEvidence",
    "UsageStore",
    "UsageStoreError",
]


class UsageStoreError(ValueError):
    """A usage-store call was made with an unusable argument."""


class UsageEvidence(Base):
    __tablename__ = "usage_evidence"
    __table_args__ = (
        CheckConstraint(
            "confidence_milli >= 0 AND confidence_milli <= 1000",
            name="ck_usage_evidence_confidence",
        ),
        Index("ix_usage_evidence_search_company", "search_id", "company_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    search_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outreach_search.id"))
    company_key: Mapped[str] = mapped_column(String(255))
    product_key: Mapped[str] = mapped_column(String(128))
    evidence_class: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(2048))
    # ISO ``YYYY-MM-DD``: the store allows only the portable column types (no Date).
    observed_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    quote: Mapped[str] = mapped_column(Text)
    relationship: Mapped[str] = mapped_column(String(32))
    # Confidence in thousandths (0..1000): an exact integer on every engine.
    confidence_milli: Mapped[int] = mapped_column(Integer)
    snippet_only: Mapped[bool] = mapped_column(Boolean)
    classifier_kind: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UsageClassificationCache(Base):
    __tablename__ = "usage_classification_cache"

    input_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    prompt_version: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    judgement_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UsageCompanyGrade(Base):
    __tablename__ = "usage_company_grades"

    search_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("outreach_search.id"), primary_key=True
    )
    company_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    product_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    grade: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    record_ids_json: Mapped[list[str]] = mapped_column(JSON)


USAGE_TABLES = frozenset(
    {
        UsageEvidence.__tablename__,
        UsageClassificationCache.__tablename__,
        UsageCompanyGrade.__tablename__,
    }
)


def _refuse_row_change(mapper: Mapper[Any], connection: Any, target: Any) -> None:
    raise AppendOnlyViolationError("UsageEvidence is append-only: no update or delete")


def _refuse_bulk_change(state: ORMExecuteState) -> None:
    if not (state.is_update or state.is_delete):
        return
    name = getattr(getattr(state.statement, "table", None), "name", None)
    if name == UsageEvidence.__tablename__:
        raise AppendOnlyViolationError(
            "usage_evidence is append-only: no bulk update or delete"
        )


event.listen(UsageEvidence, "before_update", _refuse_row_change)
event.listen(UsageEvidence, "before_delete", _refuse_row_change)
event.listen(Session, "do_orm_execute", _refuse_bulk_change)


@dataclass(frozen=True)
class StoredEvidence:
    id: uuid.UUID
    search_id: uuid.UUID
    record: EvidenceRecord


@dataclass(frozen=True)
class StoredGrade:
    grade: str
    reason: str
    record_ids: list[str]


def _to_row(search_id: uuid.UUID, rec: EvidenceRecord) -> UsageEvidence:
    return UsageEvidence(
        search_id=search_id,
        company_key=rec.company_key,
        product_key=rec.product_key,
        evidence_class=rec.evidence_class.value,
        source=rec.source,
        url=rec.url,
        observed_on=rec.observed_on.isoformat() if rec.observed_on else None,
        quote=rec.quote,
        relationship=rec.relationship.value,
        confidence_milli=int(rec.confidence * 1000),
        snippet_only=rec.snippet_only,
        classifier_kind=rec.classifier.kind,
        model=rec.classifier.model,
        prompt_version=rec.classifier.prompt_version,
        input_hash=rec.classifier.input_hash,
        created_at=datetime.now(UTC),
    )


def _to_record(row: UsageEvidence) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "company_key": row.company_key,
            "product_key": row.product_key,
            "evidence_class": EvidenceClass(row.evidence_class),
            "source": row.source,
            "url": row.url,
            "observed_on": date.fromisoformat(row.observed_on)
            if row.observed_on
            else None,
            "quote": row.quote,
            "relationship": Relationship(row.relationship),
            "confidence": Decimal(row.confidence_milli) / 1000,
            "snippet_only": row.snippet_only,
            "classifier": ClassifierStamp(
                kind=row.classifier_kind,  # type: ignore[arg-type]
                model=row.model,
                prompt_version=row.prompt_version,
                input_hash=row.input_hash,
            ),
        }
    )


def _need_hash(input_hash: str) -> None:
    if not input_hash:
        raise UsageStoreError("input_hash must not be empty")


class UsageStore:
    """Insert-and-read access to the usage tables; each call is its own transaction."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append_evidence(
        self, search_id: uuid.UUID, record: EvidenceRecord
    ) -> uuid.UUID:
        row = _to_row(search_id, record)
        with Session(self._engine) as s:
            s.add(row)
            s.commit()
            return row.id

    def list_evidence(
        self, search_id: uuid.UUID, company_key: str | None = None
    ) -> Sequence[StoredEvidence]:
        stmt = select(UsageEvidence).where(UsageEvidence.search_id == search_id)
        if company_key is not None:
            stmt = stmt.where(UsageEvidence.company_key == company_key)
        stmt = stmt.order_by(UsageEvidence.created_at, UsageEvidence.id)
        with Session(self._engine) as s:
            return [
                StoredEvidence(r.id, r.search_id, _to_record(r))
                for r in s.scalars(stmt)
            ]

    def cache_get(self, input_hash: str) -> dict[str, Any] | None:
        _need_hash(input_hash)
        with Session(self._engine) as s:
            row = s.get(UsageClassificationCache, input_hash)
            return None if row is None else dict(row.judgement_json)

    def cache_put(
        self,
        input_hash: str,
        judgement: dict[str, Any],
        *,
        model: str | None,
        prompt_version: str,
    ) -> None:
        """Store a judgement; the first answer for a hash is kept."""
        _need_hash(input_hash)
        with Session(self._engine) as s:
            if s.get(UsageClassificationCache, input_hash) is None:
                s.add(
                    UsageClassificationCache(
                        input_hash=input_hash,
                        prompt_version=prompt_version,
                        model=model,
                        judgement_json=judgement,
                        created_at=datetime.now(UTC),
                    )
                )
                s.commit()

    def upsert_company_grade(
        self,
        search_id: uuid.UUID,
        company_key: str,
        product_key: str,
        grade: str,
        reason: str,
        record_ids: Sequence[str],
    ) -> None:
        with Session(self._engine) as s:
            row = s.get(UsageCompanyGrade, (search_id, company_key, product_key))
            if row is None:
                row = UsageCompanyGrade(
                    search_id=search_id,
                    company_key=company_key,
                    product_key=product_key,
                )
                s.add(row)
            row.grade = grade
            row.reason = reason
            row.record_ids_json = list(record_ids)
            s.commit()

    def get_company_grade(
        self, search_id: uuid.UUID, company_key: str, product_key: str
    ) -> StoredGrade | None:
        with Session(self._engine) as s:
            row = s.get(UsageCompanyGrade, (search_id, company_key, product_key))
            if row is None:
                return None
            return StoredGrade(row.grade, row.reason, list(row.record_ids_json))
