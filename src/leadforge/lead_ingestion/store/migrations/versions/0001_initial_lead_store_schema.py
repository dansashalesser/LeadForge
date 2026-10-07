"""initial lead store schema

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("pool_size", sa.Integer(), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "lead_identity",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("primary_key_type", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "canonical_lead",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_identity_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("email_status", sa.String(length=32), nullable=True),
        sa.Column("linkedin_url", sa.String(length=2048), nullable=True),
        sa.Column("full_name", sa.String(length=512), nullable=True),
        sa.Column("employments", sa.JSON(), nullable=True),
        sa.Column("tech_signals", sa.JSON(), nullable=True),
        sa.Column("intent_signals", sa.JSON(), nullable=True),
        sa.Column("opt_out", sa.Boolean(), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False),
        sa.Column("contributing_sources", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projection_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_identity_id"],
            ["lead_identity.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_identity_id"),
    )
    op.create_table(
        "identity_key",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_identity_id", sa.Uuid(), nullable=False),
        sa.Column("key_type", sa.String(length=32), nullable=False),
        sa.Column("key_value", sa.String(length=512), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_identity_id"],
            ["lead_identity.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_type", "key_value", name="uq_identity_key_type_value"),
    )
    op.create_table(
        "source_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("resolved_mode", sa.String(length=16), nullable=False),
        sa.Column("mode_reason", sa.String(length=255), nullable=True),
        sa.Column("live_access", sa.Boolean(), nullable=True),
        sa.Column("credential_present", sa.Boolean(), nullable=True),
        sa.Column("leads_found", sa.Integer(), nullable=False),
        sa.Column("contributions_written", sa.Integer(), nullable=False),
        sa.Column("failure_class", sa.String(length=128), nullable=True),
        sa.Column("throttle_waits", sa.Integer(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("http_429_count", sa.Integer(), nullable=False),
        sa.Column("credits_consumed", sa.Integer(), nullable=True),
        sa.Column("quota_remaining", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ingestion_run.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_run_run_id_source_name",
        "source_run",
        ["run_id", "source_name"],
        unique=False,
    )
    op.create_table(
        "raw_response",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["source_run.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_raw_response_retention_until",
        "raw_response",
        ["retention_until"],
        unique=False,
    )
    op.create_table(
        "source_contribution",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("lead_identity_id", sa.Uuid(), nullable=True),
        sa.Column("raw_response_id", sa.Uuid(), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_scope", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_identity_id"],
            ["lead_identity.id"],
        ),
        sa.ForeignKeyConstraint(
            ["raw_response_id"],
            ["raw_response.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["source_run.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_contribution_lead_identity_id",
        "source_contribution",
        ["lead_identity_id"],
        unique=False,
    )
    op.create_table(
        "contribution_field",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contribution_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_path", sa.String(length=255), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("raw_field_path", sa.String(length=512), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("untrusted", sa.Boolean(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("original_length", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["contribution_id"],
            ["source_contribution.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_contribution_field_contribution_id_path",
        "contribution_field",
        ["contribution_id", "canonical_path"],
        unique=False,
    )
    op.create_table(
        "canonical_field_provenance",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("canonical_lead_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_path", sa.String(length=255), nullable=False),
        sa.Column("winning_field_id", sa.Uuid(), nullable=False),
        sa.Column("agreeing_source_count", sa.Integer(), nullable=False),
        sa.Column("superseded_field_ids", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["canonical_lead_id"],
            ["canonical_lead.id"],
        ),
        sa.ForeignKeyConstraint(
            ["winning_field_id"],
            ["contribution_field.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("canonical_field_provenance")
    op.drop_index(
        "ix_contribution_field_contribution_id_path", table_name="contribution_field"
    )
    op.drop_table("contribution_field")
    op.drop_index(
        "ix_source_contribution_lead_identity_id", table_name="source_contribution"
    )
    op.drop_table("source_contribution")
    op.drop_index("ix_raw_response_retention_until", table_name="raw_response")
    op.drop_table("raw_response")
    op.drop_index("ix_source_run_run_id_source_name", table_name="source_run")
    op.drop_table("source_run")
    op.drop_table("identity_key")
    op.drop_table("canonical_lead")
    op.drop_table("lead_identity")
    op.drop_table("ingestion_run")
