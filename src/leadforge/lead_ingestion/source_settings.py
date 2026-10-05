"""Per-source settings read from ``config/sources.yaml`` (task 9.1).

Builds the ``Mapping[str, SourceSettings]`` the registry takes (7.1)::

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
  enabled. Later tasks that add keys (the concurrency bound, 6.7) extend ``_TOP``.
* Values are validated here, with the key path, rather than by ``SourceSettings``,
  whose own messages echo the offending value.
"""

from __future__ import annotations

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

__all__ = ["DEFAULT_SOURCES_PATH", "load_source_settings"]

DEFAULT_SOURCES_PATH = Path("config/sources.yaml")

_TOP = ("sources",)
_FIELDS = ("enabled", "trust_rank", "mode", "live_access")


def load_source_settings(
    path: str | Path | None = None,
) -> Mapping[str, SourceSettings]:
    """Read ``sources.yaml`` into a read-only mapping keyed by exact source name."""
    file = Path(path) if path is not None else DEFAULT_SOURCES_PATH
    document = read_yaml_document(file, missing_ok=True)
    if document is None:
        return MappingProxyType({})
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
