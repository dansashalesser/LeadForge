"""Startup check of provider-issued targeting identifiers (task 9.3, Requirement 23.4).

Some providers issue the identifiers a Target Profile column holds (a technology UID,
say) and publish a lookup surface listing the valid ones. A stale identifier would
silently match nothing, so in live mode each source that has such a lookup has its
effective vocabulary checked against it, and every identifier the provider does not
recognise is returned as an ``UnrecognisedIdentifier`` naming source, term and value.
The caller prints the warnings, like ``check_against_registry``.

Provisional decisions (see choices.md, task 9.3):

* The lookup is a caller-supplied async callable per source name returning the
  recognised identifiers; no adapter attribute or base-class method is added, because
  no adapter exists yet to define the surface.
* Synthetic mode calls nothing; a mode that is not a ``DataMode`` raises ``ValueError``.
* The lookup runs at most once per source, and only if the source has identifiers.
* A lookup failure propagates: an unchecked profile is never reported as checked.
* Identifiers are text or a list of text, compared exactly; any other vocabulary shape
  for a source that has a lookup is a ``ConfigurationError`` (the value is not echoed).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.target_profile import (
    TargetProfile,
    effective_vocabulary,
)

__all__ = ["IdentifierLookup", "UnrecognisedIdentifier", "validate_identifiers"]

# Returns every identifier the provider currently recognises.
IdentifierLookup = Callable[[], Awaitable[Collection[str]]]


@dataclass(frozen=True)
class UnrecognisedIdentifier:
    source: str
    term: str
    identifier: str

    def __str__(self) -> str:
        return (
            f"source {self.source!r} does not recognise identifier "
            f"{self.identifier!r} configured for term {self.term!r}"
        )


async def validate_identifiers(
    profile: TargetProfile,
    registry: SourceRegistry,
    lookups: Mapping[str, IdentifierLookup],
    mode: DataMode,
    *,
    path: str | Path,
) -> tuple[UnrecognisedIdentifier, ...]:
    """Identifiers the provider's lookup does not recognise (live mode only)."""
    if mode is DataMode.SYNTHETIC:
        return ()
    if mode is not DataMode.LIVE:
        raise ValueError(f"unknown data mode {mode!r}")
    unrecognised: list[UnrecognisedIdentifier] = []
    for source, lookup in lookups.items():
        cls = registry.source_class(source)
        configured = [
            (term, identifier)
            for term, vocabulary in effective_vocabulary(profile, cls).items()
            for identifier in _identifiers(path, source, term, vocabulary)
        ]
        if not configured:
            continue
        returned = await lookup()
        if isinstance(returned, str | bytes):
            raise TypeError(
                f"the identifier lookup for {source!r} returned one string; "
                "it must return a collection of identifiers"
            )
        recognised = set(returned)
        unrecognised.extend(
            UnrecognisedIdentifier(source, term, identifier)
            for term, identifier in configured
            if identifier not in recognised
        )
    return tuple(unrecognised)


def _identifiers(
    path: str | Path, source: str, term: str, vocabulary: object
) -> tuple[str, ...]:
    if isinstance(vocabulary, str):
        return (vocabulary,)
    if isinstance(vocabulary, tuple) and all(isinstance(v, str) for v in vocabulary):
        return vocabulary
    raise ConfigurationError(
        str(path),
        key_path=f"{term}.{source}",
        detail="identifiers must be text or a list of text to be checked "
        "against the provider",
    )
