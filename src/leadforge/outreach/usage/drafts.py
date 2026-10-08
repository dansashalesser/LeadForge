"""Catalog drafts for unknown vendors (Req 2.5).

The model drafts an entry from the vendor name and the provider's
supported-technologies list; the draft waits in ``<catalog>/drafts/`` and no search may
use it until a person approves it. The model's answer is untrusted: it is validated
against the catalog schema, and every technology UID must come from the supplied list,
before anything is written.
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from leadforge.lead_ingestion.catalog import (
    DRAFTS_DIR,
    CatalogError,
    UnknownTechnologyUidError,
    VendorDocument,
    default_catalog_dir,
    parse_vendor_file,
    unknown_technology_uids,
)
from leadforge.outreach.llm import ModelInvoker
from leadforge.outreach.prompts import delimit, load_prompt

__all__ = [
    "DraftError",
    "DraftExistsError",
    "DraftInvalidError",
    "DraftNotFoundError",
    "approve_draft",
    "draft_vendor",
]

PROMPT_VERSION = "catalog_draft_v1"
_KEY = re.compile(r"[a-z][a-z0-9_]*")


class DraftError(CatalogError):
    """Root of draft failures."""


class DraftInvalidError(DraftError):
    """The key is unsafe or the model never produced a valid, matching entry."""


class DraftExistsError(DraftError):
    """Writing or approving would overwrite an approved vendor file."""


class DraftNotFoundError(DraftError):
    """There is no draft with that key."""


def _check_key(key: str) -> None:
    if not _KEY.fullmatch(key):
        raise DraftInvalidError(f"not a vendor key: {key!r}")


def draft_vendor(
    model: ModelInvoker,
    *,
    key: str,
    name: str,
    listed_technologies: Mapping[str, object],
    catalog_dir: Path | None = None,
    retries: int = 2,
) -> Path:
    """Draft ``key``'s entry into ``drafts/<key>.yaml`` and return the path.

    Raises ``DraftExistsError`` for a vendor already approved,
    ``UnknownTechnologyUidError`` for a UID the provider does not list, and
    ``DraftInvalidError`` if no attempt yields a schema-valid entry for ``key``.
    """
    _check_key(key)
    root = default_catalog_dir() if catalog_dir is None else catalog_dir
    if (root / f"{key}.yaml").exists():
        raise DraftExistsError(f"vendor {key} is already in the catalog")
    messages = (
        ("system", load_prompt(PROMPT_VERSION).text),
        (
            "human",
            "\n\n".join(
                [
                    delimit("vendor", f"key: {key}\nname: {name}"),
                    delimit(
                        "supported_technologies",
                        json.dumps(sorted(listed_technologies)),
                    ),
                ]
            ),
        ),
    )
    document: VendorDocument | None = None
    for _ in range(retries + 1):
        answer = model.invoke(messages)
        try:
            document = VendorDocument.model_validate(
                answer.model_dump() if isinstance(answer, BaseModel) else answer
            )
        except ValidationError:
            continue
        break
    if document is None or document.vendor.key != key:
        raise DraftInvalidError(f"the model gave no valid entry for {key}")
    used = frozenset(
        uid
        for entry in (*document.products, *document.ecosystem)
        for uid in entry.technology_uids
    )
    absent = unknown_technology_uids(listed_technologies, used)
    if absent:
        raise UnknownTechnologyUidError(absent)
    target = root / DRAFTS_DIR / f"{key}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(document.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    return target


def approve_draft(key: str, *, catalog_dir: Path | None = None) -> Path:
    """Validate ``drafts/<key>.yaml`` again and move it to ``<key>.yaml``."""
    _check_key(key)
    root = default_catalog_dir() if catalog_dir is None else catalog_dir
    draft = root / DRAFTS_DIR / f"{key}.yaml"
    if not draft.is_file():
        raise DraftNotFoundError(f"no draft for vendor {key}")
    target = root / f"{key}.yaml"
    if target.exists():
        raise DraftExistsError(f"vendor {key} is already in the catalog")
    if parse_vendor_file(draft).key != key:
        raise DraftInvalidError(f"draft {key} names a different vendor key")
    draft.rename(target)
    return target
