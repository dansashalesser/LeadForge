"""Alembic environment: online migrations against a passed connection or URL.

The connection (``config.attributes["connection"]``) wins; otherwise the URL in
``sqlalchemy.url`` is used. Offline (SQL script) mode is not supported.
"""

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from leadforge.lead_ingestion.store.models import Base

config = context.config


def _migrate(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _migrate(connection)
        return
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        _migrate(connection)


if context.is_offline_mode():
    raise NotImplementedError("offline (--sql) migrations are not supported")
run_migrations_online()
