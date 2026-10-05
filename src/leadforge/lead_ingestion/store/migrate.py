"""Programmatic entry points to the Alembic migrations (task 6.2).

Schema reaches a database only through these revisions; nothing here runs at import
or application startup, and the caller decides when to migrate. Engine resolution from
configuration is task 6.3, so the target is passed in: a database URL, or an open
``Connection`` (the caller owns its transaction; used for in-memory SQLite in tests).
"""

from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine

__all__ = ["MIGRATIONS_DIR", "alembic_config", "downgrade_to_base", "upgrade_to_head"]

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

type MigrationTarget = str | Connection


def alembic_config(target: MigrationTarget) -> Config:
    """Build an Alembic config bound to `target` (URL string or open connection)."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if isinstance(target, str):
        # Config values go through ConfigParser interpolation; escape literal '%'.
        config.set_main_option("sqlalchemy.url", target.replace("%", "%%"))
    else:
        config.attributes["connection"] = target
    return config


def upgrade_to_head(target: MigrationTarget) -> None:
    """Apply every pending revision. Idempotent."""
    _run(target, lambda cfg: command.upgrade(cfg, "head"))


def downgrade_to_base(target: MigrationTarget) -> None:
    """Revert every revision (test and local-reset use)."""
    _run(target, lambda cfg: command.downgrade(cfg, "base"))


def _run(target: MigrationTarget, action: Callable[[Config], None]) -> None:
    if isinstance(target, str):
        engine = create_engine(target)
        try:
            with engine.begin() as connection:
                action(alembic_config(connection))
        finally:
            engine.dispose()
    else:
        action(alembic_config(target))
