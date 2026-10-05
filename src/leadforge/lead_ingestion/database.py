"""Resolve the Lead Store database engine from configuration (task 6.3).

``DATABASE_URL`` selects the database. Unset or blank means a local SQLite file, so a
fresh checkout runs with no external service (9.2). A Postgres URL selects Postgres
with no change to adapter, orchestration or merge code (9.3): everything outside this
module sees only an ``Engine``.

This is the one module that may name a backend. Engine setup that cannot be expressed
portably lives here, outside ``store/`` (whose structural scan forbids dialect
branches): SQLite does not enforce foreign keys unless ``PRAGMA foreign_keys`` is on,
and an in-memory SQLite database exists per connection. Schema creation stays with the
migrations; nothing here creates tables or runs at import.

Credentials never appear in messages, reprs or logs from this module: errors never echo
the raw value, and URLs are rendered through ``redact_url``.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError
from sqlalchemy.pool import ConnectionPoolEntry, StaticPool

__all__ = [
    "DATABASE_URL_ENV",
    "DEFAULT_SQLITE_RELATIVE_PATH",
    "DatabaseConfigError",
    "create_store_engine",
    "redact_url",
    "resolve_database_url",
]

DATABASE_URL_ENV = "DATABASE_URL"
DEFAULT_SQLITE_RELATIVE_PATH = Path(".leadforge") / "leadforge.db"

_SUPPORTED_BACKENDS = ("sqlite", "postgresql")


class DatabaseConfigError(ValueError):
    """The database configuration is unusable; the message never holds credentials."""


def redact_url(url: URL) -> str:
    """Render `url` with the password masked, safe for logs."""
    return url.render_as_string(hide_password=True)


def resolve_database_url(
    environ: Mapping[str, str] | None = None, *, base_dir: Path | None = None
) -> URL:
    """The effective database URL: from ``DATABASE_URL`` or the local SQLite default.

    Side-effect free. Relative SQLite file paths are made absolute against `base_dir`
    (default: the current directory at call time) so a later `chdir` cannot move the
    database.
    """
    env = os.environ if environ is None else environ
    base = _base(base_dir)
    raw = env.get(DATABASE_URL_ENV, "").strip()
    if not raw:
        return _default_url(base)
    return _validated(_parse(raw), base)


def create_store_engine(
    url: URL | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    base_dir: Path | None = None,
) -> Engine:
    """Build the Lead Store engine. Pass `url` to override ``DATABASE_URL``.

    For the default SQLite file the parent directory is created. Tables are not.
    """
    if url is None:
        env = os.environ if environ is None else environ
        resolved = resolve_database_url(env, base_dir=base_dir)
        if not env.get(DATABASE_URL_ENV, "").strip():
            Path(str(resolved.database)).parent.mkdir(parents=True, exist_ok=True)
    else:
        parsed = _parse(url) if isinstance(url, str) else url
        resolved = _validated(parsed, _base(base_dir))
    if resolved.get_backend_name() == "sqlite":
        return _sqlite_engine(resolved)
    return _create(resolved)


def _base(base_dir: Path | None) -> Path:
    return (Path.cwd() if base_dir is None else base_dir).resolve()


def _default_url(base: Path) -> URL:
    return URL.create("sqlite", database=str(base / DEFAULT_SQLITE_RELATIVE_PATH))


def _parse(raw: str) -> URL:
    try:
        return make_url(raw.strip())
    except ArgumentError:
        # Deliberately not chained: the parser's message may quote the raw value.
        raise DatabaseConfigError(
            f"{DATABASE_URL_ENV} is not a valid database URL"
        ) from None


def _validated(url: URL, base: Path) -> URL:
    url = _normalise_driver_name(url)
    backend = url.get_backend_name()
    if backend not in _SUPPORTED_BACKENDS:
        raise DatabaseConfigError(
            f"{DATABASE_URL_ENV} names unsupported backend {backend!r}; "
            f"supported: {', '.join(_SUPPORTED_BACKENDS)}"
        )
    if backend == "sqlite" and _is_relative_sqlite_file(url):
        assert url.database is not None
        url = url.set(database=str(base / url.database))
    return url


def _normalise_driver_name(url: URL) -> URL:
    """Accept the ``postgres`` alias that SQLAlchemy 2 no longer registers."""
    name = url.drivername
    if name == "postgres" or name.startswith("postgres+"):
        return url.set(drivername="postgresql" + name[len("postgres") :])
    return url


def _is_in_memory(url: URL) -> bool:
    return url.database in (None, "", ":memory:")


def _is_relative_sqlite_file(url: URL) -> bool:
    db = url.database
    if _is_in_memory(url) or db is None or db.startswith("file:"):
        return False
    return not Path(db).is_absolute()


def _create(url: URL, **kwargs: Any) -> Engine:
    try:
        return create_engine(url, **kwargs)
    except (ModuleNotFoundError, NoSuchModuleError):
        raise DatabaseConfigError(
            f"database driver {url.get_driver_name()!r} for backend "
            f"{url.get_backend_name()!r} is not installed"
        ) from None


def _sqlite_engine(url: URL) -> Engine:
    kwargs: dict[str, Any] = {}
    if _is_in_memory(url):
        # One shared connection, or every checkout would see its own empty database.
        kwargs = {
            "poolclass": StaticPool,
            "connect_args": {"check_same_thread": False},
        }
    engine = _create(url, **kwargs)
    event.listen(engine, "connect", _enable_foreign_keys)
    return engine


def _enable_foreign_keys(dbapi_connection: Any, _record: ConnectionPoolEntry) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
