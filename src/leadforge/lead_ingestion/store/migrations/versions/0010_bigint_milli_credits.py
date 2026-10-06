"""source_run.credits_consumed_milli: INTEGER to BIGINT

Follow-up (2026-10-06) to 0008: each reported credit figure is at most ``MAX_MILLI``
(2**31 - 1 milli-Credits, ``credits``), but a source's run total is a sum of them and
can pass 32 bits, which PostgreSQL's INTEGER refuses ("integer out of range"), failing
the whole spend write. A 64-bit column keeps any realistic total exact; a figure past
it (``MAX_STORED_MILLI``) is refused by the app before the write. Bounding totals
instead would fail a source for spending, not for misreporting, so the column widens.

Engine neutral: ``batch_alter_table`` (SQLite recreates the table; its INTEGER is
already 64-bit). Every value fits both ways except, going down, a total past 32 bits,
which the downgrade cannot keep and PostgreSQL refuses rather than truncate.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "source_run"
_MILLI = "credits_consumed_milli"


def _retype(old: sa.types.TypeEngine[int], new: sa.types.TypeEngine[int]) -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column(_MILLI, existing_type=old, type_=new, existing_nullable=True)


def upgrade() -> None:
    _retype(sa.Integer(), sa.BigInteger())


def downgrade() -> None:
    _retype(sa.BigInteger(), sa.Integer())
