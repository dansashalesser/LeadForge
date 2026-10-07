"""lead_match_key index, its store key, canonical_field_provenance.agreeing_field_ids

Follow-up (user request 2026-10-06: load a lead back from the store):

* ``lead_match_key`` maps (kind, keyed digest) to an active lead, so ``find_lead``
  reads an index instead of every lead. A digest is HMAC-SHA256 under a new store key
  (``store_secret`` ``match_key_index``, 32 random bytes written here), never a plain
  value or a plain hash. The backfill indexes every active canonical lead through
  ``store.match_key_index.index_rows``, the code the app writes the index with, so a
  backfilled digest is the digest a lookup computes (same normalization, same key).
  Rejected: a frozen copy of the normalizers here; the index is derived data that must
  match the running code, and a second copy is one that drifts.
* ``canonical_field_provenance.agreeing_field_ids`` (nullable JSON) keeps the
  agreeing candidates' field ids, so a lead loads back with the projection's whole
  provenance. Existing rows stay NULL: which sources agreed was never recorded, and
  only a re-projection can tell; the count (``agreeing_source_count``) is unchanged.

Engine neutral: Core constructs only, ``batch_alter_table`` for the column drop
(SQLite). Downgrade drops the table, the column and the key; nothing else moves.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-06
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from leadforge.lead_ingestion.match_key_digest import MatchKeyDigester
from leadforge.lead_ingestion.store.match_key_index import index_rows
from leadforge.lead_ingestion.store.store_key import INDEX_KEY_NAME

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "lead_match_key"
_PROVENANCE = "canonical_field_provenance"
_AGREEING = "agreeing_field_ids"


def upgrade() -> None:
    op.add_column(_PROVENANCE, sa.Column(_AGREEING, sa.JSON(), nullable=True))
    index = op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_identity_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["lead_identity_id"], ["lead_identity.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lead_identity_id", "kind", name="uq_lead_match_key_lead_kind"
        ),
    )
    op.create_index(
        "ix_lead_match_key_kind_digest", _TABLE, ["kind", "digest"], unique=False
    )
    key = secrets.token_bytes(32)
    secret = sa.table("store_secret", sa.column("name"), sa.column("value"))
    op.bulk_insert(secret, [{"name": INDEX_KEY_NAME, "value": key.hex()}])

    lead = sa.table(
        "canonical_lead",
        sa.column("lead_identity_id", sa.Uuid()),
        sa.column("email"),
        sa.column("linkedin_url"),
    )
    identity = sa.table(
        "lead_identity", sa.column("id", sa.Uuid()), sa.column("retired_at")
    )
    digester = MatchKeyDigester(key, comparable_across_runs=True)
    rows = [
        row
        for lead_id, email, url in op.get_bind().execute(
            sa.select(lead.c.lead_identity_id, lead.c.email, lead.c.linkedin_url)
            .join(identity, identity.c.id == lead.c.lead_identity_id)
            .where(identity.c.retired_at.is_(None))
        )
        for row in index_rows(digester, lead_id, email, url)
    ]
    if rows:
        op.bulk_insert(index, rows)


def downgrade() -> None:
    secret = sa.table("store_secret", sa.column("name"))
    op.execute(sa.delete(secret).where(secret.c.name == INDEX_KEY_NAME))
    op.drop_index("ix_lead_match_key_kind_digest", table_name=_TABLE)
    op.drop_table(_TABLE)
    with op.batch_alter_table(_PROVENANCE) as batch:
        batch.drop_column(_AGREEING)
