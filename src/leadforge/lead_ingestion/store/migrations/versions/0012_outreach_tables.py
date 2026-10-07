"""outreach_search, outreach_decision, outreach_message, outreach_trigger_event

The outreach slice (specs: outreach design, Data Models) keeps its searches, the
Decision made for each Lead, the Messages written for it and the trigger events of its
sequence in four new tables. They key off existing tables only (``ingestion_run`` and
``lead_identity``) and copy no provider field.

* ``outreach_search`` and ``outreach_decision`` are plain rows (a search's status moves
  as it runs; a Decision becomes ``manual_review`` when its Messages fail validation).
* ``outreach_message`` and ``outreach_trigger_event`` are append-only; that rule is
  enforced by the ORM guard in ``leadforge.outreach.tables``. The database itself
  refuses any Message ``state`` but ``dry_run`` (a CHECK constraint): nothing here is
  ever delivered.

Engine neutral: Core constructs only. Downgrade drops the four tables (children first).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outreach_search",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("query", sa.String(length=2048), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("compiler", sa.String(length=16), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outreach_search_ingestion_run_id", "outreach_search", ["ingestion_run_id"]
    )
    op.create_table(
        "outreach_decision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("search_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("score_milli", sa.Integer(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('selected', 'rejected', 'needs_enrichment', 'manual_review')",
            name="ck_outreach_decision_status",
        ),
        sa.ForeignKeyConstraint(["search_id"], ["outreach_search.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["lead_identity.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("search_id", "lead_id", name="uq_outreach_decision_lead"),
    )
    op.create_index("ix_outreach_decision_lead_id", "outreach_decision", ["lead_id"])
    op.create_table(
        "outreach_message",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("variant", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("generator", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("judge", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state = 'dry_run'", name="ck_outreach_message_state"),
        sa.CheckConstraint(
            "channel IN ('linkedin', 'email')", name="ck_outreach_message_channel"
        ),
        sa.ForeignKeyConstraint(["decision_id"], ["outreach_decision.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outreach_message_decision_id", "outreach_message", ["decision_id"]
    )
    op.create_table(
        "outreach_trigger_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('invite', 'accepted', 'email', 'fallback_email', 'stalled',"
            " 'halted')",
            name="ck_outreach_trigger_event_kind",
        ),
        sa.ForeignKeyConstraint(["decision_id"], ["outreach_decision.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outreach_trigger_event_decision_id",
        "outreach_trigger_event",
        ["decision_id"],
    )


def downgrade() -> None:
    op.drop_table("outreach_trigger_event")
    op.drop_table("outreach_message")
    op.drop_table("outreach_decision")
    op.drop_table("outreach_search")
