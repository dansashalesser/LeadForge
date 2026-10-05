"""Fail on a fixture field no adapter rule consumes and no ignore set lists (task 17.3).

Requirement 5.5: no fixture carries a field its provider's documented schema does not
define. Every adapter's raw models tolerate unknown fields (providers add some without
notice), so the raw-schema check (17.2) cannot see one. This guard asks the adapter
(``BaseLeadSource.unmapped_fixture_paths``, which walks every leaf path against the
adapter's own rule tables and declared ignore sets) and fails on the first path left
over, naming the provider and the path.

Provisional decisions (see choices.md, task 17.3):

* The adapter owns the walk, as it owns ``validate_fixture``: only it knows how a
  fixture body is reshaped into the records its rules read, and the vendor knowledge
  stays in ``adapters/``.
* The raw-schema check runs first, so an unreadable or schema-breaking file fails
  exactly as under 17.2 and a fixture is only walked once it is known to be a JSON
  object. A reference file (no endpoint) has no fields to map and is not walked.
* ``field`` is ``<file>:<path>``, the path escaped and cut to ``_PATH_LIMIT`` because a
  provider chooses its keys. No value is ever put in a message, and the error is raised
  outside any ``except`` so no ``__context__`` can hold fixture content.
"""

import json
from collections.abc import Mapping
from pathlib import Path

from leadforge.lead_ingestion.base_source import BaseLeadSource
from leadforge.lead_ingestion.errors import FixtureSchemaError
from leadforge.lead_ingestion.fixture_metadata import (
    FixtureRecord,
    load_manifest,
    validate_fixture_tree,
)
from leadforge.lead_ingestion.fixture_schema import validate_fixture_file

__all__ = [
    "validate_fixture_field_coverage",
    "validate_fixture_fields",
    "validate_provider_fixture_fields",
]

_PATH_LIMIT = 120


def _bounded(path: str) -> str:
    return ascii(path)[1:-1][:_PATH_LIMIT]


def validate_fixture_field_coverage(
    provider_dir: Path, source: type[BaseLeadSource], record: FixtureRecord
) -> None:
    """Check one manifest record's file; raise ``FixtureSchemaError`` on a stray one."""
    validate_fixture_file(provider_dir, source, record)
    if record.endpoint is None:
        return
    body: object = json.loads((provider_dir / record.file).read_text(encoding="utf-8"))
    unmapped = source.unmapped_fixture_paths(record.endpoint, body)
    if unmapped:
        raise FixtureSchemaError(
            provider_dir.name, field=f"{record.file}:{_bounded(sorted(unmapped)[0])}"
        )


def validate_provider_fixture_fields(
    provider_dir: Path, source: type[BaseLeadSource]
) -> None:
    """Check every fixture the provider's manifest lists, stopping at the first."""
    for record in load_manifest(provider_dir).fixtures:
        validate_fixture_field_coverage(provider_dir, source, record)


def validate_fixture_fields(
    root: Path, sources: Mapping[str, type[BaseLeadSource]]
) -> None:
    """Check every registered source's fixtures under ``root``, in name order.

    The tree is checked first (17.1), as ``validate_fixture_schemas`` does.
    """
    validate_fixture_tree(root, {name: s.endpoints for name, s in sources.items()})
    for provider in sorted(sources):
        validate_provider_fixture_fields(root / provider, sources[provider])
