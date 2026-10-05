"""primary_domain_tie_resolution: the persisted answer to an exact primary-domain tie

Requirement 8.18: an exact tie in the primary-domain vote may be resolved by a language
model constrained to the candidate domains, and the projection reads the stored record
instead of calling the model. One row per tie (``tie_key`` is unique), carrying the
chosen domain, the candidate set, the model, the prompt version and a timestamp. A new
table only, so the revision carries no backend branch and touches no existing row.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "primary_domain_tie_resolution",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tie_key", sa.String(length=64), nullable=False),
        sa.Column("chosen_domain", sa.String(length=255), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tie_key"),
    )


def downgrade() -> None:
    op.drop_table("primary_domain_tie_resolution")
