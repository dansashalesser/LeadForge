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


# --------------------------------- orchestration depends on the contract only (2.4)

ORCHESTRATION_STEMS = ("orchestrator", "orchestration")
"""A module or package of one of these names, directly in the slice, is the layer."""

CONTRACT_BASE = "BaseLeadSource"


@dataclass(frozen=True)
class AdapterReference:
    """One place the orchestration layer names a concrete adapter."""

    path: Path
    """Absolute path of the offending orchestration module."""

    lineno: int
    """1-based line of the import or name that references the adapter."""

    name: str
    """The imported adapter module or the concrete adapter class name."""


def find_concrete_adapter_references(slice_root: Path) -> list[AdapterReference]:
    """Every import of the adapters package or use of a concrete adapter class name
    inside the orchestration layer. Absent orchestration modules yield no violations.
    """
    slice_root = slice_root.resolve()
    src_root = slice_root.parent.parent
    classes = _concrete_adapter_classes(slice_root / "adapters")
    violations: list[AdapterReference] = []
    for path in _orchestration_modules(slice_root):
        package = _package_of(path, src_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                hit = next(
                    (m for m in _imported_modules(node, package) if _is_raw_schema(m)),
                    None,
                )
                if hit is None:
                    hit = next(
                        (
                            a.name.rsplit(".", 1)[-1]
                            for a in node.names
                            if a.name.rsplit(".", 1)[-1] in classes
                        ),
                        None,
                    )
                if hit is not None:
                    violations.append(AdapterReference(path, node.lineno, hit))
            elif isinstance(node, ast.Call) and _dynamic_import_target(node):
                violations.append(
                    AdapterReference(path, node.lineno, _dynamic_import_target(node))
                )
            elif isinstance(node, ast.Name) and node.id in classes:
                violations.append(AdapterReference(path, node.lineno, node.id))
            elif isinstance(node, ast.Attribute) and node.attr in classes:
                violations.append(AdapterReference(path, node.lineno, node.attr))
    return violations


def _dynamic_import_target(call: ast.Call) -> str:
    """The adapters module named by `__import__("...")` or `import_module("...")`,
    or "" if this call is not one (a non-literal argument cannot be resolved)."""
    func = call.func
    called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if called not in ("__import__", "import_module") or not call.args:
        return ""
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value if _is_raw_schema(arg.value) else ""
    return ""


def _orchestration_modules(slice_root: Path) -> list[Path]:
    found: list[Path] = []
    for stem in ORCHESTRATION_STEMS:
        if (slice_root / f"{stem}.py").is_file():
            found.append(slice_root / f"{stem}.py")
        if (slice_root / stem).is_dir():
            found.extend(sorted((slice_root / stem).rglob("*.py")))
    return found


def _concrete_adapter_classes(adapters_root: Path) -> set[str]:
    """Names of classes under the adapters package deriving, transitively, from the
    contract base within that package."""
    if not adapters_root.is_dir():
        return set()
    bases: dict[str, set[str]] = {}
    for path in sorted(adapters_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(
                    b.id if isinstance(b, ast.Name) else b.attr
                    for b in node.bases
                    if isinstance(b, ast.Name | ast.Attribute)
                )
    concrete: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, parents in bases.items():
            if name not in concrete and (
                CONTRACT_BASE in parents or parents & concrete
            ):
                concrete.add(name)
                changed = True
    return concrete
