"""contribution_field.confidence becomes nullable

A provider that states no certainty has no Field Confidence (``confidence_origin``
``none``); storing a default number there would invent one. The change uses batch mode,
which is a plain ``ALTER`` where the engine supports it and a table rebuild where it
does not, so the revision carries no backend branch.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("contribution_field") as batch:
        batch.alter_column(
            "confidence",
            existing_type=sa.Float(),
            existing_nullable=False,
            nullable=True,
        )


def downgrade() -> None:
    # Fails if a row with no confidence exists; inventing a number would be worse.
    with op.batch_alter_table("contribution_field") as batch:
        batch.alter_column(
            "confidence",
            existing_type=sa.Float(),
            existing_nullable=True,
            nullable=False,
        )
