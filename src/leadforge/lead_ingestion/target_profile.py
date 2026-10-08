"""Target Profile: what we are looking for, read from configuration (task 9.1).

Requirement 23: the targeting definition is data, never code. A profile names
*terms* (technologies and competitors) and, for each term, the vocabulary every
provider uses to express it, because providers use mutually incompatible
identifiers for one technology. The file is ``config/target_profile.yaml``::

    technologies:
      <term>:
        <source name>: <opaque vocabulary: id, phrase, list of either, or mapping>
    competitors:
      <term>:
        <source name>: ...
    keyword_templates:
      - "<text containing {term}>"

Adding a term is one block in this file. Adding a source is one adapter module plus
one ``<source name>:`` line under each term it can express. A term with no line for
a source, or an empty one, is Not Applicable for that source (3.3), never "no match",
unless the adapter declares a default for that term (see ``effective_vocabulary``).

Provisional decisions (see choices.md, task 9.1):

* The path is ``DEFAULT_TARGET_PROFILE_PATH`` (relative to the working directory) or
  an explicit argument; there is no environment override.
* Source names are checked exactly (no case folding), like ``config/sources.yaml``.
* A column for a source that is not registered is a warning the caller prints
  (deferred providers keep their columns); an empty column over a term the source
  declares answerable is a ``ConfigurationError``.
* The adapter's ``target_vocabulary`` is a default; a column replaces it per term.
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
    check_name,
    freeze,
    join_path,
    read_yaml_document,
)
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.registry import SourceRegistry

__all__ = [
    "DEFAULT_TARGET_PROFILE_PATH",
    "TargetProfile",
    "check_against_registry",
    "effective_vocabulary",
    "load_target_profile",
]

DEFAULT_TARGET_PROFILE_PATH = Path("config/target_profile.yaml")

TERM_PLACEHOLDER = "{term}"
_SECTIONS = ("technologies", "competitors")
_KNOWN_KEYS = (*_SECTIONS, "keyword_templates")

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


def load_target_profile(path: str | Path | None = None) -> TargetProfile:
    """Read and validate the Target Profile; raises ``ConfigurationError``."""
    file = Path(path) if path is not None else DEFAULT_TARGET_PROFILE_PATH
    document = read_yaml_document(file)
    if document is None:
        raise ConfigurationError(str(file), key_path="", detail="file is empty")
    if not isinstance(document, Mapping):
        raise ConfigurationError(
            str(file), key_path="", detail="top level must be a mapping"
        )
    for key in document:
        if key not in _KNOWN_KEYS:
            _reject_unknown_key(file, key)
    sections = {name: _read_terms(file, name, document.get(name)) for name in _SECTIONS}
    shared = set(sections["technologies"]) & set(sections["competitors"])
    if shared:
        term = next(t for t in sections["competitors"] if t in shared)
        raise ConfigurationError(
            str(file),
            key_path=join_path("competitors", term),
            detail="term is already declared under technologies",
        )
    if not sections["technologies"] and not sections["competitors"]:
        raise ConfigurationError(
            str(file), key_path="", detail="no technologies or competitors declared"
        )
    return TargetProfile(
        technologies=sections["technologies"],
        competitors=sections["competitors"],
        keyword_templates=_read_templates(file, document.get("keyword_templates")),
    )


def _reject_unknown_key(file: Path, key: object) -> None:
    named = key if isinstance(key, str) else ""
    raise ConfigurationError(
        str(file),
        key_path=named,
        detail=f"unknown top-level key; expected one of {', '.join(_KNOWN_KEYS)}",
    )


def _read_terms(file: Path, section: str, raw: object) -> dict[str, dict[str, object]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigurationError(
            str(file), key_path=section, detail="must be a mapping of term to columns"
        )
    terms: dict[str, dict[str, object]] = {}
    for key, columns in raw.items():
        term = check_name(file, section, key)
        term_path = join_path(section, term)
        if columns is None:
            columns = {}
        if not isinstance(columns, Mapping):
            raise ConfigurationError(
                str(file),
                key_path=term_path,
                detail="must be a mapping of source name to vocabulary",
            )
        terms[term] = {
            check_name(file, term_path, source): freeze(
                file, join_path(term_path, source), vocabulary
            )
            for source, vocabulary in columns.items()
        }
    return terms


def _read_templates(file: Path, raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigurationError(
            str(file), key_path="keyword_templates", detail="must be a list of text"
        )
    for index, template in enumerate(raw):
        if not isinstance(template, str) or not template.strip():
            raise ConfigurationError(
                str(file),
                key_path=f"keyword_templates[{index}]",
                detail="must be non-blank text",
            )
    return tuple(raw)


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
