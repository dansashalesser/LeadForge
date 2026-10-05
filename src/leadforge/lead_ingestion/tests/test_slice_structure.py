"""Structural guards for the lead_ingestion vertical slice (Decision D1, Option B)."""

from pathlib import Path

import pytest

import leadforge.lead_ingestion as slice_pkg
from leadforge.lead_ingestion.structure_guard import (
    RAW_SCHEMA_PACKAGE,
    find_raw_schema_imports_outside_slice,
)

SRC_ROOT = Path(slice_pkg.__file__).resolve().parents[2]
SLICE_ROOT = Path(slice_pkg.__file__).resolve().parent
FORBIDDEN_SHARED_PACKAGES = ("models", "db", "utils", "common", "shared")


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_slice_package_is_importable_under_src_layout() -> None:
    assert slice_pkg.__name__ == "leadforge.lead_ingestion"
    assert SLICE_ROOT == SRC_ROOT / "leadforge" / "lead_ingestion"


# Verifies: specs/lead-source-adapters/requirements.md#1.1
@pytest.mark.parametrize("name", FORBIDDEN_SHARED_PACKAGES)
def test_no_shared_cross_feature_package_exists(name: str) -> None:
    assert not (SRC_ROOT / "leadforge" / name).exists()


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_no_module_outside_slice_imports_provider_raw_schema() -> None:
    assert find_raw_schema_imports_outside_slice(SRC_ROOT, SLICE_ROOT) == []


# Verifies: specs/lead-source-adapters/requirements.md#1.1
@pytest.mark.parametrize(
    "source",
    [
        f"import {RAW_SCHEMA_PACKAGE}.apollo\n",
        f"from {RAW_SCHEMA_PACKAGE} import apollo\n",
        f"from {RAW_SCHEMA_PACKAGE}.apollo.raw import PersonRaw\n",
        "from leadforge.lead_ingestion import adapters\n",
        "from ..lead_ingestion.adapters.apollo import raw\n",
        "from ..lead_ingestion import adapters\n",
    ],
)
def test_guard_flags_raw_schema_import_from_outside_slice(
    tmp_path: Path, source: str
) -> None:
    src_root, slice_root = _fake_tree(tmp_path)
    offender = src_root / "leadforge" / "scoring" / "score.py"
    offender.parent.mkdir(parents=True)
    offender.write_text(source)

    violations = find_raw_schema_imports_outside_slice(src_root, slice_root)

    assert [v.path for v in violations] == [offender]
    assert violations[0].lineno == 1


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_guard_allows_raw_schema_imports_inside_slice(tmp_path: Path) -> None:
    src_root, slice_root = _fake_tree(tmp_path)
    inside = slice_root / "normalize.py"
    inside.write_text(f"from {RAW_SCHEMA_PACKAGE}.apollo.raw import PersonRaw\n")

    assert find_raw_schema_imports_outside_slice(src_root, slice_root) == []


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_guard_allows_public_slice_imports_from_outside(tmp_path: Path) -> None:
    src_root, slice_root = _fake_tree(tmp_path)
    outside = src_root / "leadforge" / "scoring" / "score.py"
    outside.parent.mkdir(parents=True)
    outside.write_text("from leadforge.lead_ingestion.types import CanonicalLead\n")

    assert find_raw_schema_imports_outside_slice(src_root, slice_root) == []


def _fake_tree(tmp_path: Path) -> tuple[Path, Path]:
    src_root = tmp_path / "src"
    slice_root = src_root / "leadforge" / "lead_ingestion"
    slice_root.mkdir(parents=True)
    return src_root, slice_root
