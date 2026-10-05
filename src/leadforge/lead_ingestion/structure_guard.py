"""Static guard: provider raw schemas stay private to the ingestion slice (1.1).

Convention: every provider raw Pydantic schema lives under `RAW_SCHEMA_PACKAGE`.
Modules outside the slice may import the slice's public types (e.g. `CanonicalLead`)
but never anything under that package.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

RAW_SCHEMA_PACKAGE = "leadforge.lead_ingestion.adapters"


@dataclass(frozen=True)
class RawSchemaImport:
    """One import statement outside the slice that reaches a provider raw schema."""

    path: Path
    """Absolute path of the offending `.py` file (outside the ingestion slice)."""

    lineno: int
    """1-based line number of the import statement within `path`."""

    module: str
    """Fully qualified module name that was imported, resolved to absolute form
    (relative imports included), e.g. `leadforge.lead_ingestion.adapters.apollo`.
    When one statement imports several raw-schema names, this is the first."""


def find_raw_schema_imports_outside_slice(
    src_root: Path, slice_root: Path
) -> list[RawSchemaImport]:
    """Return every import of `RAW_SCHEMA_PACKAGE` from a module outside the slice."""
    if not src_root.is_dir():
        raise NotADirectoryError(f"src root does not exist: {src_root}")
    src_root = src_root.resolve()
    slice_root = slice_root.resolve()
    violations: list[RawSchemaImport] = []
    for path in sorted(src_root.rglob("*.py")):
        if path.is_relative_to(slice_root):
            continue
        package = _package_of(path, src_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            raw = [m for m in _imported_modules(node, package) if _is_raw_schema(m)]
            if raw:  # one violation per import statement, not per imported name
                violations.append(RawSchemaImport(path, node.lineno, raw[0]))
    return violations


def _package_of(path: Path, src_root: Path) -> list[str]:
    """Dotted package parts containing `path`, relative to the src root."""
    return list(path.relative_to(src_root).parent.parts)


def _imported_modules(
    node: ast.Import | ast.ImportFrom, package: list[str]
) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    base = _absolute_base(node, package)
    names = [f"{base}.{alias.name}" for alias in node.names]
    return [base, *names] if base else names


def _absolute_base(node: ast.ImportFrom, package: list[str]) -> str:
    if node.level == 0:
        return node.module or ""
    anchor = package[: len(package) - node.level + 1]
    return ".".join([*anchor, *([node.module] if node.module else [])])


def _is_raw_schema(module: str) -> bool:
    return module == RAW_SCHEMA_PACKAGE or module.startswith(f"{RAW_SCHEMA_PACKAGE}.")
