"""usage_evidence, usage_classification_cache, usage_company_grades

The user-recognition slice (Req 5.8) stores each classified Evidence Record, a cache of
classifier judgements keyed by input hash (the same input never calls the LLM twice) and
the Company Usage grade per search, company and product.

``usage_evidence`` is append-only; that rule is enforced by the ORM guard in
``leadforge.outreach.usage.store``. Engine neutral: Core constructs only. Downgrade
drops the three tables.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("search_id", sa.Uuid(), nullable=False),
        sa.Column("company_key", sa.String(length=255), nullable=False),
        sa.Column("product_key", sa.String(length=128), nullable=False),
        sa.Column("evidence_class", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("observed_on", sa.String(length=10), nullable=True),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("relationship", sa.String(length=32), nullable=False),
        sa.Column("confidence_milli", sa.Integer(), nullable=False),
        sa.Column("snippet_only", sa.Boolean(), nullable=False),
        sa.Column("classifier_kind", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "confidence_milli >= 0 AND confidence_milli <= 1000",
            name="ck_usage_evidence_confidence",
        ),
        sa.ForeignKeyConstraint(["search_id"], ["outreach_search.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_usage_evidence_search_company",
        "usage_evidence",
        ["search_id", "company_key"],
    )
    op.create_table(
        "usage_classification_cache",
        sa.Column("input_hash", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("judgement_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("input_hash"),
    )
    op.create_table(
        "usage_company_grades",
        sa.Column("search_id", sa.Uuid(), nullable=False),
        sa.Column("company_key", sa.String(length=255), nullable=False),
        sa.Column("product_key", sa.String(length=128), nullable=False),
        sa.Column("grade", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("record_ids_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["search_id"], ["outreach_search.id"]),
        sa.PrimaryKeyConstraint("search_id", "company_key", "product_key"),
    )


def downgrade() -> None:
    op.drop_table("usage_company_grades")
    op.drop_table("usage_classification_cache")
    op.drop_table("usage_evidence")
