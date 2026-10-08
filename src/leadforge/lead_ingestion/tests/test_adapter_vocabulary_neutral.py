"""Adapter code names no catalog vendor or product (vocabulary is the profile)."""

from pathlib import Path

import pytest

from leadforge.lead_ingestion.catalog import load_catalog

_ADAPTERS = Path(__file__).resolve().parents[1] / "adapters"
_BASE = _ADAPTERS.parent / "base_source.py"


def _catalog_terms() -> set[str]:
    terms: set[str] = set()
    for vendor in load_catalog().vendors():
        terms.update({vendor.key, vendor.name, *vendor.domains})
        for entry in (*vendor.products, *vendor.ecosystem):
            terms.update({entry.key, entry.name, *entry.technology_uids})
            terms.update(alias.text for alias in entry.aliases)
    return {t.casefold() for t in terms if t.strip()}


# Verifies: specs/user-recognition/requirements.md#2.7
@pytest.mark.parametrize(
    "path", sorted([*_ADAPTERS.glob("*.py"), _BASE]), ids=lambda p: p.name
)
def test_adapter_source_names_no_catalog_term(path: Path) -> None:
    source = path.read_text(encoding="utf-8").casefold()
    found = sorted(t for t in _catalog_terms() if t in source)
    assert not found, f"{path.name} names catalog terms {found}"
