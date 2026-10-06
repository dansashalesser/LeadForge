"""ingestion_run failure reason and projection stamp; per-source call/record counts

Follow-up (2026-10-06) to Requirements 21.1 and 21.2. An aborted run keeps why it ended
(a stage and an exception class, never a value), and each ``source_run`` row keeps the
source's attempted, succeeded and failed calls and the provider records it fetched.
Each run also keeps the projection stamp it wrote its canonical leads under (version
and keyed basis fingerprint, 8.13; the next run's stamp is derived from the latest
one) and the count of flagged primary-domain tie fallbacks (8.18).
All four count columns and the reason are nullable: a row written before this revision,
or by a run that never completed, has no figure, and NULL says "not recorded" rather
than an invented 0. Added columns only, so the revision carries no backend branch;
the downgrade uses batch mode so SQLite drops them by a table rebuild.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COUNTS = ("attempted", "succeeded", "failed", "records_fetched")
_RUN_COLUMNS = (
    ("failure_reason", sa.String(length=255)),
    ("projection_version", sa.Integer()),
    ("projection_fingerprint", sa.String(length=64)),
    ("primary_domain_ties_flagged", sa.Integer()),
)


def upgrade() -> None:
    for name, type_ in _RUN_COLUMNS:
        op.add_column("ingestion_run", sa.Column(name, type_, nullable=True))
    for name in _COUNTS:
        op.add_column("source_run", sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source_run") as batch:
        for name in reversed(_COUNTS):
            batch.drop_column(name)
    with op.batch_alter_table("ingestion_run") as batch:
        for name, _ in reversed(_RUN_COLUMNS):
            batch.drop_column(name)
