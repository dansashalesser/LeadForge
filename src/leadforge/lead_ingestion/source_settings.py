"""Per-source settings read from ``config/sources.yaml`` (task 9.1).

Builds the ``Mapping[str, SourceSettings]`` the registry takes (7.1)::

    max_concurrent_sources: 4   # cross-source worker pool bound (6.7); default 4
    run_timeout_s: 600          # per-run wall-clock bound, seconds (6.6); default 600
    sources:
      <exact source name>:
        enabled: true        # default true
        trust_rank: 0        # non-negative int, higher wins; default 0
        mode: synthetic      # optional per-source override (3.6)
        live_access: gated   # optional override (3.6, 4.3)

Provisional decisions (see choices.md, task 9.1):

* A missing or empty file means "no configuration": every source defaults to
  enabled at the lowest rank (7.1), so adding a source never needs this file.
* Unknown keys are errors, so a misspelt ``enabeld`` cannot silently leave a source
  enabled. Later tasks that add keys extend ``_TOP``.
* The cross-source concurrency bound (task 11.1, 6.7) is a top-level key beside
  ``sources``, read by ``load_max_concurrent_sources``; it must be a positive integer
  and defaults to ``DEFAULT_MAX_CONCURRENT_SOURCES`` when the file or key is absent.
* The per-run wall-clock timeout (task 11.4, 6.6) is a top-level key beside them, read
  by ``load_run_timeout_s``; it must be a positive finite number of seconds and
  defaults to ``DEFAULT_RUN_TIMEOUT_S`` when the file or key is absent.
* Values are validated here, with the key path, rather than by ``SourceSettings``,
  whose own messages echo the offending value.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType

from leadforge.lead_ingestion.base_source import LiveAccess
from leadforge.lead_ingestion.config_file import (
    check_name,
    join_path,
    read_yaml_document,
)
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import LOWEST_TRUST_RANK, SourceSettings

__all__ = [
    "DEFAULT_MAX_CONCURRENT_SOURCES",
    "DEFAULT_RUN_TIMEOUT_S",
    "DEFAULT_SOURCES_PATH",
    "load_max_concurrent_sources",
    "load_run_timeout_s",
    "load_source_settings",
]

DEFAULT_SOURCES_PATH = Path("config/sources.yaml")

DEFAULT_MAX_CONCURRENT_SOURCES = 4

DEFAULT_RUN_TIMEOUT_S = 600

_TOP = ("sources", "max_concurrent_sources", "run_timeout_s")
_FIELDS = ("enabled", "trust_rank", "mode", "live_access")


def load_source_settings(
    path: str | Path | None = None,
) -> Mapping[str, SourceSettings]:
    """Read ``sources.yaml`` into a read-only mapping keyed by exact source name."""
    file, document = _read_top(path)
    if document is None:
        return MappingProxyType({})
    raw = document.get("sources")
    if raw is None:
        return MappingProxyType({})
    if not isinstance(raw, Mapping):
        raise ConfigurationError(
            str(file), key_path="sources", detail="must be a mapping of source name"
        )
    return MappingProxyType(
        {
            check_name(file, "sources", name): _read_entry(file, name, entry)
            for name, entry in raw.items()
        }
    )


def load_max_concurrent_sources(path: str | Path | None = None) -> int:
    """The cross-source concurrency bound (6.7); 4 when the file or key is absent."""
    file, document = _read_top(path)
    if document is None or "max_concurrent_sources" not in document:
        return DEFAULT_MAX_CONCURRENT_SOURCES
    value = document["max_concurrent_sources"]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigurationError(
            str(file),
            key_path="max_concurrent_sources",
            detail="must be an integer >= 1",
        )
    return value


def load_run_timeout_s(path: str | Path | None = None) -> float:
    """The per-run wall-clock timeout in seconds (6.6); 600 if file or key is absent."""
    file, document = _read_top(path)
    if document is None or "run_timeout_s" not in document:
        return DEFAULT_RUN_TIMEOUT_S
    value = document["run_timeout_s"]
    seconds = 0.0
    if isinstance(value, int | float) and not isinstance(value, bool):
        with contextlib.suppress(OverflowError):  # too large for a float: stays 0.0
            seconds = float(value)
    if not 0 < seconds < math.inf:  # also rejects NaN
        raise ConfigurationError(
            str(file),
            key_path="run_timeout_s",
            detail="must be a positive finite number of seconds",
        )
    return seconds


def _read_top(path: str | Path | None) -> tuple[Path, Mapping[object, object] | None]:
    file = Path(path) if path is not None else DEFAULT_SOURCES_PATH
    document = read_yaml_document(file, missing_ok=True)
    if document is None:
        return file, None
    if not isinstance(document, Mapping):
        raise ConfigurationError(
            str(file), key_path="", detail="top level must be a mapping"
        )
    for key in document:
        if key not in _TOP:
            raise ConfigurationError(
                str(file),
                key_path=key if isinstance(key, str) else "",
                detail=f"unknown top-level key; expected one of {', '.join(_TOP)}",
            )
    return file, document


def _read_entry(file: Path, name: str, entry: object) -> SourceSettings:
    base = join_path("sources", name)
    if entry is None:
        return SourceSettings()
    if not isinstance(entry, Mapping):
        raise ConfigurationError(
            str(file), key_path=base, detail="must be a mapping of settings"
        )

    def fail(field: str, detail: str) -> ConfigurationError:
        return ConfigurationError(
            str(file), key_path=join_path(base, field), detail=detail
        )

    for key in entry:
        if key not in _FIELDS:
            raise fail(
                key if isinstance(key, str) else "",
                f"unknown setting; expected one of {', '.join(_FIELDS)}",
            )
    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise fail("enabled", "must be true or false")
    rank = entry.get("trust_rank", LOWEST_TRUST_RANK)
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < LOWEST_TRUST_RANK:
        raise fail("trust_rank", f"must be an integer >= {LOWEST_TRUST_RANK}")
    mode = _choice(entry, "mode", DataMode, fail)
    live_access = _choice(entry, "live_access", LiveAccess, fail)
    return SourceSettings(
        enabled=enabled, trust_rank=rank, mode=mode, live_access=live_access
    )


def _choice[E: (DataMode, LiveAccess)](
    entry: Mapping[object, object],
    field: str,
    enum: type[E],
    fail: Callable[[str, str], ConfigurationError],
) -> E | None:
    value = entry.get(field)
    if value is None:
        return None
    allowed = [m.value for m in enum]
    if not isinstance(value, str) or value not in allowed:
        raise fail(field, f"must be one of {', '.join(allowed)}")
    return enum(value)
