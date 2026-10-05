"""Structural guards for the lead_ingestion vertical slice (Decision D1, Option B)."""

from pathlib import Path

import pytest

import leadforge.lead_ingestion as slice_pkg
from leadforge.lead_ingestion.structure_guard import (
    RAW_SCHEMA_PACKAGE,
    find_concrete_adapter_references,
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


# ------------------------------------- orchestration stays contract-only (task 3.4)


def _slice_with_adapter(tmp_path: Path) -> tuple[Path, Path]:
    _, slice_root = _fake_tree(tmp_path)
    adapters = slice_root / "adapters"
    adapters.mkdir()
    (adapters / "__init__.py").write_text("")
    (adapters / "hunter.py").write_text(
        "from leadforge.lead_ingestion.base_source import BaseLeadSource\n"
        "class HunterSource(BaseLeadSource): ...\n"
        "class HunterSubSource(HunterSource): ...\n"
    )
    return slice_root, slice_root / "orchestrator.py"


# Verifies: specs/lead-source-adapters/requirements.md#2.4
def test_orchestration_layer_references_no_concrete_adapter() -> None:
    assert find_concrete_adapter_references(SLICE_ROOT) == []


# Verifies: specs/lead-source-adapters/requirements.md#2.4
@pytest.mark.parametrize(
    "source",
    [
        "import leadforge.lead_ingestion.adapters.hunter\n",
        "from leadforge.lead_ingestion.adapters import hunter\n",
        "from leadforge.lead_ingestion.adapters.hunter import HunterSource\n",
        "from .adapters.hunter import HunterSource as H\n",
        "from .adapters import hunter\n",
        "x = HunterSource\n",
        "x = HunterSubSource\n",
        "import m\nx = m.HunterSource\n",
        "import importlib as i\ni.import_module('leadforge.lead_ingestion.adapters')\n",
        "__import__('leadforge.lead_ingestion.adapters')\n",
    ],
)
def test_guard_flags_concrete_adapter_reference_in_orchestration(
    tmp_path: Path, source: str
) -> None:
    slice_root, orchestrator = _slice_with_adapter(tmp_path)
    orchestrator.write_text(source)

    violations = find_concrete_adapter_references(slice_root)

    assert [v.path for v in violations] == [orchestrator]


# Verifies: specs/lead-source-adapters/requirements.md#2.4
def test_guard_scans_an_orchestration_package_too(tmp_path: Path) -> None:
    slice_root, _ = _slice_with_adapter(tmp_path)
    package = slice_root / "orchestrator"
    package.mkdir()
    offender = package / "run.py"
    offender.write_text("from ..adapters import hunter\n")

    assert [v.path for v in find_concrete_adapter_references(slice_root)] == [offender]


# Verifies: specs/lead-source-adapters/requirements.md#2.4
def test_guard_allows_orchestration_that_uses_only_the_contract(
    tmp_path: Path,
) -> None:
    slice_root, orchestrator = _slice_with_adapter(tmp_path)
    orchestrator.write_text(
        "from leadforge.lead_ingestion.base_source import BaseLeadSource\n"
        "def run(sources: list[BaseLeadSource]) -> None: ...\n"
    )

    assert find_concrete_adapter_references(slice_root) == []


# Verifies: specs/lead-source-adapters/requirements.md#2.4
def test_guard_ignores_modules_that_are_not_orchestration(tmp_path: Path) -> None:
    slice_root, _ = _slice_with_adapter(tmp_path)
    (slice_root / "registry.py").write_text(
        "from .adapters.hunter import HunterSource\n"
    )

    assert find_concrete_adapter_references(slice_root) == []
