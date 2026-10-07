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
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine

__all__ = [
    "MIGRATIONS_DIR",
    "StoreNotMigratedError",
    "alembic_config",
    "downgrade_to_base",
    "require_head",
    "upgrade_to_head",
]

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

type MigrationTarget = str | Connection


class StoreNotMigratedError(RuntimeError):
    """The store's schema is not at the newest revision (or was never created)."""


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


def require_head(engine: Engine) -> None:
    """Raise `StoreNotMigratedError` unless `engine`'s schema is at the newest revision.

    Readers call this instead of migrating: only ``ingest`` writes schema.
    """
    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current != head:
        found = "no schema" if current is None else f"revision {current}"
        raise StoreNotMigratedError(
            f"lead store has {found}, expected revision {head}; "
            "run `leadforge ingest` against this database first"
        )


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
