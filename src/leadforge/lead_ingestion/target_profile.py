"""Target Profile: what we are looking for, built from the Product Catalog.

Requirement 23: the targeting definition is data, never code. A profile names
*terms* (technologies and competitors) and, for each term, the vocabulary every
provider uses to express it, because providers use mutually incompatible
identifiers for one technology. ``Catalog.to_profile`` builds it; there is no
profile file::

    technologies / competitors -> <term> -> <source name> -> <opaque vocabulary>

A term with no entry for a source, or an empty one, is Not Applicable for that
source (3.3), never "no match".

* Source names are checked exactly (no case folding).
* A column for a source that is not registered is a warning the caller prints
  (deferred providers keep their columns); an empty column over a term the source
  declares answerable is a ``ConfigurationError``.
* Keyword rendering is plain ``{term}`` substitution with ``str.replace``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from leadforge.lead_ingestion.base_source import (
    TARGET_TERM_PATH_PREFIX,
    BaseLeadSource,
    _is_empty_vocabulary,
)
from leadforge.lead_ingestion.config_file import (
    freeze,
    join_path,
)
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.registry import SourceRegistry

__all__ = [
    "TargetProfile",
    "check_against_registry",
    "effective_vocabulary",
]

TERM_PLACEHOLDER = "{term}"
_SECTIONS = ("technologies", "competitors")

# term -> source name -> opaque vocabulary
Vocabularies = Mapping[str, Mapping[str, object]]


@dataclass(frozen=True)
class TargetProfile:
    technologies: Vocabularies = field(default_factory=dict)
    competitors: Vocabularies = field(default_factory=dict)
    keyword_templates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Direct construction gets the same read-only guarantee as a loaded file.
        where = Path("<TargetProfile>")
        for name in _SECTIONS:
            object.__setattr__(self, name, freeze(where, name, getattr(self, name)))
        object.__setattr__(self, "keyword_templates", tuple(self.keyword_templates))

    def terms(self) -> tuple[str, ...]:
        """Technologies then competitors, each in file order."""
        return (*self.technologies, *self.competitors)

    def providers(self) -> tuple[str, ...]:
        """Every source named by any column, in order of first appearance."""
        seen: dict[str, None] = {}
        for vocabularies in (self.technologies, self.competitors):
            for columns in vocabularies.values():
                seen.update(dict.fromkeys(columns))
        return tuple(seen)

    def vocabulary(self, provider: str, term: str) -> object | None:
        """The provider's vocabulary for the term, or ``None`` if it has none.

        ``None`` means Not Applicable (no column, or an empty one), not "no match".
        """
        for vocabularies in (self.technologies, self.competitors):
            columns = vocabularies.get(term)
            if columns is not None:
                value = columns.get(provider)
                return None if _is_empty_vocabulary(value) else value
        return None

    def has_column(self, provider: str, term: str) -> bool:
        """Whether the file names the provider under the term, empty or not."""
        for vocabularies in (self.technologies, self.competitors):
            columns = vocabularies.get(term)
            if columns is not None:
                return provider in columns
        return False

    def vocabulary_for(self, provider: str) -> Mapping[str, object]:
        """Every term the provider can express, with its vocabulary."""
        return MappingProxyType(
            {
                term: value
                for term in self.terms()
                if (value := self.vocabulary(provider, term)) is not None
            }
        )

    def render_keywords(self, term: str) -> tuple[str, ...]:
        """Each keyword template with ``{term}`` replaced by the canonical term."""
        if term not in self.terms():
            raise KeyError(term)
        return tuple(t.replace(TERM_PLACEHOLDER, term) for t in self.keyword_templates)

    def unregistered_providers(self, registered: Iterable[str]) -> tuple[str, ...]:
        """Columns naming a source that is not registered, in profile order."""
        known = set(registered)
        return tuple(p for p in self.providers() if p not in known)


def effective_vocabulary(
    profile: TargetProfile, source: type[BaseLeadSource]
) -> Mapping[str, object]:
    """Per profile term, the vocabulary the source is asked with.

    The profile is the only source: an adapter declares no default. A term with no
    column for the source, or an empty one, is Not Applicable for it and is left out
    of the result (3.3).
    """
    effective: dict[str, object] = {}
    for term in profile.terms():
        if not profile.has_column(source.name, term):
            continue
        value = profile.vocabulary(source.name, term)
        if not _is_empty_vocabulary(value):
            effective[term] = value
    return MappingProxyType(effective)


def check_against_registry(
    profile: TargetProfile, registry: SourceRegistry, *, path: str | Path
) -> tuple[str, ...]:
    """Cross-check the profile with each registered source's own declaration.

    The configuration overrides the adapter default (3.3), so a column may make a term
    expressible. What it may not do is contradict a declared surface: a source that
    declares ``target_profile.<term>`` answerable (Negative Evidence possible) needs an
    effective vocabulary for it, so an empty column over such a term raises. Returns
    the sources named by a column but not registered: the caller reports them as a
    startup warning, not an error.
    """
    registered = set(registry.names())
    for section in _SECTIONS:
        for term, columns in getattr(profile, section).items():
            for source in columns:
                if source not in registered:
                    continue
                cls = registry.source_class(source)
                surface = f"{TARGET_TERM_PATH_PREFIX}{term}"
                if surface in cls.answerable_surfaces and term not in (
                    effective_vocabulary(profile, cls)
                ):
                    raise ConfigurationError(
                        str(path),
                        key_path=join_path(join_path(section, term), source),
                        detail="source declares a surface for this term, so the "
                        "column cannot be empty; give it a vocabulary or change "
                        "the adapter's declaration",
                    )
    return profile.unregistered_providers(registered)
