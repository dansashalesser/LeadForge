"""Identity Exclusions read from configuration (task 16.6; Requirement 8.13).

The file is ``config/identity_exclusions.yaml``::

    linkedin_urls:
      - https://www.linkedin.com/in/some-shared-profile
    emails:
      - info@example.com

Provisional decisions (choices.md, 16.6):

* An absent or empty file is the empty set; a missing optional file is not an error.
* Values are normalised by ``IdentityExclusions.from_values``, the very code key
  extraction uses, so a differently cased or slashed value still bars.
* The set is personal data: an error names the file and the key path (``emails[2]``)
  and never the value, nor the text of an unknown top-level key.
* Storing the ``projection_version`` bump when the set changes needs the Lead Store
  recompute wiring (a later task); projection's keyed ``ProjectionBasis`` is the seam.
"""

from collections.abc import Mapping
from pathlib import Path

from leadforge.lead_ingestion.config_file import read_yaml_document
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.match_keys import IdentityExclusions

__all__ = ["DEFAULT_EXCLUSIONS_PATH", "load_identity_exclusions"]

DEFAULT_EXCLUSIONS_PATH = Path("config/identity_exclusions.yaml")
_KEYS = ("linkedin_urls", "emails")


def load_identity_exclusions(path: str | Path | None = None) -> IdentityExclusions:
    """Read the exclusion set; raises ``ConfigurationError``, never echoing a value."""
    file = Path(path) if path is not None else DEFAULT_EXCLUSIONS_PATH
    document = read_yaml_document(file, missing_ok=True)
    if document is None:
        return IdentityExclusions()
    if not isinstance(document, Mapping):
        raise ConfigurationError(
            str(file), key_path="", detail="top level must be a mapping"
        )
    if any(key not in _KEYS for key in document):
        raise ConfigurationError(
            str(file),
            key_path="",
            detail=f"unknown top-level key; expected only {', '.join(_KEYS)}",
        )
    return IdentityExclusions(
        linkedin_urls=_read_list(file, "linkedin_urls", document),
        emails=_read_list(file, "emails", document),
    )


def _read_list(
    file: Path, name: str, document: Mapping[object, object]
) -> frozenset[str]:
    raw = document.get(name)
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise ConfigurationError(
            str(file), key_path=name, detail="must be a list of text"
        )
    found: set[str] = set()
    for index, item in enumerate(raw):
        try:
            one = IdentityExclusions.from_values(**{name: [item]})
        except ValueError:
            raise ConfigurationError(
                str(file),
                key_path=f"{name}[{index}]",
                detail="must name a usable value of this kind",
            ) from None
        found |= getattr(one, name)
    return frozenset(found)
