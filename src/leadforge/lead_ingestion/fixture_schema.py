"""Validate every fixture against its adapter's declared raw schema (task 17.2).

Requirements 5.3 and 5.4: each fixture listed in a provider's ``manifest.json`` is read
and handed to the adapter that consumes it (``BaseLeadSource.validate_fixture`` for an
endpoint response, ``validate_reference_file`` for non-JSON reference data), so the
check uses the adapter's own raw models and the vendor knowledge stays in ``adapters/``.
The first failure raises ``FixtureSchemaError`` naming the provider and the field.

Provisional decisions (see choices.md, task 17.2):

* Test-time and callable, not a startup gate: 5.2 already runs the raw-schema check on
  every synthetic fixture through ``normalize()``, and 5.4 asks that the suite fail.
* ``field`` is ``<file>`` when the file cannot be read as a JSON object, else
  ``<file>:<raw field path>``. No fixture content or value is ever put in an error.
* A fixture over ``MAX_FIXTURE_BYTES`` fails unread; JSON nested past the parser's
  recursion limit fails as unreadable rather than crashing the run.
"""

import json
from collections.abc import Mapping
from pathlib import Path

from leadforge.lead_ingestion.base_source import BaseLeadSource
from leadforge.lead_ingestion.errors import FixtureSchemaError, NormalizationError
from leadforge.lead_ingestion.fixture_metadata import (
    FixtureRecord,
    load_manifest,
    validate_fixture_tree,
)

__all__ = [
    "MAX_FIXTURE_BYTES",
    "validate_fixture_file",
    "validate_fixture_schemas",
    "validate_provider_fixtures",
]

MAX_FIXTURE_BYTES = 1_000_000


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """A repeated key hides one of its values from the schema, so it is a break."""
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def validate_fixture_file(
    provider_dir: Path, source: type[BaseLeadSource], record: FixtureRecord
) -> None:
    """Check one manifest record's file; raise ``FixtureSchemaError`` on a violation.

    Every failure is raised outside the ``except`` that caught its cause, so no
    ``__cause__`` or ``__context__`` (a ``JSONDecodeError`` keeps the whole document, a
    ``ValidationError`` the offending value) can carry fixture content to a traceback.
    """
    provider = provider_dir.name
    path = provider_dir / record.file
    failed_field: str | None = None
    text = ""
    try:
        if path.is_symlink() or path.stat().st_size > MAX_FIXTURE_BYTES:
            failed_field = record.file
        else:
            text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):  # UnicodeDecodeError is a ValueError
        failed_field = record.file
    if failed_field is None:
        failed_field = _schema_failure(source, record, text)
    if failed_field is not None:
        raise FixtureSchemaError(provider, field=failed_field)


def _schema_failure(
    source: type[BaseLeadSource], record: FixtureRecord, text: str
) -> str | None:
    """The ``field`` of the first violation in ``text``, or ``None`` if it is clean."""
    try:
        if record.endpoint is None:
            source.validate_reference_file(record.file, text)
            return None
        body: object = json.loads(text, object_pairs_hook=_no_duplicate_keys)
        if not isinstance(body, dict):
            return record.file
        source.validate_fixture(record.endpoint, body)
    except (ValueError, RecursionError):
        return record.file
    except NormalizationError as exc:
        return f"{record.file}:{exc.raw_field_path}"
    return None


def validate_provider_fixtures(
    provider_dir: Path, source: type[BaseLeadSource]
) -> None:
    """Check every fixture the provider's manifest lists, stopping at the first."""
    for record in load_manifest(provider_dir).fixtures:
        validate_fixture_file(provider_dir, source, record)


def validate_fixture_schemas(
    root: Path, sources: Mapping[str, type[BaseLeadSource]]
) -> None:
    """Check every registered source's fixtures under ``root``, in name order.

    The tree is checked first (17.1): a directory with no source, a source with no
    directory and a file the manifest does not list all fail before any schema runs.
    """
    validate_fixture_tree(root, {name: s.endpoints for name, s in sources.items()})
    for provider in sorted(sources):
        validate_provider_fixtures(root / provider, sources[provider])
