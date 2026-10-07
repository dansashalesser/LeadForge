"""Fixture provenance metadata (task 17.1, Requirements 5.1 and 5.6).

Each directory ``fixtures/<provider>/`` carries one ``manifest.json`` with one record
per fixture file: where it came from (``origin``), the provider documentation URL and
the date the schema was verified against it (5.6), and, for a capture, how it was
redacted. ``origin`` makes a hand-made stand-in machine-readable, so a later capture
can replace it knowingly.

Provisional decisions (see choices.md, task 17.1):

* The layout is the design's: ``manifest.json`` per provider directory, not a sidecar
  per fixture. A JSON file's ``endpoint`` must equal its file stem, the convention
  ``FixtureTransport`` already serves; a non-JSON reference file has ``endpoint`` None.
* Task 17.3: a second outcome for one endpoint is a variant in a subdirectory
  (``no_match/search.json``), recorded like any fixture and named by its file name; the
  root ``<endpoint>.json`` stays mandatory: it is the file the transport serves.
* Every failure is a ``FixtureSchemaError`` naming the provider and the offending
  field path. No metadata value and no fixture content is ever put in a message.
* ``schema_status`` is explicit. A date is recorded only when the schema was checked
  against the provider's documentation (``verified``); otherwise the date is null and
  the status is ``unverified``, so no date asserts a check nobody made (5.6).
* ``redaction`` is required for a capture and forbidden otherwise, so a hand-made file
  cannot claim a scrub that never happened.
"""

from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationError,
)

from leadforge.lead_ingestion.base_source import Endpoint
from leadforge.lead_ingestion.errors import FixtureSchemaError

__all__ = [
    "MANIFEST_NAME",
    "FixtureManifest",
    "FixtureOrigin",
    "FixtureRecord",
    "SchemaStatus",
    "load_manifest",
    "validate_fixture_tree",
]

MANIFEST_NAME = "manifest.json"

_HttpsUrl = Annotated[str, StringConstraints(pattern=r"^https://[^\s/]+\S*$")]
_Text = Annotated[str, StringConstraints(min_length=1, max_length=300)]
_Note = Annotated[str, StringConstraints(min_length=1, max_length=500)]


def _relative_posix(value: str) -> str:
    parts = value.split("/")
    if value.startswith("/") or "\\" in value or {"", ".", ".."} & set(parts):
        raise ValueError("not a plain relative path")
    return value


_File = Annotated[
    str,
    StringConstraints(min_length=1, max_length=200),
    AfterValidator(_relative_posix),
]


class FixtureOrigin(StrEnum):
    CAPTURED = "captured"  # a real provider response, redacted
    HAND_MADE = "hand_made"  # authored by us from the documented shape
    DOC_EXAMPLE = "doc_example"  # copied from the provider's documentation


class SchemaStatus(StrEnum):
    VERIFIED = "verified"  # checked against the provider's documentation on a date
    UNVERIFIED = "unverified"  # shape taken from notes or memory; never checked


class FixtureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file: _File
    endpoint: (
        _Text | None
    )  # key in the adapter's ``endpoints``; None for reference data
    origin: FixtureOrigin
    recorded_on: date  # capture date, or authoring date for a made or copied file
    doc_url: _HttpsUrl
    schema_status: SchemaStatus
    schema_verified_on: date | None  # null exactly when ``schema_status`` is unverified
    api_version: _Text | None
    redaction: _Text | None
    note: _Note


class FixtureManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: _Text
    fixtures: tuple[FixtureRecord, ...]


def load_manifest(provider_dir: Path, *, today: date | None = None) -> FixtureManifest:
    """Read and validate ``<provider_dir>/manifest.json``."""
    provider = provider_dir.name
    try:
        text = (provider_dir / MANIFEST_NAME).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise FixtureSchemaError(provider, field=MANIFEST_NAME) from exc
    try:
        manifest = FixtureManifest.model_validate_json(text, strict=True)
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_url=False)[0]
        raise FixtureSchemaError(provider, field=_path(first["loc"])) from None
    if manifest.provider != provider:
        raise FixtureSchemaError(provider, field="provider")
    limit = today or date.today()
    seen: set[str] = set()
    for i, rec in enumerate(manifest.fixtures):
        where = f"fixtures[{i}]"
        if rec.file in seen:
            raise FixtureSchemaError(provider, field=f"{where}.file")
        seen.add(rec.file)
        is_json = rec.file.endswith(".json")
        # An outcome variant lives in a subdirectory (``no_match/search.json``) and
        # names its endpoint by file name, as the default file does.
        if rec.endpoint != (PurePosixPath(rec.file).stem if is_json else None):
            raise FixtureSchemaError(provider, field=f"{where}.endpoint")
        if rec.recorded_on > limit:
            raise FixtureSchemaError(provider, field=f"{where}.recorded_on")
        if (rec.schema_status is SchemaStatus.VERIFIED) != (
            rec.schema_verified_on is not None
        ):
            raise FixtureSchemaError(provider, field=f"{where}.schema_verified_on")
        if rec.schema_verified_on is not None and rec.schema_verified_on > limit:
            raise FixtureSchemaError(provider, field=f"{where}.schema_verified_on")
        if (rec.origin is FixtureOrigin.CAPTURED) != (rec.redaction is not None):
            raise FixtureSchemaError(provider, field=f"{where}.redaction")
    return manifest


def validate_fixture_tree(
    root: Path, declared: Mapping[str, Mapping[str, Endpoint]]
) -> dict[str, FixtureManifest]:
    """Check every provider directory under ``root`` against the declared endpoints.

    ``declared`` maps a provider name to its adapter's ``endpoints``. Fails if a
    provider has no directory, a directory has no provider, a declared endpoint has no
    record, a record names an undeclared endpoint, or a file and its record disagree.
    """
    directories = {p.name for p in root.iterdir() if p.is_dir()}
    for stray in sorted(directories - declared.keys()):
        raise FixtureSchemaError(stray, field="provider")
    manifests: dict[str, FixtureManifest] = {}
    for provider in sorted(declared):
        provider_dir = root / provider
        manifest = load_manifest(provider_dir)
        recorded_endpoints = {r.endpoint for r in manifest.fixtures if r.endpoint}
        for undeclared in sorted(recorded_endpoints - declared[provider].keys()):
            raise FixtureSchemaError(provider, field=f"endpoint {undeclared}")
        for missing in sorted(declared[provider].keys() - recorded_endpoints):
            raise FixtureSchemaError(provider, field=f"endpoint {missing}")
        recorded_files = {r.file for r in manifest.fixtures}
        # ``FixtureTransport`` serves only ``<endpoint>.json`` at the provider root, so
        # an outcome variant never stands in for the default fixture.
        for endpoint in sorted(declared[provider]):
            if f"{endpoint}.json" not in recorded_files:
                raise FixtureSchemaError(provider, field=f"file {endpoint}.json")
        on_disk = {
            p.relative_to(provider_dir).as_posix()
            for p in provider_dir.rglob("*")
            if p.is_file() and p.name != MANIFEST_NAME
        }
        for link in sorted(p for p in provider_dir.rglob("*") if p.is_symlink()):
            raise FixtureSchemaError(
                provider, field=f"file {link.relative_to(provider_dir).as_posix()}"
            )
        for orphan in sorted(on_disk ^ recorded_files):
            raise FixtureSchemaError(provider, field=f"file {orphan}")
        manifests[provider] = manifest
    return manifests


def _path(loc: tuple[int | str, ...]) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else f".{part}" if out else part
    return out or MANIFEST_NAME
