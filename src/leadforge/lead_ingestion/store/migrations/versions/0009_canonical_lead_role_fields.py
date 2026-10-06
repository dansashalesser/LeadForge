"""canonical_lead role-address fields: email_is_role_address, role_contact_emails

Follow-up (2026-10-06) to the email-preference user decision: ``CanonicalLead`` gained
``email_is_role_address`` (its email is a role address such as ``info@``) and
``role_contact_emails`` (the role addresses kept as company contacts). Both are stored
so the Lead read back is the Lead projected, and a change in either is an update.

``email_is_role_address`` is NOT NULL: a ``false`` server default fills every existing
row ("not a role address" until it is re-projected) and is then removed, since the
schema carries no ``server_default`` (``store.models``; the app writes the value).
``role_contact_emails`` is a nullable JSON list (NULL: projected before 0009). Engine
neutral: ``sa.false()`` renders on both engines and the alter and drop run in batch
mode for SQLite. No data moves either way.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "canonical_lead"
_FLAG = "email_is_role_address"
_CONTACTS = "role_contact_emails"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(_FLAG, sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(_TABLE, sa.Column(_CONTACTS, sa.JSON(), nullable=True))
    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column(_FLAG, existing_type=sa.Boolean(), server_default=None)


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_CONTACTS)
        batch.drop_column(_FLAG)
