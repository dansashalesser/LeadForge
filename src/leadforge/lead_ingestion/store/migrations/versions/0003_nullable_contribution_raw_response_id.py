"""source_contribution.raw_response_id becomes nullable, ON DELETE SET NULL

Raw provider payloads expire after 30 days in live mode (9.8) while the contribution
log is append-only (8.12). Making the link nullable lets the database detach a
contribution when its expired payload is deleted, so the purge needs no UPDATE of a
contribution row (which the ORM guard forbids).

The change uses batch mode, a plain ``ALTER`` where the engine supports it and a table
rebuild where it does not (SQLite), so the revision carries no backend branch. The
original foreign key was created unnamed; the naming convention below gives it the name
PostgreSQL assigns by default (``<table>_<column>_fkey``), which SQLite reflection then
reproduces, so the same drop works on both. Only SQLite was exercised when this
revision was written; PostgreSQL is covered by task 6.7.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "source_contribution_raw_response_id_fkey"
_NAMING = {"fk": "%(table_name)s_%(column_0_name)s_fkey"}


def upgrade() -> None:
    with op.batch_alter_table(
        "source_contribution", naming_convention=_NAMING
    ) as batch:
        batch.drop_constraint(_FK_NAME, type_="foreignkey")
        batch.alter_column(
            "raw_response_id",
            existing_type=sa.Uuid(),
            existing_nullable=False,
            nullable=True,
        )
        batch.create_foreign_key(
            _FK_NAME,
            "raw_response",
            ["raw_response_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Fails if a contribution already lost its raw payload (NULL link): re-attaching
    # a payload that no longer exists is impossible, and inventing one would be worse.
    with op.batch_alter_table(
        "source_contribution", naming_convention=_NAMING
    ) as batch:
        batch.drop_constraint(_FK_NAME, type_="foreignkey")
        batch.alter_column(
            "raw_response_id",
            existing_type=sa.Uuid(),
            existing_nullable=True,
            nullable=False,
        )
        batch.create_foreign_key(_FK_NAME, "raw_response", ["raw_response_id"], ["id"])
