"""run lock, store key, keyed digests, lead-owned company ids, re-projection columns

Follow-up (2026-10-06) to Requirements 6.4, 8.12, 10.5 and 21.5:

* ``run_lock`` holds the one-run-at-a-time lock; its one row (``ingestion``) is
  seeded free, so acquiring is one conditional UPDATE on every engine.
* ``store_secret`` holds the store's own random 32-byte HMAC key (hex), written once
  here. Every existing ``source_contribution.content_sha`` (plain sha256 hex) is
  rewritten to HMAC-SHA256(key, old hex): exactly what ``store.store_key`` computes
  for the same content, so a store written before this revision re-ingests a record
  as the same contribution.
* Every ``raw_response.request_fingerprint`` (a plain sha256 of the payload, looked
  up by nothing) becomes an opaque random value, as the app now writes it.
* ``source_contribution.lead_identity_id`` (the first-seen lead) is dropped, with its
  foreign key and index: contributions are stored before any lead exists, so the
  app no longer writes it, and nothing reads it; ``contribution_lead`` (0006) is the
  one mapping. The foreign key was created unnamed; it is dropped by the name
  PostgreSQL assigns (``<table>_<column>_fkey``), as 0003 does.
* A canonical lead's domainless company (``employments[].company.domains == []``)
  had an id derived from a plain sha256 of a contribution; it becomes
  ``co-<lead_identity_id hex>``, the lead's persisted uuid.
* ``ingestion_run`` gains ``clusters_reprojected`` and ``reprojection``.

The rewrites read and update rows through Core constructs (no raw SQL, no backend
branch). Downgrade drops what was added and sets ``content_sha`` back to NULL: the
0006 code recomputes a NULL identity from the row (its legacy path) instead of
mistaking a keyed digest for a different record. It restores
``source_contribution.lead_identity_id`` filled from ``contribution_lead`` (the
current lead: the first-seen one is not recoverable, and 0006 reads the column only
to seed that mapping). Random fingerprints and lead-owned company ids are kept (the
plain values cannot be recovered and nothing looks them up).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06
"""

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY_NAME = "content_digest"
_LOCK_NAME = "ingestion"
_NAMING = {"fk": "%(table_name)s_%(column_0_name)s_fkey"}
_LEAD_FK = "source_contribution_lead_identity_id_fkey"
_LEAD_INDEX = "ix_source_contribution_lead_identity_id"


def _keyed(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def _rewrite(
    conn: sa.Connection, table: str, column: str, new: Callable[[str], str]
) -> None:
    t = sa.table(table, sa.column("id"), sa.column(column))
    rows = conn.execute(
        sa.select(t.c.id, t.c[column]).where(t.c[column].is_not(None))
    ).all()
    for row_id, value in rows:
        conn.execute(sa.update(t).where(t.c.id == row_id).values({column: new(value)}))


def _lead_owned_companies(conn: sa.Connection) -> None:
    t = sa.table(
        "canonical_lead",
        sa.column("id"),
        sa.column("lead_identity_id"),
        sa.column("employments", sa.JSON()),
    )
    for row_id, lead, employments in conn.execute(
        sa.select(t.c.id, t.c.lead_identity_id, t.c.employments)
    ).all():
        if not employments:
            continue
        rewritten: list[Any] = []
        for employment in employments:
            company = employment.get("company")
            if isinstance(company, dict) and not company.get("domains"):
                company = {**company, "company_id": f"co-{_hex(lead)}"}
                employment = {**employment, "company": company}
            rewritten.append(employment)
        if rewritten != employments:
            conn.execute(
                sa.update(t).where(t.c.id == row_id).values(employments=rewritten)
            )


def _hex(value: Any) -> str:
    return (value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))).hex


def upgrade() -> None:
    op.add_column(
        "ingestion_run", sa.Column("clusters_reprojected", sa.Integer(), nullable=True)
    )
    op.add_column(
        "ingestion_run", sa.Column("reprojection", sa.String(length=64), nullable=True)
    )
    lock = op.create_table(
        "run_lock",
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("holder", sa.Uuid(), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    secret = op.create_table(
        "store_secret",
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    key = secrets.token_bytes(32)
    op.bulk_insert(lock, [{"name": _LOCK_NAME}])
    op.bulk_insert(secret, [{"name": _KEY_NAME, "value": key.hex()}])

    conn = op.get_bind()
    _rewrite(conn, "source_contribution", "content_sha", lambda old: _keyed(key, old))
    _rewrite(conn, "raw_response", "request_fingerprint", lambda _: uuid.uuid4().hex)
    _lead_owned_companies(conn)
    with op.batch_alter_table(
        "source_contribution", naming_convention=_NAMING
    ) as batch:
        batch.drop_constraint(_LEAD_FK, type_="foreignkey")
        batch.drop_index(_LEAD_INDEX)
        batch.drop_column("lead_identity_id")


def downgrade() -> None:
    with op.batch_alter_table(
        "source_contribution", naming_convention=_NAMING
    ) as batch:
        batch.add_column(sa.Column("lead_identity_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            _LEAD_FK, "lead_identity", ["lead_identity_id"], ["id"]
        )
        batch.create_index(_LEAD_INDEX, ["lead_identity_id"], unique=False)
    contribution = sa.table(
        "source_contribution",
        sa.column("id"),
        sa.column("content_sha"),
        sa.column("lead_identity_id"),
    )
    mapping = sa.table(
        "contribution_lead", sa.column("contribution_id"), sa.column("lead_identity_id")
    )
    op.execute(
        sa.update(contribution).values(
            content_sha=None,
            lead_identity_id=sa.select(mapping.c.lead_identity_id)
            .where(mapping.c.contribution_id == contribution.c.id)
            .scalar_subquery(),
        )
    )
    op.drop_table("store_secret")
    op.drop_table("run_lock")
    with op.batch_alter_table("ingestion_run") as batch:
        batch.drop_column("reprojection")
        batch.drop_column("clusters_reprojected")
