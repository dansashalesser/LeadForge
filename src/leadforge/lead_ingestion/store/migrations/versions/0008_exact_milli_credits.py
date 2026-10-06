"""exact Credits: source_run.credits_consumed becomes integer milli-Credits

Follow-up (2026-10-06) to Requirements 16.8 and 21.2. A provider plan may price a call
at half a Credit; a whole-number column made the adapter round up per batch.
``source_run.credits_consumed`` (whole Credits) is replaced by
``credits_consumed_milli`` (Credits x 1000), which the app reads and writes as an exact
``Decimal`` (``store.models.MilliCredits``). Integer milli-Credits were chosen over
``Numeric``: an integer is exact and identical on every engine, while SQLite stores a
NUMERIC as a float.

Upgrade keeps every value exactly (x 1000; NULL stays NULL: not reported). Downgrade
restores whole Credits rounding a fraction UP, so the older schema never under-reports
what was spent. Both directions are Core statements (no raw SQL, no backend branch);
``//`` is integer floor division on both engines, and every figure is non-negative.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "source_run"
_WHOLE = "credits_consumed"
_MILLI = "credits_consumed_milli"
_PER_CREDIT = 1000


def _move(source: str, target: str, value: sa.ColumnElement[int]) -> None:
    """Add ``target``, fill it from ``source`` through ``value``, drop ``source``."""
    op.add_column(_TABLE, sa.Column(target, sa.Integer(), nullable=True))
    op.execute(
        sa.table(_TABLE, sa.column(source), sa.column(target))
        .update()
        .values({target: value})
    )
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(source)


def upgrade() -> None:
    _move(_WHOLE, _MILLI, sa.column(_WHOLE, sa.Integer) * _PER_CREDIT)


def downgrade() -> None:
    milli = sa.column(_MILLI, sa.Integer)
    _move(_MILLI, _WHOLE, (milli + (_PER_CREDIT - 1)) // _PER_CREDIT)
