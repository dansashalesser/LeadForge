"""contribution_lead mapping, lead retirement, stored provenance, primary-domain columns

Follow-up (user option A, 2026-10-06) to Requirements 8.12, 8.13 and 8.18. Which lead
a contribution belongs to becomes a DERIVED mapping (``contribution_lead``) that every
merge rebuilds, so a cluster that splits or joins re-saves with no contribution
relinked; the append-only log is untouched. A lead a merge no longer produces is
retired (``lead_identity.retired_at``) with ``lead_succession`` rows naming what
replaced it. ``source_contribution.content_sha`` identifies an observation so it is
stored once; ``contribution_field`` gains the rest of a field's provenance and
``contribution_absence`` keeps absences, so a stored contribution reads back whole for
a re-merge. ``canonical_lead`` keeps its display primary domain, how it was decided and
the keyed basis fingerprint it was projected under; ``ingestion_run`` keeps how many
leads its merge wrote and retired.

Every added column is nullable (a row written before this revision has no value; NULL
says "not recorded"). The upgrade backfills the mapping from each contribution's
first-seen ``lead_identity_id`` with a Core INSERT ... SELECT, so the revision carries
no raw SQL and no backend branch; the downgrade drops only what it added (batch mode,
so SQLite rebuilds the tables) and keeps every existing row.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADDED: tuple[tuple[str, tuple[tuple[str, sa.types.TypeEngine[Any]], ...]], ...] = (
    ("lead_identity", (("retired_at", sa.DateTime(timezone=True)),)),
    (
        "contribution_field",
        (
            ("confidence_origin", sa.String(length=32)),
            ("confidence_raw", sa.String(length=255)),
            ("confidence_scale", sa.String(length=255)),
        ),
    ),
    (
        "canonical_lead",
        (
            ("primary_domain", sa.String(length=255)),
            ("primary_domain_source", sa.String(length=32)),
            ("projection_fingerprint", sa.String(length=64)),
        ),
    ),
    (
        "ingestion_run",
        (("leads_merged", sa.Integer()), ("leads_retired", sa.Integer())),
    ),
)


def upgrade() -> None:
    for table, columns in _ADDED:
        for name, type_ in columns:
            op.add_column(table, sa.Column(name, type_, nullable=True))
    with op.batch_alter_table("source_contribution") as batch:
        batch.add_column(sa.Column("content_sha", sa.String(length=64), nullable=True))
        batch.create_unique_constraint(
            "uq_source_contribution_content_sha", ["content_sha"]
        )

    op.create_table(
        "contribution_absence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contribution_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_path", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("raw_field_path", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(["contribution_id"], ["source_contribution.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_contribution_absence_contribution_id",
        "contribution_absence",
        ["contribution_id"],
    )
    op.create_table(
        "contribution_lead",
        sa.Column("contribution_id", sa.Uuid(), nullable=False),
        sa.Column("lead_identity_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["contribution_id"], ["source_contribution.id"]),
        sa.ForeignKeyConstraint(["lead_identity_id"], ["lead_identity.id"]),
        sa.PrimaryKeyConstraint("contribution_id"),
    )
    op.create_index(
        "ix_contribution_lead_lead_identity_id",
        "contribution_lead",
        ["lead_identity_id"],
    )
    op.create_table(
        "lead_succession",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("predecessor_id", sa.Uuid(), nullable=False),
        sa.Column("successor_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["predecessor_id"], ["lead_identity.id"]),
        sa.ForeignKeyConstraint(["successor_id"], ["lead_identity.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "predecessor_id", "successor_id", name="uq_lead_succession_pair"
        ),
    )

    contribution = sa.table(
        "source_contribution", sa.column("id"), sa.column("lead_identity_id")
    )
    mapping = sa.table(
        "contribution_lead", sa.column("contribution_id"), sa.column("lead_identity_id")
    )
    op.execute(
        sa.insert(mapping).from_select(
            ["contribution_id", "lead_identity_id"],
            sa.select(contribution.c.id, contribution.c.lead_identity_id).where(
                contribution.c.lead_identity_id.is_not(None)
            ),
        )
    )


def downgrade() -> None:
    op.drop_table("lead_succession")
    op.drop_index("ix_contribution_lead_lead_identity_id", "contribution_lead")
    op.drop_table("contribution_lead")
    op.drop_index("ix_contribution_absence_contribution_id", "contribution_absence")
    op.drop_table("contribution_absence")
    with op.batch_alter_table("source_contribution") as batch:
        batch.drop_constraint("uq_source_contribution_content_sha", type_="unique")
        batch.drop_column("content_sha")
    for table, columns in reversed(_ADDED):
        with op.batch_alter_table(table) as batch:
            for name, _ in reversed(columns):
                batch.drop_column(name)
