"""Environment-file loader that never overrides the process (task 8.2, Req 10.6).

Rules (provisional, see choices.md task 8.2):

* Default file: ``.env`` under the working directory. Override: the
  ``LEADFORGE_ENV_FILE`` variable of the *process* mapping (never of the file
  itself), then an explicit ``path`` argument, which wins over both.
* An absent file (including a dangling symlink) is not an error. A directory, an
  unreadable file, invalid UTF-8, or a malformed line fails with ``EnvFileError``.
* A variable counts as already set when the target mapping holds a value that is
  not blank after stripping; a blank value counts as unset, matching how 8.1 reads
  credentials. Process values are never replaced otherwise.
* Parsing is python-dotenv's parser (design, Technology Stack): comments, blank
  lines, ``export``, quoting, ``=`` inside values. A BOM is stripped and CRLF is
  normalised before parsing. A repeated key in the file: the last occurrence wins.
  A bare ``KEY`` with no ``=`` is malformed here (dotenv would yield ``None``).
* Errors name the file path and a line NUMBER only; never line content or bytes.
  Nothing is applied unless the whole file parses.
* The loader writes into an injected mapping. ``load_env_file_into_process`` is
  the single place that touches ``os.environ``.
"""

from __future__ import annotations

import io
import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path

from dotenv.parser import parse_stream

__all__ = [
    "DEFAULT_ENV_FILE",
    "ENV_FILE_VARIABLE",
    "EnvFileError",
    "load_env_file",
    "load_env_file_into_process",
    "parse_env_text",
    "resolve_env_file_path",
]

DEFAULT_ENV_FILE = ".env"
ENV_FILE_VARIABLE = "LEADFORGE_ENV_FILE"


class EnvFileError(Exception):
    """The environment file could not be read or parsed. Never carries content."""


def parse_env_text(text: str) -> dict[str, str]:
    """Parse environment-file text. Raises ``EnvFileError`` naming a line number."""
    text = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    values: dict[str, str] = {}
    for binding in parse_stream(io.StringIO(text)):
        if binding.error or (binding.key is not None and binding.value is None):
            raw = binding.original.string
            # The parser folds preceding blank lines into an error's span.
            line = binding.original.line + len(raw) - len(raw.lstrip("\n"))
            raise EnvFileError(f"malformed environment file entry at line {line}")
        if binding.key is not None and binding.value is not None:
            values[binding.key] = binding.value
    return values


def resolve_env_file_path(
    environ: Mapping[str, str] | None = None,
    path: str | Path | None = None,
    *,
    base_dir: Path | None = None,
) -> Path:
    """Explicit ``path``, else ``LEADFORGE_ENV_FILE`` in ``environ``, else ``.env``."""
    env = os.environ if environ is None else environ
    chosen: str | Path | None = path
    if chosen is None:
        configured = env.get(ENV_FILE_VARIABLE, "").strip()
        chosen = configured or DEFAULT_ENV_FILE
    return (Path.cwd() if base_dir is None else base_dir) / chosen


def _read(path: Path) -> dict[str, str] | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        raise EnvFileError(f"environment file {path} is not a file") from None
    except OSError as exc:
        raise EnvFileError(
            f"environment file {path} could not be read ({type(exc).__name__})"
        ) from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise EnvFileError(f"environment file {path} is not valid UTF-8") from None
    try:
        return parse_env_text(text)
    except EnvFileError as exc:
        raise EnvFileError(f"{exc} in {path}") from None


def load_env_file(
    environ: MutableMapping[str, str],
    path: str | Path | None = None,
    *,
    base_dir: Path | None = None,
) -> frozenset[str]:
    """Apply the file into ``environ`` without replacing set variables.

    Returns the names applied (never values).
    """
    values = _read(resolve_env_file_path(environ, path, base_dir=base_dir))
    if not values:
        return frozenset()
    applied = {
        name for name, value in values.items() if not environ.get(name, "").strip()
    }
    for name in applied:
        environ[name] = values[name]
    return frozenset(applied)


def load_env_file_into_process(path: str | Path | None = None) -> frozenset[str]:
    """The one entry point that mutates ``os.environ``. Call once at startup."""
    return load_env_file(os.environ, path)
